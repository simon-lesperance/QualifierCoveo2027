"""Train a policy and value network by PPO on the C++ simulator.

The network sees only what a live bot sees, and picks one action a tick: nothing,
a meteor on a tile, or a volcano on a mountain tile. Rewards are game points, so
it can learn to hold fire when waiting pays off, which a 4-tick search can't see.

    python tools/train_ppo.py --out runs/ppo/first
"""

import argparse
import ctypes
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
SIZE = 20
CELLS = SIZE * SIZE
CHANNELS = 14
GLOBALS = 5
BASE_BYTES = CHANNELS * CELLS + GLOBALS
FEATURE_BYTES = BASE_BYTES + CELLS + 2
ACTIONS = 1 + 2 * CELLS
MOUNTAIN_CHANNEL = 2
STEGO_ROW, STEGO_CYCLE = 13, 38
PLANES = CHANNELS + 1 + 2 + GLOBALS + 4 + 4


class VecEnv:
    def __init__(self, envs: int, threads: int, biomes: int, prefix: str, desync: bool):
        self.lib = ctypes.CDLL(str(ROOT / "build" / ("libvec.dll" if sys.platform == "win32" else "libvec.so")))
        self.lib.vec_create.restype = ctypes.c_void_p
        self.lib.vec_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.lib.vec_observe.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 2
        self.lib.vec_step.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 7
        self.lib.vec_destroy.argtypes = [ctypes.c_void_p]
        self.lib.vec_set_plans.restype = ctypes.c_int
        self.lib.vec_set_plans.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
        self.handle = self.lib.vec_create(envs, threads, biomes, prefix.encode(), int(desync))
        self.features = np.zeros((envs, FEATURE_BYTES), dtype=np.uint8)
        self.ticks = np.zeros(envs, dtype=np.int32)
        self.rewards = np.zeros(envs, dtype=np.float32)
        self.kills = np.zeros(envs, dtype=np.int32)
        self.scores = np.zeros(envs, dtype=np.int32)
        self.games = np.zeros(envs, dtype=np.int64)
        self.lib.vec_observe(self.handle, self.features.ctypes.data, self.ticks.ctypes.data)

    def step(self, actions: np.ndarray) -> None:
        actions = actions.astype(np.int32)
        self.lib.vec_step(
            self.handle, actions.ctypes.data, self.features.ctypes.data, self.ticks.ctypes.data,
            self.rewards.ctypes.data, self.kills.ctypes.data, self.scores.ctypes.data, self.games.ctypes.data,
        )

    def close(self) -> None:
        self.lib.vec_destroy(self.handle)


def build_inputs(features: torch.Tensor, ticks: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Turns raw feature bytes into planes, plus the legal-action mask."""
    batch = features.shape[0]
    device = features.device
    spatial = features[:, :CHANNELS * CELLS].float().div(255).view(batch, CHANNELS, SIZE, SIZE)
    globals_ = features[:, CHANNELS * CELLS:BASE_BYTES].float().div(255)
    ticks = ticks.long()

    stego = torch.zeros(batch, 1, SIZE, SIZE, device=device)
    phase = ticks % STEGO_CYCLE
    column = torch.where(phase < SIZE, phase, STEGO_CYCLE - phase)
    stego[torch.arange(batch, device=device), 0, STEGO_ROW, column.clamp(max=SIZE - 1)] = 1

    axis = torch.linspace(-1, 1, SIZE, device=device)
    rows = axis.view(1, 1, SIZE, 1).expand(batch, 1, SIZE, SIZE)
    columns = axis.view(1, 1, 1, SIZE).expand(batch, 1, SIZE, SIZE)

    angle = 2 * math.pi * ticks.float()
    clock = torch.stack([
        torch.sin(angle / 5), torch.cos(angle / 5),
        torch.sin(angle / STEGO_CYCLE), torch.cos(angle / STEGO_CYCLE),
    ], dim=1)
    flat = torch.cat([globals_, clock], dim=1).view(batch, GLOBALS + 4, 1, 1).expand(batch, GLOBALS + 4, SIZE, SIZE)
    spawn = features[:, BASE_BYTES:BASE_BYTES + CELLS].float().div(255).view(batch, 1, SIZE, SIZE)
    extra = features[:, BASE_BYTES + CELLS:].float().div(255)
    alive_none = (features[:, CHANNELS * CELLS + 1] == 0).float().unsqueeze(1)  # A wave comes next tick.
    extra = torch.cat([extra, alive_none], dim=1).view(batch, 3, 1, 1).expand(batch, 3, SIZE, SIZE)
    planes = torch.cat([spatial, stego, rows, columns, flat, spawn, extra[:, :2], extra[:, 2:]], dim=1)

    meteors_free = features[:, CHANNELS * CELLS + 2] < 240
    volcano_free = features[:, CHANNELS * CELLS + 3] == 0
    mountains = features[:, MOUNTAIN_CHANNEL * CELLS:(MOUNTAIN_CHANNEL + 1) * CELLS] > 0
    mask = torch.cat([
        torch.ones(batch, 1, dtype=torch.bool, device=device),
        meteors_free.unsqueeze(1).expand(batch, CELLS),
        mountains & volcano_free.unsqueeze(1),
    ], dim=1)
    return planes, mask


class Block(nn.Module):
    def __init__(self, channels: int, dilation: int):
        super().__init__()
        self.conv = nn.Conv2d(channels, channels, 3, padding=dilation, dilation=dilation)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + F.relu(self.conv(x))


class PolicyNet(nn.Module):
    """Stays at full resolution, so each tile gets its own logit. Dilations reach the whole map."""

    def __init__(self, channels: int = 48, hidden: int = 128):
        super().__init__()
        self.head = nn.Conv2d(PLANES, channels, 3, padding=1)
        self.blocks = nn.ModuleList(Block(channels, d) for d in (1, 1, 2, 2, 4, 1))
        self.tiles = nn.Conv2d(channels, 2, 1)
        self.fc = nn.Linear(2 * channels, hidden)
        self.none = nn.Linear(hidden, 1)
        self.value = nn.Linear(hidden, 1)
        nn.init.normal_(self.tiles.weight, std=0.01)
        nn.init.zeros_(self.tiles.bias)
        nn.init.normal_(self.none.weight, std=0.01)
        nn.init.zeros_(self.none.bias)

    def forward(self, planes: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = F.relu(self.head(planes))
        for block in self.blocks:
            x = block(x)
        tiles = self.tiles(x)
        pooled = torch.cat([x.mean(dim=(2, 3)), x.amax(dim=(2, 3))], dim=1)
        hidden = F.relu(self.fc(pooled))
        logits = torch.cat([self.none(hidden), tiles[:, 0].flatten(1), tiles[:, 1].flatten(1)], dim=1)
        logits = logits.masked_fill(~mask, -1e4)
        return logits, self.value(hidden).squeeze(1)


def export(net: PolicyNet, path: Path) -> None:
    """Writes the layers sim/policy_net.cpp needs, as raw float32 after two int32 sizes."""
    hidden = net.fc.out_features
    layers = [net.head, *(block.conv for block in net.blocks), net.tiles, net.fc, net.none]
    with open(path, "wb") as file:
        np.array([net.head.out_channels, hidden], dtype=np.int32).tofile(file)
        for layer in layers:
            for parameter in (layer.weight, layer.bias):
                parameter.detach().cpu().numpy().astype(np.float32).tofile(file)


def sample(logits: torch.Tensor) -> torch.Tensor:
    noise = -torch.log(-torch.log(torch.rand_like(logits).clamp(min=1e-9)))
    return (logits.float() + noise).argmax(dim=1)


@torch.no_grad()
def evaluate(net: PolicyNet, args, device: torch.device, label: str) -> dict:
    """Plays fresh seeds to the end, taking the best action or sampling."""
    results = {}
    for name, greedy in (("greedy", True), ("sampled", False)):
        env = VecEnv(args.eval_games, args.threads, args.biomes, f"{label}-{name}", False)
        scores = None
        for _ in range(1000):
            features = torch.from_numpy(env.features).to(device)
            ticks = torch.from_numpy(env.ticks).to(device)
            planes, mask = build_inputs(features, ticks)
            with torch.amp.autocast("cuda"):
                logits, _ = net(planes, mask)
            actions = logits.argmax(dim=1) if greedy else sample(logits)
            env.step(actions.cpu().numpy())
            if (env.scores >= 0).any():
                scores = env.scores.copy()
        results[name] = float(scores.mean())
        results[name + "_best"] = int(scores.max())
        env.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--resume-net", type=Path, help="Takes only the weights, such as from train_policy.py")
    parser.add_argument("--scale", type=float, help="Overrides the return scale in the checkpoint")
    parser.add_argument("--envs", type=int, default=2048)
    parser.add_argument("--steps", type=int, default=64)
    parser.add_argument("--threads", type=int, default=11)
    parser.add_argument("--biomes", type=int, default=7)
    parser.add_argument("--channels", type=int, default=48)
    parser.add_argument("--hours", type=float, default=1)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.995)
    parser.add_argument("--lam", type=float, default=0.97)
    parser.add_argument("--clip", type=float, default=0.2)
    parser.add_argument("--entropy", type=float, default=0.003)
    parser.add_argument("--value-coef", type=float, default=0.5)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--minibatch", type=int, default=8192)
    parser.add_argument("--eval-every", type=int, default=100)
    parser.add_argument("--eval-games", type=int, default=128)
    parser.add_argument("--group-bonus", type=float, default=0,
                        help="Multiplies a tick's reward by 1 + bonus * (kills - 1), early in training only")
    parser.add_argument("--bonus-hours", type=float, default=0)
    parser.add_argument("--plans", type=Path, help="From tools/extract_plans.py. Some games start inside these plans")
    parser.add_argument("--plan-share", type=float, default=0.5)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "args.json").write_text(json.dumps({k: str(v) for k, v in vars(args).items()}, indent=2))
    device = torch.device("cuda")
    torch.backends.cudnn.benchmark = True

    net = PolicyNet(args.channels).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr, eps=1e-5)
    scaler = torch.amp.GradScaler("cuda")
    iteration = 0
    if args.resume:
        state = torch.load(args.resume, map_location=device)
        net.load_state_dict(state["net"])
        optimizer.load_state_dict(state["optimizer"])
        iteration = state["iteration"]
    return_scale = state.get("scale", 1.0) if args.resume else 1.0
    if args.resume_net:
        state = torch.load(args.resume_net, map_location=device)
        weights = state["net"]
        old = weights["head.weight"]
        if old.shape[1] < PLANES:  # New input planes start with no effect.
            pad = torch.zeros(old.shape[0], PLANES - old.shape[1], 3, 3, device=old.device)
            weights["head.weight"] = torch.cat([old, pad], dim=1)
        net.load_state_dict(weights)
        return_scale = state.get("scale", 1.0)
    if args.scale:
        return_scale = args.scale
    print(f"{sum(p.numel() for p in net.parameters()):,} parameters", flush=True)

    env = VecEnv(args.envs, args.threads, args.biomes, f"{args.out.name}-{iteration}", True)
    if args.plans:
        print(env.lib.vec_set_plans(env.handle, str(args.plans).encode(), args.plan_share), "plans", flush=True)
    n, t = args.envs, args.steps
    features = torch.zeros(t, n, FEATURE_BYTES, dtype=torch.uint8, device=device)
    ticks = torch.zeros(t, n, dtype=torch.int32, device=device)
    actions = torch.zeros(t, n, dtype=torch.long, device=device)
    log_probs = torch.zeros(t, n, device=device)
    values = torch.zeros(t, n, device=device)
    rewards = torch.zeros(t, n, device=device)
    dones = torch.zeros(t, n, device=device)

    finished: list[int] = []
    kill_counts = np.zeros(21, dtype=np.int64)
    start = time.perf_counter()
    last_log = start
    samples = 0
    best_eval = -1.0

    while time.perf_counter() - start < args.hours * 3600:
        hours = (time.perf_counter() - start) / 3600
        bonus = args.group_bonus if hours < args.bonus_hours else 0.0

        net.eval()
        with torch.no_grad():
            for step in range(t):
                current = torch.from_numpy(env.features).to(device, non_blocking=True)
                current_ticks = torch.from_numpy(env.ticks).to(device, non_blocking=True)
                features[step], ticks[step] = current, current_ticks
                planes, mask = build_inputs(current, current_ticks)
                with torch.amp.autocast("cuda"):
                    logits, value = net(planes, mask)
                logits = logits.float()
                action = sample(logits)
                actions[step] = action
                log_probs[step] = F.log_softmax(logits, dim=1).gather(1, action.unsqueeze(1)).squeeze(1)
                values[step] = value.float() * return_scale

                env.step(action.cpu().numpy())
                reward = env.rewards.copy()
                if bonus > 0:
                    reward *= 1 + bonus * np.maximum(env.kills - 1, 0)
                rewards[step] = torch.from_numpy(reward).to(device) / 100
                ended = env.scores >= 0
                dones[step] = torch.from_numpy(ended.astype(np.float32)).to(device)
                finished.extend(env.scores[ended].tolist())
                kill_counts += np.bincount(np.minimum(env.kills, 20), minlength=21)

            planes, mask = build_inputs(torch.from_numpy(env.features).to(device), torch.from_numpy(env.ticks).to(device))
            with torch.amp.autocast("cuda"):
                _, next_value = net(planes, mask)
            next_value = next_value.float() * return_scale

        advantages = torch.zeros(t, n, device=device)
        running = torch.zeros(n, device=device)
        for step in reversed(range(t)):
            alive = 1 - dones[step]
            following = next_value if step == t - 1 else values[step + 1]
            delta = rewards[step] + args.gamma * following * alive - values[step]
            running = delta + args.gamma * args.lam * alive * running
            advantages[step] = running
        returns = advantages + values
        return_scale = 0.99 * return_scale + 0.01 * max(float(returns.std()), 0.1)

        net.train()
        total = t * n
        flat_features = features.view(total, FEATURE_BYTES)
        flat_ticks = ticks.view(total)
        flat_actions = actions.view(total)
        flat_log_probs = log_probs.view(total)
        flat_advantages = advantages.view(total)
        flat_returns = returns.view(total)
        policy_loss_sum = value_loss_sum = entropy_sum = clip_sum = 0.0
        batches = 0
        for _ in range(args.epochs):
            order = torch.randperm(total, device=device)
            for first in range(0, total - args.minibatch + 1, args.minibatch):
                index = order[first:first + args.minibatch]
                planes, mask = build_inputs(flat_features[index], flat_ticks[index])
                with torch.amp.autocast("cuda"):
                    logits, value = net(planes, mask)
                    logits = logits.float()
                    log_all = F.log_softmax(logits, dim=1)
                    log_prob = log_all.gather(1, flat_actions[index].unsqueeze(1)).squeeze(1)
                    ratio = torch.exp(log_prob - flat_log_probs[index])
                    advantage = flat_advantages[index]
                    advantage = (advantage - advantage.mean()) / (advantage.std() + 1e-8)
                    policy_loss = -torch.min(ratio * advantage, ratio.clamp(1 - args.clip, 1 + args.clip) * advantage).mean()
                    value_loss = F.mse_loss(value.float(), flat_returns[index] / return_scale)
                    probs = log_all.exp()
                    entropy = -(probs * log_all.masked_fill(probs < 1e-12, 0)).sum(dim=1).mean()
                    loss = policy_loss + args.value_coef * value_loss - args.entropy * entropy
                optimizer.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                policy_loss_sum += policy_loss.detach().item()
                value_loss_sum += value_loss.detach().item()
                entropy_sum += entropy.detach().item()
                clip_sum += ((ratio - 1).abs() > args.clip).float().mean().detach().item()
                batches += 1

        iteration += 1
        samples += total
        now = time.perf_counter()
        if now - last_log >= 60:
            recent = finished[-500:]
            big = " ".join(f"{k}:{kill_counts[k]}" for k in range(1, 21) if kill_counts[k])
            print(
                f"iter {iteration}  {(now - start) / 3600:.2f}h  {samples / (now - start):,.0f} samples/s  "
                f"games {len(finished)}  score {np.mean(recent) if recent else 0:,.0f}  "
                f"policy {policy_loss_sum / batches:.4f}  value {value_loss_sum / batches:.3f}  "
                f"entropy {entropy_sum / batches:.2f}  clip {clip_sum / batches:.2f}  scale {return_scale:.1f}  "
                f"kills {big}",
                flush=True,
            )
            kill_counts[:] = 0
            last_log = now

        if iteration % args.eval_every == 0:
            net.eval()
            results = evaluate(net, args, device, f"eval-{iteration}")
            print(f"eval {iteration}  {results}", flush=True)
            state = {"net": net.state_dict(), "optimizer": optimizer.state_dict(), "iteration": iteration, "channels": args.channels, "scale": return_scale}
            torch.save(state, args.out / "latest.pt")
            score = max(results["greedy"], results["sampled"])
            if score > best_eval:
                best_eval = score
                torch.save(state, args.out / "best.pt")

    torch.save({"net": net.state_dict(), "optimizer": optimizer.state_dict(), "iteration": iteration, "channels": args.channels, "scale": return_scale}, args.out / "latest.pt")
    env.close()


if __name__ == "__main__":
    main()
