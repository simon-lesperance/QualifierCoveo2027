"""Teach the policy network to copy MCTS, from sim_policygen data.

Saves a checkpoint train_ppo.py can continue from with --resume-net, and plays
fresh games to show how the copy does with no search at all.

    python tools/train_policy.py runs/data/policy0 --out runs/policy/first
"""

import argparse
import time
from argparse import Namespace
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from train_ppo import ACTIONS, FEATURE_BYTES, PolicyNet, build_inputs, evaluate

MAX_CHOICES = 128
GAMMA = 0.995
REWARD_SCALE = 100


def discounted(score: np.ndarray, gamma: float) -> np.ndarray:
    """Discounted points from each tick to the end, indexed by the tick about to be played."""
    points = np.diff(score.astype(np.float64), prepend=0)
    returns = np.zeros(len(score) + 1)
    for tick in range(len(score) - 1, 0, -1):
        returns[tick] = points[tick] + gamma * returns[tick + 1]
    return returns


def load(folders: list[Path]):
    features, ticks, codes, visits, targets, seeds = [], [], [], [], [], []
    for index, path in enumerate(p for folder in folders for p in sorted(folder.glob("*.bin"))):
        data = path.read_bytes()
        score = np.frombuffer(data, dtype=np.int32, count=1001)
        returns = discounted(score, GAMMA)
        count = int(np.frombuffer(data, dtype=np.int32, count=1, offset=1001 * 4)[0])
        offset = 1002 * 4
        for _ in range(count):
            tick = int(np.frombuffer(data, dtype=np.int32, count=1, offset=offset)[0])
            feature = np.frombuffer(data, dtype=np.uint8, count=FEATURE_BYTES, offset=offset + 4)
            offset += 4 + FEATURE_BYTES
            size = int(np.frombuffer(data, dtype=np.int32, count=1, offset=offset)[0])
            pairs = np.frombuffer(data, dtype=np.int32, count=2 * size, offset=offset + 4).reshape(size, 2)
            offset += 4 + 8 * size
            top = pairs[np.argsort(-pairs[:, 1])[:MAX_CHOICES]]
            code = np.zeros(MAX_CHOICES, dtype=np.int16)
            visit = np.zeros(MAX_CHOICES, dtype=np.float32)
            code[:len(top)] = top[:, 0]
            visit[:len(top)] = top[:, 1] / max(1, top[:, 1].sum())
            features.append(feature)
            ticks.append(tick)
            codes.append(code)
            visits.append(visit)
            targets.append(returns[tick])
            seeds.append(index)
    return (np.stack(features), np.array(ticks, dtype=np.int32), np.stack(codes), np.stack(visits),
            np.array(targets, dtype=np.float32) / REWARD_SCALE, np.array(seeds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channels", type=int, default=48)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--eval-games", type=int, default=128)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda")

    start = time.perf_counter()
    features, ticks, codes, visits, targets, seeds = load(args.data)
    print(f"{len(features):,} samples, {time.perf_counter() - start:.0f}s", flush=True)
    scale = float(targets.std())
    valid = seeds % 10 == 0
    train_index = np.flatnonzero(~valid)
    valid_index = np.flatnonzero(valid)

    net = PolicyNet(args.channels).to(device)
    optimizer = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=1e-5)
    steps = args.epochs * (len(train_index) // args.batch)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, args.lr, total_steps=steps)
    scaler = torch.amp.GradScaler("cuda")

    def batch_loss(index: np.ndarray) -> tuple[torch.Tensor, torch.Tensor]:
        feature = torch.from_numpy(features[index]).to(device)
        tick = torch.from_numpy(ticks[index]).to(device)
        code = torch.from_numpy(codes[index].astype(np.int64)).to(device)
        visit = torch.from_numpy(visits[index]).to(device)
        target = torch.from_numpy(targets[index]).to(device)
        planes, mask = build_inputs(feature, tick)
        with torch.amp.autocast("cuda"):
            logits, value = net(planes, mask)
        log_probs = F.log_softmax(logits.float(), dim=1)
        policy = -(log_probs.gather(1, code) * visit).sum(dim=1).mean()
        return policy, F.mse_loss(value.float(), target / scale)

    def validate() -> tuple[float, float]:
        policy_sum = value_sum = 0.0
        batches = 0
        with torch.no_grad():
            for first in range(0, len(valid_index), 4096):
                policy, value = batch_loss(valid_index[first:first + 4096])
                policy_sum += policy.item()
                value_sum += value.item()
                batches += 1
        return policy_sum / batches, value_sum / batches

    for epoch in range(args.epochs):
        net.train()
        order = np.random.permutation(train_index)
        policy_sum = value_sum = 0.0
        batches = len(order) // args.batch
        for b in range(batches):
            policy, value = batch_loss(order[b * args.batch:(b + 1) * args.batch])
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(policy + 0.5 * value).backward()
            scaler.step(optimizer)
            scaler.update()
            schedule.step()
            policy_sum += policy.detach().item()
            value_sum += value.detach().item()
        net.eval()
        valid_policy, valid_value = validate()
        print(f"epoch {epoch + 1}  policy {policy_sum / batches:.3f}  value {value_sum / batches:.3f}  "
              f"valid {valid_policy:.3f} {valid_value:.3f}  {time.perf_counter() - start:.0f}s", flush=True)
        torch.save({"net": net.state_dict(), "channels": args.channels, "scale": scale}, args.out / "policy.pt")

    results = evaluate(net, Namespace(eval_games=args.eval_games, threads=args.threads, biomes=7), device, "bc")
    print(results, flush=True)


if __name__ == "__main__":
    main()
