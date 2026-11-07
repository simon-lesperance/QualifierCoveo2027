"""Teach a small policy network to rank moves like a big one, cheaply enough for use inside the tree.

The big net plays games with sampled moves, and the small net copies its move
probabilities on those positions. Saves after every report, so it can be stopped.

    python tools/distill_policy.py runs/ppo/dag1/best.pt --out runs/policy/small16 --channels 16
"""

import argparse
import time
from pathlib import Path

import torch
import torch.nn.functional as F

from train_ppo import FEATURE_BYTES, PolicyNet, VecEnv, build_inputs, export, sample


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("teacher", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channels", type=int, default=16)
    parser.add_argument("--hidden", type=int, default=32)
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

    device = torch.device("cuda")
    state = torch.load(args.teacher, map_location=device)
    teacher = PolicyNet(state["channels"]).to(device).eval()
    teacher.load_state_dict(state["net"])
    net = PolicyNet(args.channels, args.hidden).to(device)
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
        with torch.no_grad(), torch.amp.autocast("cuda"):
            logits, _ = teacher(*build_inputs(now_features, now_ticks))
        env.step(sample(logits).cpu().numpy())
        slots = (torch.arange(args.envs, device=device) + position) % args.buffer
        features[slots] = now_features
        ticks[slots] = now_ticks
        position = (position + args.envs) % args.buffer
        used = min(args.buffer, used + args.envs)
        if used < 50 * args.envs:
            continue

        progress = (now - start) / total_seconds
        for group in optimizer.param_groups:
            group["lr"] = args.lr * min(1.0, 1.05 - progress)
        for _ in range(args.updates):
            index = torch.randint(used, (args.batch,), device=device)
            planes, mask = build_inputs(features[index], ticks[index])
            with torch.no_grad(), torch.amp.autocast("cuda"):
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
            export(net, args.out / "policy_net.bin")

    torch.save({"net": net.state_dict(), "channels": args.channels, "hidden": args.hidden}, args.out / "small.pt")
    export(net, args.out / "policy_net.bin")
    env.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
