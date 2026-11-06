"""Open-loop MCTS through the compiled simulator, guided by a small value network.

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
PSI_CUT_MS = float(os.environ.get("BOT_PSI_CUT_MS", "0"))
PSI_THRESHOLD_MS = float(os.environ.get("BOT_PSI_THRESHOLD_MS", "20"))
# Freezes come in clusters, so the cut budget stays for this many ticks after a pressured one.
PSI_HOLD_TICKS = int(os.environ.get("BOT_PSI_HOLD_TICKS", "3"))
if PSI_CUT_MS < 0:
    raise ValueError("BOT_PSI_CUT_MS must be >= 0")
if PSI_THRESHOLD_MS < 0:
    raise ValueError("BOT_PSI_THRESHOLD_MS must be >= 0")
if PSI_HOLD_TICKS < 0:
    raise ValueError("BOT_PSI_HOLD_TICKS must be >= 0")


def pin_cpu() -> None:
    requested = os.environ.get("BOT_PIN_CPU", "")
    if not requested:
        print("PIN off", flush=True)
        return

    try:
        allowed = os.sched_getaffinity(0)
        if requested == "auto":
            with open("/proc/self/stat", encoding="ascii") as stat:
                fields = stat.read().rsplit(")", 1)[1].split()
            if len(fields) <= 36:
                raise ValueError("could not read current CPU from /proc/self/stat")
            cpu = int(fields[36])
        else:
            cpu = int(requested)
    except (AttributeError, OSError, ValueError) as exc:
        print(f"WARNING: CPU pinning unavailable: {exc}", file=sys.stderr, flush=True)
        print("PIN off", flush=True)
        return

    if cpu not in allowed:
        print(f"WARNING: CPU {cpu} is not in the allowed CPU set", file=sys.stderr, flush=True)
        print("PIN off", flush=True)
        return
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError as exc:
        print(f"WARNING: could not pin to CPU {cpu}: {exc}", file=sys.stderr, flush=True)
        print("PIN off", flush=True)
        return
    print(f"PIN cpu={cpu} allowed={sorted(allowed)}", flush=True)


pin_cpu()


def read_cpu_pressure_us() -> int:
    with open("/proc/pressure/cpu", encoding="ascii") as pressure:
        for line in pressure:
            if line.startswith("some "):
                for field in line.split():
                    if field.startswith("total="):
                        return int(field[6:])
                break
    raise ValueError("missing some total in /proc/pressure/cpu")


# The next tick normally arrives about 3 ms after we reply (p95 5.4 ms on the server). When the
# host stalls us between ticks, the gap beyond this grace is time the server's clock already spent,
# so the budget shrinks by it.
ARRIVAL_GRACE_MS = float(os.environ.get("BOT_ARRIVAL_GRACE_MS", "6"))
if ARRIVAL_GRACE_MS < 0:
    raise ValueError("BOT_ARRIVAL_GRACE_MS must be >= 0")
RADIUS, HORIZON, VOLCANOES, DEPTH = 2, 4, 3, 5
WIDENING, EXPLORATION = 3.0, 0.5
VALUE_WEIGHT = 0.3
POCKET_LIMIT, POCKET_TARGETS, POCKET_WEIGHT = 60, 4, 0.0


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
    library.sim_agent_set_volcano_rank.restype = None
    library.sim_agent_set_volcano_rank.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.sim_agent_set_waves.restype = None
    library.sim_agent_set_waves.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_double]
    library.sim_agent_last_simulated.restype = ctypes.c_int
    library.sim_agent_last_simulated.argtypes = [ctypes.c_void_p]
    if library.sim_observation_size() != ctypes.sizeof(Observation):
        raise RuntimeError("Observation layout differs between Python and C++")
    return library


LIBRARY = load_library()


class Bot:
    def __init__(self):
        self.agent = LIBRARY.sim_agent_create(RADIUS, HORIZON, VOLCANOES, DEPTH, WIDENING, EXPLORATION)
        net_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "value_net.bin")
        if not LIBRARY.sim_agent_load_net(self.agent, net_path.encode(), VALUE_WEIGHT):
            print("Could not load the value network, playing without it", flush=True)
        LIBRARY.sim_agent_set_waves(self.agent, POCKET_LIMIT, POCKET_TARGETS, POCKET_WEIGHT)
        LIBRARY.sim_agent_set_volcano_rank(self.agent, 1)
        self.observation = Observation()
        self.spent = []
        self.spent_ms = []
        self.lagged = 0    # Ticks that arrived late, and the worst lateness.
        self.max_lag_ms = 0.0
        self.psi_enabled = PSI_CUT_MS > 0
        self.previous_psi_us = None
        self.pressured_until_tick = -1
        self.psi_cut = 0
        self.max_psi_ms = 0.0
        self.replied_at = None

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
        actions = self.decide(game_message)
        self.replied_at = time.perf_counter()
        return actions

    def decide(self, game_message: TeamGameState) -> list[Action]:
        arrived = time.perf_counter()
        tick = game_message.tick
        if self.psi_enabled:
            try:
                pressure_us = read_cpu_pressure_us()
            except (OSError, ValueError) as exc:
                print(f"WARNING: CPU pressure monitoring disabled: {exc}", file=sys.stderr, flush=True)
                self.psi_enabled = False
                self.previous_psi_us = None
            else:
                if self.previous_psi_us is not None:
                    psi_ms = max(pressure_us - self.previous_psi_us, 0) / 1000
                    self.max_psi_ms = max(self.max_psi_ms, psi_ms)
                    if psi_ms >= PSI_THRESHOLD_MS:
                        self.pressured_until_tick = tick + PSI_HOLD_TICKS
                self.previous_psi_us = pressure_us
        lag_ms = self.arrival_lag_ms(arrived)
        if lag_ms > 0:
            self.lagged += 1
            self.max_lag_ms = max(self.max_lag_ms, lag_ms)
        # The budget is measured from when the server most likely sent the tick.
        start = arrived - lag_ms / 1000
        self.fill(game_message)

        budget_ms = TIME_BUDGET_MS
        if PSI_CUT_MS > 0 and tick <= self.pressured_until_tick:
            budget_ms = min(TIME_BUDGET_MS, PSI_CUT_MS)
            self.psi_cut += 1
        remaining = budget_ms - (time.perf_counter() - start) * 1000
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
                f"move_ms max={max(self.spent_ms):.1f} "
                f"lagged={self.lagged} max_lag_ms={self.max_lag_ms:.1f} "
                f"psi_cut={self.psi_cut} max_psi_ms={self.max_psi_ms:.1f}",
                flush=True,
            )
            self.spent, self.spent_ms = [], []
            self.lagged = 0
            self.max_lag_ms = 0.0
            self.psi_cut = 0
            self.max_psi_ms = 0.0

        if kind.value == 1:
            return [LaunchMeteorAction(target=WorldPosition(x=x.value, y=y.value))]
        if kind.value == 2:
            return [TriggerVolcanoAction(target=WorldPosition(x=x.value, y=y.value))]
        return []
