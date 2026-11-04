"""Exercises edge actions, then random actions, to compare simulator behavior.

Set ACTIONS_LOG to choose the log path and CHAOS_SEED to vary the game. The
opening actions cover rejected targets, volcano constraints, map edges, and
the in-flight meteor limit; later actions vary randomly.
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
        if game_message.tick <= 9:
            return self.coverage_action(game_message)

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

    @staticmethod
    def coverage_action(game_message: TeamGameState) -> Action | None:
        tick = game_message.tick
        origin = game_message.map.origin
        if tick == 1:
            return LaunchMeteorAction(target=WorldPosition(x=origin.x - 1, y=origin.y))
        if tick == 2:
            return TriggerVolcanoAction(target=WorldPosition(x=origin.x, y=origin.y - 1))
        if tick == 3:
            for row, tiles in enumerate(game_message.map.tiles):
                for column, tile in enumerate(tiles):
                    if not tile.isMountain:
                        return TriggerVolcanoAction(
                            target=WorldPosition(x=origin.x + column, y=origin.y + row)
                        )
            return TriggerVolcanoAction(target=WorldPosition(x=origin.x, y=origin.y - 1))
        if tick in (4, 5):
            if game_message.mountains:
                return TriggerVolcanoAction(target=game_message.mountains[0])
            return None
        if tick == 6:
            return LaunchMeteorAction(target=WorldPosition(x=origin.x, y=origin.y))
        if tick == 7:
            return LaunchMeteorAction(
                target=WorldPosition(
                    x=origin.x + game_message.map.width - 1,
                    y=origin.y + game_message.map.height - 1,
                )
            )
        if tick == 8:
            target = (
                game_message.dinosaurs[0].position
                if game_message.dinosaurs
                else WorldPosition(x=origin.x + 10, y=origin.y + 10)
            )
            return LaunchMeteorAction(target=target)
        if tick == 9:
            return LaunchMeteorAction(target=WorldPosition(x=origin.x + 10, y=origin.y + 10))
        return None
