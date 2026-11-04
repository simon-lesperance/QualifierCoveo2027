"""Beam search through the compiled simulator.

The library is built by tools/build_sim.py and loaded here at import time, which
runs before the bot connects. Set BOT_TIME_MS to test with a different time budget.
"""

import ctypes
import os
import sys
import time

from game_message import *

WIDTH = 20
CELLS = WIDTH * WIDTH
MAX_DINOS = 20
MAX_METEORS = 3
MAX_VOLCANOES = 1

IMPASSABLE, MOUNTAIN, LAVA = 1, 2, 4
SPECIES = {"Stegosaurus": 0, "Velociraptor": 1, "Triceratops": 2, "Tyrannosaurus": 3}

# The limit is 150 ms, but even a trivial bot sees spikes of up to 65 ms.
TIME_BUDGET_MS = float(os.environ.get("BOT_TIME_MS", "95"))
BEAM, RADIUS, HORIZON, VOLCANOES = 30, 2, 4, 6


class Observation(ctypes.Structure):
    """Mirrors sim::Observation in sim/game.hpp."""

    _fields_ = [
        ("tick", ctypes.c_int32),
        ("origin_y", ctypes.c_int32),
        ("dino_count", ctypes.c_int32),
        ("dino_id", ctypes.c_int32 * MAX_DINOS),
        ("dino_species", ctypes.c_int32 * MAX_DINOS),
        ("dino_x", ctypes.c_int32 * MAX_DINOS),
        ("dino_y", ctypes.c_int32 * MAX_DINOS),
        ("dino_age", ctypes.c_int32 * MAX_DINOS),
        ("meteor_count", ctypes.c_int32),
        ("meteor_x", ctypes.c_int32 * MAX_METEORS),
        ("meteor_y", ctypes.c_int32 * MAX_METEORS),
        ("meteor_turns", ctypes.c_int32 * MAX_METEORS),
        ("volcano_count", ctypes.c_int32),
        ("volcano_x", ctypes.c_int32 * MAX_VOLCANOES),
        ("volcano_y", ctypes.c_int32 * MAX_VOLCANOES),
        ("elevation", ctypes.c_uint8 * CELLS),
        ("flags", ctypes.c_uint8 * CELLS),
        ("corpses", ctypes.c_uint8 * CELLS),
    ]


def load_library() -> ctypes.CDLL:
    name = "libsim.dll" if sys.platform == "win32" else "libsim.so"
    library = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))
    library.sim_agent_create.restype = ctypes.c_void_p
    library.sim_agent_create.argtypes = [ctypes.c_int] * 4
    library.sim_agent_act.restype = None
    library.sim_agent_act.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(Observation), ctypes.c_double,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    library.sim_agent_last_simulated.restype = ctypes.c_int
    library.sim_agent_last_simulated.argtypes = [ctypes.c_void_p]
    if library.sim_observation_size() != ctypes.sizeof(Observation):
        raise RuntimeError("Observation layout differs between Python and C++")
    return library


LIBRARY = load_library()


class Bot:
    def __init__(self):
        self.agent = LIBRARY.sim_agent_create(BEAM, RADIUS, HORIZON, VOLCANOES)
        self.observation = Observation()
        self.spent = []
        self.spent_ms = []

    def fill(self, game_message: TeamGameState) -> None:
        o = self.observation
        o.tick = game_message.tick
        origin = game_message.map.origin
        o.origin_y = origin.y

        dinosaurs = game_message.dinosaurs[:MAX_DINOS]
        o.dino_count = len(dinosaurs)
        for i, dino in enumerate(dinosaurs):
            o.dino_id[i] = dino.id
            o.dino_species[i] = SPECIES[dino.name]
            o.dino_x[i] = dino.position.x
            o.dino_y[i] = dino.position.y
            o.dino_age[i] = dino.age

        meteors = game_message.meteors[:MAX_METEORS]
        o.meteor_count = len(meteors)
        for i, meteor in enumerate(meteors):
            o.meteor_x[i] = meteor.target.x
            o.meteor_y[i] = meteor.target.y
            o.meteor_turns[i] = meteor.turnsUntilImpact

        volcanoes = game_message.volcanoes[:MAX_VOLCANOES]
        o.volcano_count = len(volcanoes)
        for i, volcano in enumerate(volcanoes):
            o.volcano_x[i] = volcano.position.x
            o.volcano_y[i] = volcano.position.y

        cell = 0
        elevation, flags = o.elevation, o.flags
        for row in game_message.map.tiles:
            for tile in row:
                elevation[cell] = tile.elevation
                flags[cell] = (
                    (IMPASSABLE if tile.isImpassable else 0)
                    | (MOUNTAIN if tile.isMountain else 0)
                    | (LAVA if tile.hasLava else 0)
                )
                cell += 1

        ctypes.memset(o.corpses, 0, CELLS)
        for corpse in game_message.corpses:
            o.corpses[(corpse.y - origin.y) * WIDTH + corpse.x] = 1

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        start = time.perf_counter()
        self.fill(game_message)

        remaining = TIME_BUDGET_MS - (time.perf_counter() - start) * 1000
        kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        LIBRARY.sim_agent_act(
            self.agent, ctypes.byref(self.observation), max(remaining, 1.0),
            ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y),
        )

        simulated = LIBRARY.sim_agent_last_simulated(self.agent)
        if simulated > 0:
            self.spent.append(simulated)
            self.spent_ms.append((time.perf_counter() - start) * 1000)
        if game_message.tick % 100 == 0 and self.spent:
            ordered = sorted(self.spent)
            print(
                f"BUDGET tick={game_message.tick} score={game_message.score} "
                f"simulated median={ordered[len(ordered) // 2]} min={ordered[0]} "
                f"move_ms max={max(self.spent_ms):.1f}",
                flush=True,
            )
            self.spent, self.spent_ms = [], []

        if kind.value == 1:
            return [LaunchMeteorAction(target=WorldPosition(x=x.value, y=y.value))]
        if kind.value == 2:
            return [TriggerVolcanoAction(target=WorldPosition(x=x.value, y=y.value))]
        return []
