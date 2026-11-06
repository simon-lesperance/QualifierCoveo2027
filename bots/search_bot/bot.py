"""Open-loop MCTS through the compiled simulator.

The library is built by tools/build_sim.py and loaded here at import time, which
runs before the bot connects.

Each tick is an anytime search: a worker thread grows one MCTS tree and publishes
its best action at BOT_FIRST_MS, then every BOT_STEP_MS, and a last time just
before BOT_LAST_MS. The calling thread returns the latest published action by
BOT_LAST_MS whatever the worker is doing, so a slow search never means no action.
"""

import ctypes
import os
import sys
import threading
import time

from game_message import *

WIDTH = 20
CELLS = WIDTH * WIDTH
MAX_DINOS = 20
MAX_METEORS = 3
MAX_VOLCANOES = 1

IMPASSABLE, MOUNTAIN, LAVA = 1, 2, 4
SPECIES = {"Stegosaurus": 0, "Velociraptor": 1, "Triceratops": 2, "Tyrannosaurus": 3}

# The server limit is 150 ms, measured from when it sends the tick.
FIRST_DECISION_MS = float(os.environ.get("BOT_FIRST_MS", "75"))
DECISION_STEP_MS = float(os.environ.get("BOT_STEP_MS", "10"))
LAST_DECISION_MS = float(os.environ.get("BOT_LAST_MS", "95"))
# The worker's final search slice ends this early, so its answer is ready at LAST_DECISION_MS.
FINAL_SLICE_MARGIN_MS = 1.0
if not 0 < FIRST_DECISION_MS <= LAST_DECISION_MS or DECISION_STEP_MS <= 0:
    raise ValueError("Need 0 < BOT_FIRST_MS <= BOT_LAST_MS and BOT_STEP_MS > 0")


def decision_checkpoints() -> list[float]:
    """Elapsed ms at which the worker publishes, e.g. 90, 100, ..., 140, 148."""
    last = max(LAST_DECISION_MS - FINAL_SLICE_MARGIN_MS, 0.0)
    checkpoints = []
    at = FIRST_DECISION_MS
    while at < last:
        checkpoints.append(at)
        at += DECISION_STEP_MS
    return checkpoints + [last]


CHECKPOINTS_MS = decision_checkpoints()
RADIUS, HORIZON, VOLCANOES, DEPTH = 2, 4, 6, 5
WIDENING, EXPLORATION = 3.0, 0.5
AIM_PREDICTED = os.environ.get("BOT_AIM_PREDICTED", "0") == "1"
VOLCANO_NEAR_DINOS = os.environ.get("BOT_VOLCANO_NEAR", "0") == "1"
OPTIMISM = float(os.environ.get("BOT_OPTIMISM", "0"))
if not 0 <= OPTIMISM <= 1:
    raise ValueError("BOT_OPTIMISM must be between 0 and 1")


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


def load_library(file_name: str | None = None) -> ctypes.CDLL:
    name = file_name or ("libsim.dll" if sys.platform == "win32" else "libsim.so")
    library = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))
    library.sim_agent_create.restype = ctypes.c_void_p
    library.sim_agent_create.argtypes = [ctypes.c_int] * 4 + [ctypes.c_double] * 2
    library.sim_agent_set_targeting.restype = None
    library.sim_agent_set_targeting.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.sim_agent_set_optimism.restype = None
    library.sim_agent_set_optimism.argtypes = [ctypes.c_void_p, ctypes.c_double]
    library.sim_agent_act.restype = None
    library.sim_agent_act.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(Observation), ctypes.c_double,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    library.sim_agent_last_simulated.restype = ctypes.c_int
    library.sim_agent_last_simulated.argtypes = [ctypes.c_void_p]
    library.sim_agent_begin.restype = ctypes.c_int
    library.sim_agent_begin.argtypes = [ctypes.c_void_p, ctypes.POINTER(Observation)]
    library.sim_agent_resume.restype = None
    library.sim_agent_resume.argtypes = [
        ctypes.c_void_p, ctypes.c_double,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    library.sim_agent_stop.restype = None
    library.sim_agent_stop.argtypes = [ctypes.c_void_p]
    if library.sim_observation_size() != ctypes.sizeof(Observation):
        raise RuntimeError("Observation layout differs between Python and C++")
    return library


LIBRARY = load_library()


class Bot:
    def __init__(self):
        self.agent = LIBRARY.sim_agent_create(RADIUS, HORIZON, VOLCANOES, DEPTH, WIDENING, EXPLORATION)
        LIBRARY.sim_agent_set_targeting(self.agent, AIM_PREDICTED, VOLCANO_NEAR_DINOS)
        LIBRARY.sim_agent_set_optimism(self.agent, OPTIMISM)
        self.observation = Observation()
        self.spent = []
        self.spent_ms = []
        self.revised = 0   # Ticks where the final decision differs from the first one.
        self.overran = 0   # Ticks where the worker missed the last checkpoint.
        self.worker = None
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.decisions = []

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

    def search(self, start: float) -> None:
        """Worker thread: grows the tree between checkpoints and publishes each best action.

        ctypes releases the GIL during library calls, so the caller keeps its own clock.
        """
        kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        try:
            for checkpoint in CHECKPOINTS_MS:
                remaining = checkpoint - (time.perf_counter() - start) * 1000
                LIBRARY.sim_agent_resume(
                    self.agent, remaining, ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y),
                )
                with self.lock:
                    self.decisions.append((kind.value, x.value, y.value))
        finally:
            self.finished.set()

    def wait_for_worker(self) -> None:
        """The tree belongs to one search at a time, so never start over a running worker."""
        if self.worker is not None:
            LIBRARY.sim_agent_stop(self.agent)
            self.worker.join()
            self.worker = None

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        start = time.perf_counter()
        self.wait_for_worker()
        self.fill(game_message)

        decision = (0, 0, 0)
        if LIBRARY.sim_agent_begin(self.agent, ctypes.byref(self.observation)):
            self.decisions = []
            self.finished.clear()
            self.worker = threading.Thread(target=self.search, args=(start,), daemon=True)
            self.worker.start()
            self.finished.wait(max(LAST_DECISION_MS - (time.perf_counter() - start) * 1000, 0) / 1000)
            if self.finished.is_set():
                self.worker.join()
                self.worker = None
            else:
                LIBRARY.sim_agent_stop(self.agent)
                self.overran += 1
            with self.lock:
                decisions = list(self.decisions)
            if decisions:
                decision = decisions[-1]
                self.revised += decision != decisions[0]

        simulated = LIBRARY.sim_agent_last_simulated(self.agent)
        if simulated > 0:
            self.spent.append(simulated)
            self.spent_ms.append((time.perf_counter() - start) * 1000)
        if game_message.tick % 100 == 0 and self.spent:
            ordered = sorted(self.spent)
            print(
                f"BUDGET tick={game_message.tick} score={game_message.score} "
                f"simulated median={ordered[len(ordered) // 2]} min={ordered[0]} "
                f"move_ms max={max(self.spent_ms):.1f} revised={self.revised} overran={self.overran}",
                flush=True,
            )
            self.spent, self.spent_ms = [], []
            self.revised = self.overran = 0

        kind, x, y = decision
        if kind == 1:
            return [LaunchMeteorAction(target=WorldPosition(x=x, y=y))]
        if kind == 2:
            return [TriggerVolcanoAction(target=WorldPosition(x=x, y=y))]
        return []
