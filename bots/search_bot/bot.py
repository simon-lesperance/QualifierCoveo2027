"""Open-loop MCTS through the compiled simulator, guided by a small value network.

The library is built by tools/build_sim.py and loaded here at import time, which
runs before the bot connects. Set BOT_TIME_MS to test with a different time budget. The
phase-aware budget is on by default (big budget 120 ms); set BOT_PHASE_BIG_MS=0 for the
fixed budget.
"""

import ctypes
import os
import sys
import time

from game_message import *
from phase_budget import PhaseBudget

WIDTH = 20
CELLS = WIDTH * WIDTH
MAX_DINOS = 20
MAX_METEORS = 3
MAX_VOLCANOES = 1

IMPASSABLE, MOUNTAIN, LAVA = 1, 2, 4
SPECIES = {"Stegosaurus": 0, "Velociraptor": 1, "Triceratops": 2, "Tyrannosaurus": 3}

# The limit is 150 ms, but even a trivial bot sees spikes of up to 65 ms.
TIME_BUDGET_MS = float(os.environ.get("BOT_TIME_MS", "95"))
# The next tick normally arrives about 3 ms after we reply (p95 5.4 ms on the server). When the
# host stalls us between ticks, the gap beyond this grace is time the server's clock already spent,
# so the budget shrinks by it.
ARRIVAL_GRACE_MS = float(os.environ.get("BOT_ARRIVAL_GRACE_MS", "6"))
# Phase-aware budget (phase_budget.py, docs/notes/phase-budget.md). 0 keeps the fixed budget;
# a larger value is the budget for ticks whose deadline can't meet a predicted late freeze.
PHASE_BIG_MS = float(os.environ.get("BOT_PHASE_BIG_MS", "0"))
PHASE_CUT_MS = float(os.environ.get("BOT_PHASE_CUT_MS", "85"))
PHASE_TRACE = os.environ.get("BOT_PHASE_TRACE", "1") == "1"
MAX_GAPS = 16
# Only volcanic_plains has reached 85,000 in tests, so games on other maps end early: late replies
# get the bot kicked after 50 ticks, which voids the game. Set BOT_SKIP_OTHER_MAPS=0 to play them.
SKIP_OTHER_MAPS = os.environ.get("BOT_SKIP_OTHER_MAPS", "1") == "1"
SKIP_DELAY_S = 0.2
VOLCANIC_MOUNTAIN_MIN = 18  # The lowest mountain tile is 13 on deep_crevasse and 16 on mountain_range.
RADIUS, HORIZON, VOLCANOES, DEPTH = 2, 4, 3, 5
WIDENING, EXPLORATION = 1.5, 0.35
VALUE_WEIGHT = 0.4
POCKET_LIMIT, POCKET_TARGETS, POCKET_WEIGHT = 60, 4, 0.0
POLICY_PUCT = 0.3  # Used only if policy_net.bin is in this folder.
ROOT_HALVING = 16  # Root actions tried by sequential halving; 0 keeps UCT at the root.
TREE_DEPTH, TREE_VISITS = 1, 0  # Used only if tree_net.bin is in this folder too.


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
    library.sim_agent_create.argtypes = [ctypes.c_int] * 4 + [ctypes.c_double] * 2
    library.sim_agent_act.restype = None
    library.sim_agent_act.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(Observation), ctypes.c_double,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    library.sim_agent_load_net.restype = ctypes.c_int
    library.sim_agent_load_net.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
    library.sim_agent_load_policy.restype = ctypes.c_int
    library.sim_agent_load_policy.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
    library.sim_agent_load_tree_policy.restype = ctypes.c_int
    library.sim_agent_load_tree_policy.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    library.sim_agent_set_root_halving.restype = None
    library.sim_agent_set_root_halving.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_double]
    library.sim_agent_set_volcano_rank.restype = None
    library.sim_agent_set_volcano_rank.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.sim_agent_set_waves.restype = None
    library.sim_agent_set_waves.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_double]
    library.sim_agent_last_simulated.restype = ctypes.c_int
    library.sim_agent_last_simulated.argtypes = [ctypes.c_void_p]
    library.sim_agent_gaps.restype = ctypes.c_int
    library.sim_agent_gaps.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.c_int,
    ]
    library.sim_now_ms.restype = ctypes.c_double
    library.sim_now_ms.argtypes = []
    if library.sim_observation_size() != ctypes.sizeof(Observation):
        raise RuntimeError("Observation layout differs between Python and C++")
    return library


LIBRARY = load_library()


def clock_offset_ms() -> float:
    """perf_counter milliseconds minus the library's clock, from the tightest of a few reads."""
    best = None
    for _ in range(20):
        before = time.perf_counter() * 1000
        native = LIBRARY.sim_now_ms()
        after = time.perf_counter() * 1000
        if best is None or after - before < best[0]:
            best = (after - before, (before + after) / 2 - native)
    return best[1]


CLOCK_OFFSET_MS = clock_offset_ms()


def is_volcanic(game_message: TeamGameState) -> bool:
    """Reads the map from the first frame. Matched all 193 server games with a known biome."""
    heights = [tile.elevation for row in game_message.map.tiles for tile in row if tile.isMountain]
    return bool(heights) and min(heights) >= VOLCANIC_MOUNTAIN_MIN


class Bot:
    def __init__(self):
        self.agent = LIBRARY.sim_agent_create(RADIUS, HORIZON, VOLCANOES, DEPTH, WIDENING, EXPLORATION)
        net_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "value_net.bin")
        if not LIBRARY.sim_agent_load_net(self.agent, net_path.encode(), VALUE_WEIGHT):
            print("Could not load the value network, playing without it", flush=True)
        policy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy_net.bin")
        if os.path.exists(policy_path):
            loaded = LIBRARY.sim_agent_load_policy(self.agent, policy_path.encode(), POLICY_PUCT)
            print(f"POLICY loaded={loaded} puct={POLICY_PUCT}", flush=True)
            tree_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tree_net.bin")
            if os.path.exists(tree_path):
                loaded = LIBRARY.sim_agent_load_tree_policy(self.agent, tree_path.encode(), TREE_DEPTH, TREE_VISITS)
                print(f"TREE loaded={loaded} depth={TREE_DEPTH} visits={TREE_VISITS}", flush=True)
        LIBRARY.sim_agent_set_waves(self.agent, POCKET_LIMIT, POCKET_TARGETS, POCKET_WEIGHT)
        LIBRARY.sim_agent_set_volcano_rank(self.agent, 1)
        LIBRARY.sim_agent_set_root_halving(self.agent, ROOT_HALVING, 0.0)
        print(f"HALVING actions={ROOT_HALVING}", flush=True)
        self.observation = Observation()
        self.spent = []
        self.spent_ms = []
        self.replied_at = None
        self.lagged = 0    # Ticks that arrived late, and the worst lateness.
        self.skipping = False
        self.phase = None
        if PHASE_BIG_MS > 0:
            self.phase = PhaseBudget(
                base_ms=TIME_BUDGET_MS, big_ms=PHASE_BIG_MS, cut_ms=min(PHASE_CUT_MS, TIME_BUDGET_MS),
            )
        self.gap_starts = (ctypes.c_double * MAX_GAPS)()
        self.gap_ends = (ctypes.c_double * MAX_GAPS)()
        self.max_lag_ms = 0.0

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

    def arrival_lag_ms(self, now: float) -> float:
        """How much later than usual this tick arrived after our previous reply."""
        if self.replied_at is None:
            return 0.0
        return max((now - self.replied_at) * 1000 - ARRIVAL_GRACE_MS, 0.0)

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        if SKIP_OTHER_MAPS and game_message.tick <= 1 and not is_volcanic(game_message):
            self.skipping = True
            print("SKIP this map", flush=True)
        if self.skipping:
            time.sleep(SKIP_DELAY_S)
            return []
        actions = self.decide(game_message)
        self.replied_at = time.perf_counter()
        return actions

    def decide(self, game_message: TeamGameState) -> list[Action]:
        arrived = time.perf_counter()
        thread_arrived = time.thread_time()
        lag_ms = self.arrival_lag_ms(arrived)
        if lag_ms > 0:
            self.lagged += 1
            self.max_lag_ms = max(self.max_lag_ms, lag_ms)
        start = arrived - lag_ms / 1000  # Measured from when the server most likely sent the tick.
        phase = self.phase
        if phase is not None and self.replied_at is not None:
            phase.observe_wait(self.replied_at * 1000, arrived * 1000, lag_ms)
        self.fill(game_message)

        budget_ms = TIME_BUDGET_MS
        if phase is not None:
            budget_ms = phase.budget(start * 1000, time.perf_counter() * 1000)
        remaining = budget_ms - (time.perf_counter() - start) * 1000
        kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        LIBRARY.sim_agent_act(
            self.agent, ctypes.byref(self.observation), max(remaining, 1.0),
            ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y),
        )

        if phase is not None:
            ended = time.perf_counter()
            off_ms = max((ended - arrived) - (time.thread_time() - thread_arrived), 0.0) * 1000
            count = min(LIBRARY.sim_agent_gaps(self.agent, self.gap_starts, self.gap_ends, MAX_GAPS), MAX_GAPS)
            gaps = [
                (self.gap_starts[i] + CLOCK_OFFSET_MS, self.gap_ends[i] + CLOCK_OFFSET_MS) for i in range(count)
            ]
            overrun_ms = (ended - start) * 1000 - budget_ms
            phase.observe_search(arrived * 1000, ended * 1000, gaps, off_ms, overrun_ms)
            phase.finish(start * 1000, ended * 1000, budget_ms)
            if PHASE_TRACE:
                print(
                    f"PHASE tick={game_message.tick} start={start * 1000:.1f} arrived={arrived * 1000:.1f} "
                    f"budget={budget_ms:.1f} end={ended * 1000:.1f} off={off_ms:.1f} "
                    f"gaps={';'.join(f'{a:.1f}-{b:.1f}' for a, b in gaps)}",
                    flush=True,
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
                f"move_ms max={max(self.spent_ms):.1f} "
                f"lagged={self.lagged} max_lag_ms={self.max_lag_ms:.1f}"
                + (f" {self.phase.summary()}" if self.phase is not None else ""),
                flush=True,
            )
            if self.phase is not None:
                self.phase.reset_stats()
            self.spent, self.spent_ms = [], []
            self.lagged, self.max_lag_ms = 0, 0.0

        if kind.value == 1:
            return [LaunchMeteorAction(target=WorldPosition(x=x.value, y=y.value))]
        if kind.value == 2:
            return [TriggerVolcanoAction(target=WorldPosition(x=x.value, y=y.value))]
        return []
