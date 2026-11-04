"""Train the value network on games from sim_datagen.

It predicts the points scored over the next --window ticks, in thousands. Games are
split by seed, so validation positions come from games the net never saw.

    python tools/train_value.py runs/data/gen0 --out runs/nets/gen0
"""

import argparse
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

CHANNELS = 14
GLOBALS = 5
CELLS = 400
FEATURE_BYTES = CHANNELS * CELLS + GLOBALS
TICKS = 1001
RECORD = np.dtype([("tick", "<i4"), ("features", "u1", FEATURE_BYTES)])


class ValueNet(nn.Module):
    """Small enough to evaluate in about the time of one MCTS iteration."""

    def __init__(self, first: int = 12, second: int = 16, hidden: int = 32):
        super().__init__()
        self.conv1 = nn.Conv2d(CHANNELS, first, 3, stride=2, padding=1)
        self.conv2 = nn.Conv2d(first, second, 3, stride=2, padding=1)
        self.fc1 = nn.Linear(second * 5 * 5 + GLOBALS, hidden)
        self.fc2 = nn.Linear(hidden, 1)
        self.relu = nn.ReLU()

    def forward(self, grid: torch.Tensor, globals_: torch.Tensor) -> torch.Tensor:
        x = self.relu(self.conv1(grid))
        x = self.relu(self.conv2(x))
        x = torch.cat([x.flatten(1), globals_], dim=1)
        return self.fc2(self.relu(self.fc1(x))).squeeze(1)


def load_games(folder: Path, window: int, group_size: int, group_bonus: float):
    features, targets, seeds, ticks = [], [], [], []
    for index, path in enumerate(sorted(folder.glob("*.bin"))):
        raw = path.read_bytes()
        score = np.frombuffer(raw, "<i4", TICKS)
        kills = np.frombuffer(raw, "<i4", TICKS, TICKS * 4)
        count = int(np.frombuffer(raw, "<i4", 1, TICKS * 8)[0])
        records = np.frombuffer(raw, RECORD, count, TICKS * 8 + 4)

        extra = np.where(kills >= group_size, kills * group_bonus, 0.0).cumsum()
        keep = records["tick"] + window - 1 <= 1000
        tick = records["tick"][keep]
        points = score[tick + window - 1] - score[tick - 1] + extra[tick + window - 1] - extra[tick - 1]
        features.append(records["features"][keep])
        targets.append(points / 1000.0)
        seeds.append(np.full(len(tick), index))
        ticks.append(tick)
    return np.concatenate(features), np.concatenate(targets).astype(np.float32), np.concatenate(seeds), np.concatenate(ticks)


def split_inputs(batch: np.ndarray, device: str):
    data = torch.from_numpy(batch).to(device).float() / 255.0
    grid = data[:, : CHANNELS * CELLS].view(-1, CHANNELS, 20, 20)
    return grid, data[:, CHANNELS * CELLS :]


def evaluate(model: nn.Module, features: np.ndarray, targets: np.ndarray, device: str):
    predictions = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(features), 4096):
            predictions.append(model(*split_inputs(features[start : start + 4096], device)).cpu().numpy())
    predicted = np.concatenate(predictions)
    mse = float(np.mean((predicted - targets) ** 2))
    return mse, 1 - mse / float(np.var(targets))


def export(model: ValueNet, path: Path) -> None:
    """Writes the layer sizes, then every parameter as little-endian float32, in declaration order."""
    with path.open("wb") as file:
        sizes = [model.conv1.out_channels, model.conv2.out_channels, model.fc1.out_features]
        file.write(np.array(sizes, "<i4").tobytes())
        for parameter in model.state_dict().values():
            file.write(parameter.cpu().numpy().astype("<f4").tobytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("--out", type=Path, default=Path("runs/nets/latest"))
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--first", type=int, default=12)
    parser.add_argument("--second", type=int, default=16)
    parser.add_argument("--hidden", type=int, default=32)
    parser.add_argument("--group-size", type=int, default=4, help="kills in a tick that earn the bonus")
    parser.add_argument("--group-bonus", type=float, default=0.0, help="extra points per kill in such a tick")
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    features, targets, seeds, ticks = load_games(args.data, args.window, args.group_size, args.group_bonus)
    is_validation = seeds % 10 == 0
    train_x, train_y = features[~is_validation], targets[~is_validation]
    valid_x, valid_y = features[is_validation], targets[is_validation]
    print(f"{len(train_x)} training positions, {len(valid_x)} validation, device {device}")
    print(f"target mean {targets.mean():.3f}, sd {targets.std():.3f} (thousands of points)")

    # A reference: how well the tick alone predicts the target.
    fit = np.polyfit(ticks[~is_validation], train_y, 2)
    baseline = 1 - np.mean((np.polyval(fit, ticks[is_validation]) - valid_y) ** 2) / np.var(valid_y)
    print(f"baseline R2 from tick alone: {baseline:.3f}")

    model = ValueNet(args.first, args.second, args.hidden).to(device)
    parameters = sum(p.numel() for p in model.parameters())
    print(f"{parameters} parameters")
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs)
    target_tensor = torch.from_numpy(train_y)

    best_r2 = -np.inf
    args.out.mkdir(parents=True, exist_ok=True)
    for epoch in range(args.epochs):
        start = time.time()
        model.train()
        order = np.random.permutation(len(train_x))
        total = 0.0
        for begin in range(0, len(order), args.batch):
            indices = order[begin : begin + args.batch]
            grid, globals_ = split_inputs(train_x[indices], device)
            loss = nn.functional.mse_loss(model(grid, globals_), target_tensor[indices].to(device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss) * len(indices)
        scheduler.step()
        mse, r2 = evaluate(model, valid_x, valid_y, device)
        print(f"epoch {epoch + 1:3}  train {total / len(order):.4f}  valid {mse:.4f}  R2 {r2:.3f}  {time.time() - start:.1f}s", flush=True)
        if r2 > best_r2:
            best_r2 = r2
            torch.save(model.state_dict(), args.out / "model.pt")
            export(model, args.out / "value_net.bin")

    # Reference outputs, so sim_nettest can check the C++ inference.
    model.load_state_dict(torch.load(args.out / "model.pt", weights_only=True))
    model.eval()
    sample = valid_x[:: max(1, len(valid_x) // 256)][:256].copy()
    with torch.no_grad():
        outputs = model(*split_inputs(sample, device)).cpu().numpy().astype("<f4")
    with (args.out / "check.bin").open("wb") as file:
        file.write(np.array([len(sample)], "<i4").tobytes())
        for row, output in zip(sample, outputs):
            file.write(row.tobytes())
            file.write(output.tobytes())

    print(f"best validation R2 {best_r2:.3f}, saved to {args.out}")


if __name__ == "__main__":
    main()
