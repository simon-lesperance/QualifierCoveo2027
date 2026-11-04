"""Plays a fixed plan, to test the simulator's results on the real engine.

REPLAY_ACTIONS is a plan file with lines of "<tick> <M|V> <x> <y>".
"""

import os

from game_message import *


class Bot:
    def __init__(self):
        self.plan = {}
        with open(os.environ["REPLAY_ACTIONS"]) as file:
            for line in file:
                tick, kind, x, y = line.split()
                action = TriggerVolcanoAction if kind == "V" else LaunchMeteorAction
                self.plan[int(tick)] = action(target=WorldPosition(x=int(x), y=int(y)))

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        action = self.plan.get(game_message.tick)
        return [action] if action else []
