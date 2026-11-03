"""Plays random actions and logs them, to test the C++ simulator against the real engine.

Set ACTIONS_LOG to choose the log path and CHAOS_SEED to vary the game. Some
actions are invalid on purpose, so rejected actions get tested too.
"""

import os
import random

from game_message import *


class Bot:
    def __init__(self):
        self.rng = random.Random(os.environ.get("CHAOS_SEED", "0"))
        self.log = open(os.environ["ACTIONS_LOG"], "w")

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        action = self.choose(game_message)
        if action is None:
            return []
        kind = "V" if isinstance(action, TriggerVolcanoAction) else "M"
        self.log.write(f"{game_message.tick} {kind} {action.target.x} {action.target.y}\n")
        self.log.flush()
        return [action]

    def choose(self, game_message: TeamGameState) -> Action | None:
        rng = self.rng
        roll = rng.random()
        origin = game_message.map.origin

        if roll < 0.06 and game_message.mountains:
            return TriggerVolcanoAction(target=rng.choice(game_message.mountains))
        if roll < 0.08:
            # Usually not a mountain, or out of the frame.
            return TriggerVolcanoAction(
                target=WorldPosition(x=rng.randint(-1, 20), y=origin.y + rng.randint(-1, 20))
            )
        if roll < 0.10:
            return LaunchMeteorAction(
                target=WorldPosition(x=rng.randint(-1, 20), y=origin.y + rng.randint(-1, 20))
            )
        if roll < 0.75 and game_message.dinosaurs:
            position = rng.choice(game_message.dinosaurs).position
            target = WorldPosition(
                x=min(19, max(0, position.x + rng.randint(-2, 2))),
                y=max(origin.y, position.y + rng.randint(-2, 2)),
            )
            return LaunchMeteorAction(target=target)
        return None
