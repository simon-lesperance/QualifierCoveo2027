"""Exports a checkpoint for sim/policy_net.cpp, with samples to check it against.

    python tools/export_policy.py runs/policy/first/policy.pt
"""

import argparse
from pathlib import Path

import numpy as np
import torch

from train_ppo import PolicyNet, VecEnv, build_inputs, export


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()
    state = torch.load(args.checkpoint, map_location="cpu")
    net = PolicyNet(state["channels"])
    net.load_state_dict(state["net"])
    net.eval()
    folder = args.checkpoint.parent
    export(net, folder / "policy_net.bin")

    env = VecEnv(16, 1, 7, "export", True)
    features = torch.from_numpy(env.features.copy())
    ticks = torch.from_numpy(env.ticks.copy())
    with torch.no_grad():
        logits, _ = net(*build_inputs(features, ticks))
    with open(folder / "policy_check.bin", "wb") as file:
        np.array([len(features)], dtype=np.int32).tofile(file)
        for i in range(len(features)):
            np.array([env.ticks[i]], dtype=np.int32).tofile(file)
            env.features[i].tofile(file)
            logits[i].numpy().astype(np.float32).tofile(file)
    print("exported", folder)


if __name__ == "__main__":
    main()
