"""Exports a checkpoint for sim/policy_net.cpp, with samples to check it against.

    python tools/export_policy.py runs/policy/first/policy.pt
"""

import argparse
from pathlib import Path

import numpy as np
import torch

from distill_policy import DepthwiseNet, export_depthwise
from train_ppo import PolicyNet, VecEnv, build_inputs, export


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()
    state = torch.load(args.checkpoint, map_location="cpu")
    folder = args.checkpoint.parent
    if "blocks.0.spatial.weight" in state["net"]:
        net = DepthwiseNet(state["channels"], state["hidden"])
        net.load_state_dict(state["net"])
        export_depthwise(net, folder / "policy_net.bin")
    else:
        net = PolicyNet(state["channels"], state.get("hidden", 128))
        net.load_state_dict(state["net"])
        export(net, folder / "policy_net.bin")
    net.eval()

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
