"""Train a value net on bootstrapped targets: real points over the next --boot ticks, plus a
teacher net's estimate of the position reached then.

The usual target, points over the next 100 ticks, is mostly luck at spawn (sd 3.2k for v47 games).
Replacing everything past --boot ticks with the teacher's estimate removes most of that noise.
The teacher predicts 100 ticks, so its estimate is scaled by (100 - boot) / 100.

    python tools/train_value_boot.py runs/data/v47a --teacher runs/nets/gen2/model.pt --boot 20 --out runs/nets/b20
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_value import RECORD, TICKS, ValueNet, evaluate, export, split_inputs  # noqa: E402


def predict(model: nn.Module, features: np.ndarray, device: str) -> np.ndarray:
    out = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(features), 8192):
            out.append(model(*split_inputs(features[start : start + 8192], device)).cpu().numpy())
    return np.concatenate(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path, nargs="+")
    parser.add_argument("--teacher", type=Path, required=True)
    parser.add_argument("--boot", type=int, default=20, help="ticks of real points before the teacher takes over")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--init", action="store_true", help="start from the teacher's weights")
    args = parser.parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    teacher = ValueNet().to(device)
    teacher.load_state_dict(torch.load(args.teacher, weights_only=True))

    features, targets, seeds, mc = [], [], [], []
    paths = [path for folder in args.data for path in sorted(folder.glob("*.bin"))]
    for index, path in enumerate(paths):
        raw = path.read_bytes()
        score = np.frombuffer(raw, "<i4", TICKS)
        count = int(np.frombuffer(raw, "<i4", 1, TICKS * 8)[0])
        records = np.frombuffer(raw, RECORD, count, TICKS * 8 + 4)
        tick = records["tick"]
        position = {int(t): i for i, t in enumerate(tick)}
        later = np.array([position.get(int(t) + args.boot, -1) for t in tick])
        keep = (later >= 0) & (tick + 99 <= 1000)
        value = predict(teacher, records["features"][later[keep]], device)
        t = tick[keep]
        points = (score[t + args.boot - 1] - score[t - 1]) / 1000.0
        targets.append(points + value * (100 - args.boot) / 100.0)
        mc.append((score[t + 99] - score[t - 1]) / 1000.0)
        features.append(records["features"][keep])
        seeds.append(np.full(len(t), index))
    features = np.concatenate(features)
    targets = np.concatenate(targets).astype(np.float32)
    mc = np.concatenate(mc).astype(np.float32)
    seeds = np.concatenate(seeds)

    valid = seeds % 10 == 0
    print(f"{(~valid).sum()} training positions, {valid.sum()} validation, device {device}")
    print(f"target mean {targets.mean():.3f}, sd {targets.std():.3f}; 100-tick return mean {mc.mean():.3f}, sd {mc.std():.3f}")
    t_mc = predict(teacher, features[valid], device)
    print(f"teacher R2 on 100-tick returns {1 - np.mean((t_mc - mc[valid]) ** 2) / np.var(mc[valid]):.3f}")

    model = ValueNet().to(device)
    if args.init:
        model.load_state_dict(teacher.state_dict())
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs)
    train_x, train_y = features[~valid], torch.from_numpy(targets[~valid])
    best = np.inf
    args.out.mkdir(parents=True, exist_ok=True)
    for epoch in range(args.epochs):
        start = time.time()
        model.train()
        order = np.random.permutation(len(train_x))
        for begin in range(0, len(order), args.batch):
            idx = order[begin : begin + args.batch]
            loss = nn.functional.mse_loss(model(*split_inputs(train_x[idx], device)), train_y[idx].to(device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        scheduler.step()
        mse, r2 = evaluate(model, features[valid], targets[valid], device)
        _, r2_mc = evaluate(model, features[valid], mc[valid], device)
        print(f"epoch {epoch + 1:3}  valid {mse:.4f}  R2 {r2:.3f}  R2 on 100-tick returns {r2_mc:.3f}  {time.time() - start:.1f}s", flush=True)
        if mse < best:
            best = mse
            torch.save(model.state_dict(), args.out / "model.pt")
            export(model, args.out / "value_net.bin")
    print(f"saved to {args.out}")


if __name__ == "__main__":
    main()
