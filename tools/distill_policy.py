"""Teach a small policy network to rank moves like a big one, cheaply enough for use inside the tree.

The big net plays games with sampled moves, and the small net copies its move
probabilities on those positions. Saves after every report, so it can be stopped.

    python tools/distill_policy.py runs/ppo/dag1/best.pt --out runs/policy/small16 --channels 16
"""

import argparse
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

import torch.nn as nn

from train_ppo import FEATURE_BYTES, PLANES, PolicyNet, VecEnv, build_inputs, export, sample

DILATIONS = (1, 1, 2, 2, 4, 1)


class DepthwiseBlock(nn.Module):
    def __init__(self, channels: int, dilation: int):
        super().__init__()
        self.spatial = nn.Conv2d(channels, channels, 3, padding=dilation, dilation=dilation, groups=channels)
        self.mix = nn.Conv2d(channels, channels, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + F.relu(self.mix(F.relu(self.spatial(x))))


class DepthwiseNet(nn.Module):
    """PolicyNet with each 3x3 convolution split into one filter per channel and a 1x1 mix."""

    def __init__(self, channels: int, hidden: int):
        super().__init__()
        self.head = nn.Conv2d(PLANES, channels, 1)
        self.blocks = nn.ModuleList(DepthwiseBlock(channels, d) for d in DILATIONS)
        self.tiles = nn.Conv2d(channels, 2, 1)
        self.fc = nn.Linear(2 * channels, hidden)
        self.none = nn.Linear(hidden, 1)

    def forward(self, planes: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = F.relu(self.head(planes))
        for block in self.blocks:
            x = block(x)
        tiles = self.tiles(x)
        pooled = torch.cat([x.mean(dim=(2, 3)), x.amax(dim=(2, 3))], dim=1)
        none = self.none(F.relu(self.fc(pooled)))
        logits = torch.cat([none, tiles[:, 0].flatten(1), tiles[:, 1].flatten(1)], dim=1)
        return logits.masked_fill(~mask, -1e4), none.squeeze(1)


def export_depthwise(net: DepthwiseNet, path: Path) -> None:
    """The layout of sim/policy_net.cpp. A negative hidden size marks a depthwise net."""
    layers = [net.head]
    for block in net.blocks:
        layers += [block.spatial, block.mix]
    layers += [net.tiles, net.fc, net.none]
    with open(path, "wb") as file:
        np.array([net.head.out_channels, -net.fc.out_features], dtype=np.int32).tofile(file)
        for layer in layers:
            for parameter in (layer.weight, layer.bias):
                parameter.detach().cpu().numpy().astype(np.float32).tofile(file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("teacher", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channels", type=int, default=16)
    parser.add_argument("--hidden", type=int, default=32)
    parser.add_argument("--depthwise", action="store_true")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--envs", type=int, default=2048)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--buffer", type=int, default=400_000)
    parser.add_argument("--batch", type=int, default=2048)
    parser.add_argument("--updates", type=int, default=2, help="Training steps per env step")
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--temperature", type=float, default=2.0)
    parser.add_argument("--report-seconds", type=float, default=120)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    device = torch.device(args.device)
    use_amp = args.device == "cuda"
    if args.device == "cpu":
        torch.set_num_threads(args.threads)
    state = torch.load(args.teacher, map_location=device)
    teacher = PolicyNet(state["channels"]).to(device).eval()
    teacher.load_state_dict(state["net"])
    net = (DepthwiseNet if args.depthwise else PolicyNet)(args.channels, args.hidden).to(device)
    save_net = export_depthwise if args.depthwise else export
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)
    total_seconds = args.minutes * 60
    print(f"{sum(p.numel() for p in net.parameters()):,} parameters", flush=True)

    env = VecEnv(args.envs, args.threads, 7, f"distill-{args.out.name}", True)
    features = torch.zeros(args.buffer, FEATURE_BYTES, dtype=torch.uint8, device=device)
    ticks = torch.zeros(args.buffer, dtype=torch.int32, device=device)
    used = position = updates = 0
    totals = torch.zeros(3, device=device)
    seen = 0
    start = last_report = time.perf_counter()

    while (now := time.perf_counter()) - start < total_seconds:
        now_features = torch.from_numpy(env.features).to(device)
        now_ticks = torch.from_numpy(env.ticks).to(device)
        with torch.no_grad(), torch.amp.autocast("cuda", enabled=use_amp):
            logits, _ = teacher(*build_inputs(now_features, now_ticks))
        env.step(sample(logits).cpu().numpy())
        slots = (torch.arange(args.envs, device=device) + position) % args.buffer
        features[slots] = now_features
        ticks[slots] = now_ticks
        position = (position + args.envs) % args.buffer
        used = min(args.buffer, used + args.envs)
        if used < 200 * args.envs:
            continue

        progress = (now - start) / total_seconds
        for group in optimizer.param_groups:
            group["lr"] = args.lr * min(1.0, 1.05 - progress)
        for _ in range(args.updates):
            index = torch.randint(used, (args.batch,), device=device)
            planes, mask = build_inputs(features[index], ticks[index])
            with torch.no_grad(), torch.amp.autocast("cuda", enabled=use_amp):
                target, _ = teacher(planes, mask)
            logits, _ = net(planes, mask)  # Half precision overflows while the small net is young.
            target = target.float().masked_fill(~mask, -1e4)
            target_log = F.log_softmax(target / args.temperature, dim=1)
            log_probs = F.log_softmax(logits / args.temperature, dim=1)
            loss = (target_log.exp() * (target_log - log_probs)).where(mask, 0.0).sum(dim=1).mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            optimizer.step()
            with torch.no_grad():
                best = target.argmax(dim=1)
                top5 = logits.topk(5, dim=1).indices
                totals += torch.stack([
                    loss.detach(),
                    (logits.argmax(dim=1) == best).float().mean(),
                    (top5 == best.unsqueeze(1)).any(dim=1).float().mean(),
                ])
            seen += 1
            updates += 1

        if now - last_report >= args.report_seconds:
            values = (totals / max(1, seen)).tolist()
            print(f"{(now - start) / 60:5.1f} min  updates {updates}  kl {values[0]:.3f}  "
                  f"top1 {values[1]:.3f}  top5 {values[2]:.3f}", flush=True)
            totals[:] = 0
            seen = 0
            last_report = now
            torch.save({"net": net.state_dict(), "channels": args.channels, "hidden": args.hidden}, args.out / "small.pt")
            save_net(net, args.out / "policy_net.bin")

    torch.save({"net": net.state_dict(), "channels": args.channels, "hidden": args.hidden}, args.out / "small.pt")
    save_net(net, args.out / "policy_net.bin")
    env.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
