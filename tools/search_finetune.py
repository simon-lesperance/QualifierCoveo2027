"""Fine-tune a depthwise policy net towards the halving search's decisions (expert iteration).

`sim_live --dump folder` writes one record per searched tick: the root's policy features, the tick,
the chosen action and each halving action's visits. This trains the net on

    cross-entropy(search target) + kl_weight * KL(teacher || net), both at the dumped positions,

so it learns what the search ends up preferring while staying close to the PPO teacher.

    python tools/search_finetune.py runs/policy/ds32/small.pt runs/ppo/dag1/best.pt runs/dumps/a --out runs/policy/ex1
"""

import argparse
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from distill_policy import DepthwiseNet, export_depthwise
from train_ppo import FEATURE_BYTES, PolicyNet, build_inputs

ARMS = 16
RECORD = FEATURE_BYTES + 6 + 4 * ARMS


def load(folders: list[Path]) -> tuple[list[np.ndarray], list[str]]:
    games, names = [], []
    for folder in folders:
        for path in sorted(folder.glob("*.bin")):
            raw = np.fromfile(path, dtype=np.uint8)
            if raw.size == 0 or raw.size % RECORD:
                print(f"skipping {path} ({raw.size} bytes)")
                continue
            games.append(raw.reshape(-1, RECORD))
            names.append(path.name)
    return games, names


def unpack(records: np.ndarray, device: torch.device):
    features = torch.from_numpy(np.ascontiguousarray(records[:, :FEATURE_BYTES])).to(device)
    header = np.ascontiguousarray(records[:, FEATURE_BYTES:FEATURE_BYTES + 6]).view(np.int16)
    arms = np.ascontiguousarray(records[:, FEATURE_BYTES + 6:]).view(np.int16).reshape(-1, ARMS, 2)
    ticks = torch.from_numpy(header[:, 0].astype(np.int32)).to(device)
    chosen = torch.from_numpy(header[:, 1].astype(np.int64)).to(device)
    count = torch.from_numpy(header[:, 2].astype(np.int64)).to(device)
    codes = torch.from_numpy(arms[:, :, 0].astype(np.int64)).to(device)
    visits = torch.from_numpy(arms[:, :, 1].astype(np.float32)).to(device)
    visits = visits * (torch.arange(ARMS, device=device).unsqueeze(0) < count.unsqueeze(1))
    return features, ticks, chosen, codes, visits


def targets(chosen, codes, visits, mask, actions, mix: float, power: float) -> torch.Tensor:
    """One-hot on the chosen action, mixed with the halving visits (raised to power) by mix."""
    batch = chosen.shape[0]
    target = torch.zeros(batch, actions, device=chosen.device)
    target[torch.arange(batch, device=chosen.device), chosen] = 1 - mix
    if mix > 0:
        weights = visits.pow(power)
        weights = weights / weights.sum(dim=1, keepdim=True).clamp(min=1e-6)
        target.scatter_add_(1, codes, mix * weights)
    return target * mask


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("student", type=Path, help="small.pt of the depthwise net to start from")
    parser.add_argument("teacher", type=Path)
    parser.add_argument("data", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--kl-weight", type=float, default=1.0)
    parser.add_argument("--ce-weight", type=float, default=1.0, help="0 trains on the teacher alone, at search positions")
    parser.add_argument("--temperature", type=float, default=2.0)
    parser.add_argument("--mix", type=float, default=0.5, help="Share of the target from halving visits")
    parser.add_argument("--power", type=float, default=2.0)
    parser.add_argument("--holdout", type=float, default=0.1, help="Share of games kept for validation")
    parser.add_argument("--report-seconds", type=float, default=60)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)
    torch.manual_seed(0)

    games, names = load(args.data)
    order = np.random.default_rng(0).permutation(len(games))
    held = max(1, int(len(games) * args.holdout))
    train = unpack(np.concatenate([games[i] for i in order[held:]]), device)
    valid = unpack(np.concatenate([games[i] for i in order[:held]]), device)
    print(f"{len(games)} games, {train[0].shape[0]:,} training and {valid[0].shape[0]:,} validation positions", flush=True)

    state = torch.load(args.teacher, map_location=device)
    teacher = PolicyNet(state["channels"]).to(device).eval()
    teacher.load_state_dict(state["net"])
    start_state = torch.load(args.student, map_location=device)
    net = DepthwiseNet(start_state["channels"], start_state["hidden"]).to(device)
    net.load_state_dict(start_state["net"])
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)

    def batch_loss(data, index, train_mode: bool):
        features, ticks, chosen, codes, visits = (t[index] for t in data)
        planes, mask = build_inputs(features, ticks)
        with torch.no_grad(), torch.autocast("cuda", enabled=device.type == "cuda"):
            teacher_logits, _ = teacher(planes, mask)
        teacher_logits = teacher_logits.float().masked_fill(~mask, -1e4)
        with torch.set_grad_enabled(train_mode):
            logits, _ = net(planes, mask)
            target = targets(chosen, codes, visits, mask, logits.shape[1], args.mix, args.power)
            log_probs = F.log_softmax(logits, dim=1)
            ce = -(target * log_probs).where(mask, 0.0).sum(dim=1).mean()
            teacher_log = F.log_softmax(teacher_logits / args.temperature, dim=1)
            student_log = F.log_softmax(logits / args.temperature, dim=1)
            kl = (teacher_log.exp() * (teacher_log - student_log)).where(mask, 0.0).sum(dim=1).mean()
            loss = args.ce_weight * ce + args.kl_weight * kl
        with torch.no_grad():
            top16 = logits.topk(ARMS, dim=1).indices
            stats = torch.stack([
                ce.detach(), kl.detach(),
                (logits.argmax(dim=1) == chosen).float().mean(),
                (top16 == chosen.unsqueeze(1)).any(dim=1).float().mean(),
                (teacher_logits.argmax(dim=1) == chosen).float().mean(),
            ])
        return loss, stats

    def validate():
        net.eval()
        totals = torch.zeros(5, device=device)
        size = valid[0].shape[0]
        count = 0
        for begin in range(0, size, 4096):
            index = torch.arange(begin, min(size, begin + 4096), device=device)
            _, stats = batch_loss(valid, index, False)
            totals += stats * index.numel()
            count += index.numel()
        net.train()
        return (totals / count).tolist()

    def describe(values):
        return (f"ce {values[0]:.3f} kl {values[1]:.3f} top1 {values[2]:.3f} "
                f"chosen in top16 {values[3]:.3f} teacher top1 {values[4]:.3f}")

    print("start  valid " + describe(validate()), flush=True)
    size = train[0].shape[0]
    start = last = time.perf_counter()
    steps = 0
    best_ce = float("inf")
    while (now := time.perf_counter()) - start < args.minutes * 60:
        progress = (now - start) / (args.minutes * 60)
        for group in optimizer.param_groups:
            group["lr"] = args.lr * min(1.0, 1.05 - progress)
        index = torch.randint(size, (args.batch,), device=device)
        loss, _ = batch_loss(train, index, True)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
        optimizer.step()
        steps += 1
        if now - last >= args.report_seconds:
            last = now
            values = validate()
            print(f"{(now - start) / 60:5.1f} min  steps {steps}  valid " + describe(values), flush=True)
            torch.save({"net": net.state_dict(), "channels": start_state["channels"], "hidden": start_state["hidden"]},
                       args.out / "small.pt")
            export_depthwise(net, args.out / "policy_net.bin")
            if values[0] < best_ce:
                best_ce = values[0]
    torch.save({"net": net.state_dict(), "channels": start_state["channels"], "hidden": start_state["hidden"]},
               args.out / "small.pt")
    export_depthwise(net, args.out / "policy_net.bin")
    print("end    valid " + describe(validate()), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
