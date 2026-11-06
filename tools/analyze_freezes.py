"""Reproduce the server timing analysis from collect_game_logs.py's archive.

Requires numpy, pandas, scipy, statsmodels and matplotlib in the analysis environment.
Raw CPU telemetry exists only in env-probe logs; ordinary reply tails are proxies,
not measurements of CPU starvation. Run with --help for paths and sampling controls.
"""

import argparse
import csv
import json
import platform
import re
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy import signal, stats
from statsmodels.stats.multitest import multipletests

from platform_paths import ROOT

TIMESTAMP = re.compile(r"^\[\w+\] ([\dT:.\-]+Z): (.*)")
PLAY = re.compile(r"Playing tick (\d+)\.")
REPLY = re.compile(r"Command received .* for tick (\d+) after ([\d.]+) ms")
TIMEOUT = re.compile(r"No command was received in time .* on tick (\d+)")
PROBE = re.compile(r"^TICK (\d+) ")
KEY_VALUE = re.compile(r"(\w+)=([-\d.]+)")
VARIANTS = {
    **dict.fromkeys([24278, 24279, 24280, 24291, 24292, 24293], "baseline95"),
    **dict.fromkeys([24282, 24283, 24284], "pin95"),
    **dict.fromkeys([24295, 24296, 24297], "cut85_hold1"),
    **dict.fromkeys([24299, 24300, 24301], "cut85_hold3"),
    **dict.fromkeys([24303, 24304, 24305], "cut70_hold1"),
    **dict.fromkeys([24286, 24287, 24288], "cut70_hold3"),
    **dict.fromkeys([24307, 24308, 24309], "cut60_hold0"),
    24273: "env_probe",
    24276: "arrival_lag_anytime",
}


def epoch_ms(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def parse_server(text: str) -> dict[int, dict]:
    ticks = {}

    def row(tick: str) -> dict:
        return ticks.setdefault(int(tick), {
            "tick": int(tick), "timeout": False, "socket_error": False,
            "ignored_commands": 0, "accepted_commands": 0,
        })

    for line in text.splitlines():
        match = TIMESTAMP.match(line)
        if not match:
            continue
        timestamp, message = match.groups()
        when = epoch_ms(timestamp)
        if match := PLAY.search(message):
            row(match[1])["server_start_epoch_ms"] = when
        elif match := REPLY.search(message):
            item = row(match[1])
            item["accepted_commands"] += 1
            if "reply_ms" not in item:
                item.update(reply_ms=float(match[2]), reply_epoch_ms=when)
        elif match := TIMEOUT.search(message):
            row(match[1]).update(timeout=True, timeout_epoch_ms=when)
        elif "Socket connection lost" in message:
            if match := re.search(r"for tick (\d+)", message):
                row(match[1])["socket_error"] = True
        elif match := re.search(r"Command for tick (\d+) ignored", message):
            row(match[1])["ignored_commands"] += 1
    return ticks


def parse_probe(line: str) -> dict:
    fields = line.split()
    if len(fields) != 19:
        raise ValueError(f"unrecognized probe record ({len(fields)} fields): {line}")
    stall, offset = fields[5].split("@")
    throttle_count, throttle_time = fields[13].split("/")
    return {
        "tick": int(fields[1]), "bot_wall_epoch_ms": float(fields[2]),
        "decision_ms": float(fields[3]), "cycle_ms": float(fields[4]),
        "max_stall_ms": float(stall), "stall_offset_ms": float(offset),
        "stalls_over3_count": int(fields[6]), "worker_run_ms": float(fields[7]),
        "worker_wait_ms": float(fields[8]), "steal_ms": float(fields[9]),
        "host_busy_ms": float(fields[10]), "own_cpu_ms": float(fields[11]),
        "psi_ms": float(fields[12]) / 1000, "throttle_count": int(throttle_count),
        "throttle_time_raw": int(throttle_time), "involuntary_switches": int(fields[14]),
        "minor_faults": int(fields[15]), "gc_ms": float(fields[16]),
        "library": fields[17], "simulations": int(fields[18]),
    }


def read_replay(path: Path) -> dict[int, dict]:
    rows = {}
    with path.open() as file:
        for line in file:
            state = json.loads(line)
            tick = int(state["currentTick"])
            if tick in rows:
                continue  # Initial/final duplicate states must not double-count ticks.
            rows[tick] = {
                "dino_count": len(state.get("dinosaurs", [])),
                "meteor_count": len(state.get("meteors", [])),
                "volcano_count": len(state.get("volcanoes", [])),
                "corpse_count": len(state.get("corpses", [])),
                "origin_y": state["map"]["origin"]["y"],
                "score_at_state": state.get("score"),
                "total_kills": state.get("totalKills"),
                "map_shift_interval": state["constants"]["mapShiftInterval"],
                "meteor_explosions": len(state.get("meteorExplosions", [])),
            }
    return rows


def extract(archive: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with (archive / "server-games.tsv").open() as file:
        inventory = list(csv.DictReader(file, delimiter="\t"))
    all_ticks, all_games, budgets = [], [], []
    for game in inventory:
        game_id = int(game["gameId"])
        folder = archive / "raw" / str(game_id)
        if not all((folder / name).exists() for name in ("bot.log", "game.log", "replay.jsonl")):
            all_games.append({"game_id": game_id, "missing_logs": True})
            continue
        server = (folder / "game.log").read_text()
        bot = (folder / "bot.log").read_text()
        rows = parse_server(server)
        replay = read_replay(folder / "replay.jsonl")
        for line in bot.splitlines():
            if PROBE.match(line):
                item = parse_probe(line)
                if item["tick"] in rows:
                    rows[item["tick"]].update(item)
            if line.startswith("BUDGET "):
                budgets.append({"game_id": game_id, **{
                    key: float(value) for key, value in KEY_VALUE.findall(line)
                }})
        biome = re.search(r"Map biome: (\w+)", server)
        seed = re.search(r"Random seed: (\S+)", server)
        probe_ticks = sum("cycle_ms" in row for row in rows.values())
        has_manual_probe = "STATE " in bot or "STALL " in bot or "stall_test" in bot.lower()
        game_info = {
            "game_id": game_id, "variant": VARIANTS.get(game_id, "historical_unknown"),
            "game_time_utc": game["gameTime"], "inventory_score": int(game["score"]),
            "kicked_or_disconnected": game["timedOut"] == "true",
            "biome": biome[1] if biome else "", "seed": seed[1] if seed else "",
            "missing_logs": False, "server_ticks": len(rows), "probe_ticks": probe_ticks,
            "socket_error_ticks": sum(item["socket_error"] for item in rows.values()),
            "timeouts": sum(item["timeout"] for item in rows.values()),
            "manual_probe_possible": has_manual_probe,
        }
        all_games.append(game_info)
        previous, previous_state = None, None
        for tick, item in sorted(rows.items()):
            item.update(game_id=game_id, variant=game_info["variant"], biome=game_info["biome"])
            item.update(replay.get(tick, {}))
            item["has_probe"] = "cycle_ms" in item
            if previous is not None and tick == previous["tick"] + 1:
                if "server_start_epoch_ms" in previous and "server_start_epoch_ms" in item:
                    previous["server_cycle_ms"] = (
                        item["server_start_epoch_ms"] - previous["server_start_epoch_ms"]
                    )
                end = previous.get("reply_epoch_ms", previous.get("timeout_epoch_ms"))
                if end is not None and "server_start_epoch_ms" in item:
                    previous["server_post_reply_ms"] = item["server_start_epoch_ms"] - end
                if previous_state and "origin_y" in item:
                    item["map_shifted"] = item["origin_y"] != previous_state["origin_y"]
                    item["dino_count_delta"] = item["dino_count"] - previous_state["dino_count"]
            if "reply_ms" in item and "server_start_epoch_ms" in item:
                item["server_elapsed_ms"] = item["reply_epoch_ms"] - item["server_start_epoch_ms"]
                item["server_pre_send_ms"] = item["server_elapsed_ms"] - item["reply_ms"]
                if item["has_probe"]:
                    item["arrival_after_send_ms"] = (
                        item["bot_wall_epoch_ms"] - item["reply_epoch_ms"] + item["reply_ms"]
                    )
            if item["has_probe"] and "server_start_epoch_ms" in item:
                item["arrival_after_play_log_ms"] = (
                    item["bot_wall_epoch_ms"] - item["server_start_epoch_ms"]
                )
            if item["has_probe"]:
                item["post_decision_gap_ms"] = item["cycle_ms"] - item["decision_ms"]
                offset, stall = item["stall_offset_ms"], item["max_stall_ms"]
                if offset < 0 or offset + stall > item["cycle_ms"] + 2:
                    phase = "boundary_or_unaligned"
                elif offset >= item["decision_ms"]:
                    phase = "after_decision"
                elif offset + stall <= item["decision_ms"]:
                    phase = "during_decision"
                else:
                    phase = "overlaps_decision_end"
                item["stall_phase"] = phase
                item["stall20"] = stall >= 20
                item["pressure20"] = item["psi_ms"] >= 20
            previous, previous_state = item, replay.get(tick)
            all_ticks.append(item)
    ticks, games, budget = pd.DataFrame(all_ticks), pd.DataFrame(all_games), pd.DataFrame(budgets)
    ticks = ticks.sort_values(["game_id", "tick"]).reset_index(drop=True)
    ticks["responded"] = ticks.reply_ms.notna()
    windows = (ticks.responded | ticks.timeout).groupby(ticks.game_id).transform("sum")
    ticks["responsive_game"] = ticks.groupby("game_id").responded.transform("sum") / windows >= 0.9
    ticks["late110"] = ticks.reply_ms.gt(110) | (ticks.timeout & ~ticks.socket_error)
    ticks["late130"] = ticks.reply_ms.gt(130) | (ticks.timeout & ~ticks.socket_error)
    ticks["analysis_eligible"] = (
        ticks.responsive_game & ~ticks.socket_error & (ticks.responded | ticks.timeout)
    )
    # Controlled probes intentionally sleep late in the match. Do not mix these with freezes.
    manual_games = set(games.loc[games.manual_probe_possible.fillna(False), "game_id"])
    ticks.loc[ticks.game_id.isin(manual_games) & ticks.tick.ge(899), "analysis_eligible"] = False
    ticks["exclusion_reason"] = ""
    ticks.loc[~ticks.responsive_game, "exclusion_reason"] = "under_90_percent_response_coverage"
    ticks.loc[ticks.socket_error, "exclusion_reason"] = "socket_disconnected"
    ticks.loc[~ticks.responded & ~ticks.timeout, "exclusion_reason"] = "no_command_window"
    ticks.loc[ticks.game_id.isin(manual_games) & ticks.tick.ge(899), "exclusion_reason"] = (
        "controlled_delay_probe_tail"
    )
    ticks["tick_decile"] = ((ticks.tick - 1) // 100).clip(upper=9)
    ticks["dispatch_gap20"] = ticks.server_pre_send_ms.ge(20)
    ticks["post_reply_gap20"] = ticks.server_post_reply_ms.ge(20)
    for lag in (1, 2, 3, 5, 10):
        group = ticks.groupby("game_id")
        for column in ("late110", "late130", "reply_ms", "dino_count", "meteor_explosions"):
            ticks[f"prev{lag}_{column}"] = group[column].shift(lag)
    summary = ticks.groupby("game_id").agg(
        eligible_ticks=("analysis_eligible", "sum"),
        accepted_reply_count=("responded", "sum"),
        accepted_reply_median_ms=("reply_ms", "median"),
        accepted_reply_max_ms=("reply_ms", "max"),
        late110_count=("late110", "sum"), late130_count=("late130", "sum"),
    ).reset_index()
    games = games.merge(summary, on="game_id", how="left")
    return ticks, games, budget


def probe_episodes(probe: pd.DataFrame, output: Path) -> dict:
    events = probe[probe.max_stall_ms.ge(20)].copy()
    events["sampled_stall_start_ms"] = events.bot_wall_epoch_ms + events.stall_offset_ms
    events["sampled_stall_end_ms"] = events.sampled_stall_start_ms + events.max_stall_ms
    events["episode"] = events.sampled_stall_start_ms.diff().gt(500).cumsum()
    events.to_csv(output / "sampled-freezes.csv", index=False)
    episodes = events.groupby("episode").agg(
        first_tick=("tick", "min"), last_tick=("tick", "max"),
        start_epoch_ms=("sampled_stall_start_ms", "min"),
        end_epoch_ms=("sampled_stall_end_ms", "max"),
        affected_cycles=("tick", "size"), sampled_gap_total_ms=("max_stall_ms", "sum"),
    ).reset_index()
    episodes["span_ms"] = episodes.end_epoch_ms - episodes.start_epoch_ms
    episodes["since_previous_start_ms"] = episodes.start_epoch_ms.diff()
    episodes["sampled_gap_fraction"] = episodes.sampled_gap_total_ms / episodes.span_ms
    episodes.to_csv(output / "freeze-episodes.csv", index=False)
    substantial = episodes[episodes.affected_cycles.ge(3)]
    spacing = events.sampled_stall_start_ms.diff()
    return {
        "episodes": len(episodes), "episodes_at_least3_cycles": len(substantial),
        "span_ms_at_least3_cycles": describe(substantial.span_ms),
        "start_spacing_ms_at_least3_cycles": describe(substantial.start_epoch_ms.diff()),
        "sampled_gap_fraction_at_least3_cycles": describe(substantial.sampled_gap_fraction),
        "within_episode_sample_start_spacing_ms": describe(spacing[spacing.lt(500)]),
    }


def spectral_test(frame: pd.DataFrame, event: str, rng, repetitions: int) -> dict:
    """Scan 2–10 second periods against a short-block permutation null.

    Permuting 10-tick blocks retains local clustering while breaking long cadence.
    The null maximum across the entire frequency scan corrects frequency selection.
    """
    times = (frame.server_start_epoch_ms.to_numpy() - frame.server_start_epoch_ms.iloc[0]) / 1000
    flags = frame[event].to_numpy(dtype=float)
    frequencies = np.linspace(0.1, 0.5, 401)
    angles = 2 * np.pi * frequencies[:, None] * times
    # Centering the basis corrects for non-uniform observation phases.
    basis = np.exp(1j * angles)
    basis -= basis.mean(axis=1, keepdims=True)
    scale = np.sum(np.abs(basis) ** 2, axis=1)
    scores = np.abs(basis @ flags) ** 2 / scale
    blocks = [np.arange(i, min(i + 10, len(flags))) for i in range(0, len(flags), 10)]
    maximums, fixed = [], []
    for _ in range(repetitions):
        order = np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        null = np.abs(basis @ flags[order]) ** 2 / scale
        maximums.append(null.max())
        fixed.append(null[100])  # 0.2 Hz = 5 seconds, specified by observed probe cadence.
    best = int(np.argmax(scores))
    return {
        "event": event, "ticks": len(frame), "events": int(flags.sum()),
        "best_period_ms": float(1000 / frequencies[best]), "peak_statistic": float(scores[best]),
        "scan_corrected_p": float((1 + np.sum(np.asarray(maximums) >= scores[best]))
                                  / (repetitions + 1)),
        "five_second_statistic": float(scores[100]),
        "five_second_p": float((1 + np.sum(np.asarray(fixed) >= scores[100]))
                              / (repetitions + 1)),
    }


def fixed_cadence_test(frame, event, rng, repetitions):
    observed = 0.0
    null = np.zeros(repetitions)
    games_used = 0
    for _, part in frame.groupby("game_id"):
        flags = part[event].to_numpy(dtype=float)
        if flags.sum() < 3 or len(flags) < 100:
            continue
        times = part.server_start_epoch_ms.to_numpy() / 1000
        basis = np.exp(2j * np.pi * 0.2 * (times - times[0]))
        basis -= basis.mean()
        scale = np.sum(np.abs(basis) ** 2)
        observed += abs(basis @ flags) ** 2 / scale
        blocks = [np.arange(i, min(i + 10, len(flags))) for i in range(0, len(flags), 10)]
        for i in range(repetitions):
            order = np.concatenate([blocks[j] for j in rng.permutation(len(blocks))])
            null[i] += abs(basis @ flags[order]) ** 2 / scale
        games_used += 1
    return {
        "event": event, "games": games_used, "period_ms": 5000,
        "statistic": observed,
        "block_permutation_p": float((1 + np.sum(null >= observed)) / (repetitions + 1)),
    }


def phase_alignment(frame, output, rng):
    rows = []
    for game_id, part in frame.groupby("game_id"):
        flags = part.dispatch_gap20.to_numpy(dtype=float)
        if flags.sum() < 3 or len(flags) < 100:
            continue
        times = part.server_start_epoch_ms.to_numpy() / 1000
        for reference, values in (("utc", times % 5), ("game_start", times - times[0])):
            basis = np.exp(2j * np.pi * 0.2 * values)
            basis -= basis.mean()
            phase = np.angle(basis @ flags) % (2 * np.pi)
            rows.append({"game_id": game_id, "reference": reference,
                         "events": int(flags.sum()), "preferred_phase_ms": phase * 5000 / (2 * np.pi)})
    data = pd.DataFrame(rows)
    data.to_csv(output / "five-second-phases.csv", index=False)
    results = []
    for reference, group in data.groupby("reference"):
        angles = group.preferred_phase_ms.to_numpy() * 2 * np.pi / 5000
        vector = np.exp(1j * angles).mean()
        null = np.abs(np.exp(1j * rng.uniform(
            0, 2 * np.pi, (19999, len(angles))
        )).mean(axis=1))
        results.append({
            "reference": reference, "games": len(group), "phase_concentration_R": float(abs(vector)),
            "mean_phase_ms": float(np.angle(vector) % (2 * np.pi) * 5000 / (2 * np.pi)),
            "uniform_phase_p": float((1 + np.sum(null >= abs(vector))) / 20000),
        })
    return results


def timing_model(modern, output):
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    frame = modern.dropna(subset=["map_shifted", "dino_count", "meteor_explosions"]).copy()
    frame["outcome"] = frame.late110.astype(int)
    frame["progress"] = frame.tick / 1000
    frame["map_shifted"] = frame.map_shifted.astype(int)
    frame["previous_idle"] = frame.prev1_reply_ms.le(5).astype(int)
    fit = smf.glm(
        "outcome ~ progress + dino_count + map_shifted + meteor_explosions"
        " + previous_idle + C(variant)",
        frame, family=sm.families.Binomial(),
    ).fit(cov_type="cluster", cov_kwds={"groups": frame.game_id})
    ci = fit.conf_int()
    rows = []
    for name in ("progress", "dino_count", "map_shifted", "meteor_explosions", "previous_idle"):
        rows.append({
            "predictor": name, "odds_ratio": float(np.exp(fit.params[name])),
            "ci_low": float(np.exp(ci.loc[name, 0])), "ci_high": float(np.exp(ci.loc[name, 1])),
            "cluster_robust_p": float(fit.pvalues[name]), "ticks": len(frame),
            "games": frame.game_id.nunique(), "events": int(frame.outcome.sum()),
        })
    adjusted = multipletests([row["cluster_robust_p"] for row in rows], method="holm")[1]
    for row, p in zip(rows, adjusted):
        row["holm_p"] = float(p)
    pd.DataFrame(rows).to_csv(output / "timing-model.csv", index=False)
    return rows


def clustering_test(probe, rng, repetitions):
    flags = probe.stall20.to_numpy(dtype=bool)
    observed = int(np.sum(flags[:-1] & flags[1:]))
    groups = list(probe.groupby("tick_decile").indices.values())
    null = []
    for _ in range(repetitions):
        shuffled = flags.copy()
        for index in groups:
            shuffled[index] = rng.permutation(flags[index])
        null.append(np.sum(shuffled[:-1] & shuffled[1:]))
    return {
        "adjacent_stall_pairs": observed,
        "null_mean_pairs": float(np.mean(null)),
        "null_p95_pairs": float(np.percentile(null, 95)),
        "within_decile_permutation_p": float(
            (1 + np.sum(np.asarray(null) >= observed)) / (repetitions + 1)
        ),
    }


def describe(values) -> dict:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"n": 0}
    return {
        "n": int(len(values)), "mean": float(values.mean()), "std": float(values.std()),
        **{f"p{q}": float(np.percentile(values, q)) for q in (10, 25, 50, 75, 90, 95, 99)},
        "min": float(values.min()), "max": float(values.max()),
    }


def block_interval(frame: pd.DataFrame, column: str, rng, repetitions: int) -> tuple[float, float]:
    """Hierarchical game bootstrap, then circular 20-tick blocks within sampled games."""
    groups = [part[column].to_numpy(dtype=float) for _, part in frame.groupby("game_id")]
    means = []
    for _ in range(repetitions):
        samples = []
        for index in rng.integers(len(groups), size=len(groups)):
            values = groups[index]
            starts = rng.integers(len(values), size=(len(values) + 19) // 20)
            indices = ((starts[:, None] + np.arange(20)) % len(values)).ravel()[:len(values)]
            samples.append(values[indices])
        means.append(np.nanmean(np.concatenate(samples)))
    return tuple(float(value) for value in np.percentile(means, [2.5, 97.5]))


def conditional_effect(source: np.ndarray, outcome: np.ndarray) -> tuple:
    source, outcome = source.astype(bool), outcome.astype(bool)
    n1, n0 = source.sum(), (~source).sum()
    if not n1 or not n0:
        return np.nan, np.nan, np.nan
    p1, p0 = outcome[source].mean(), outcome[~source].mean()
    return float(p1), float(p0), float(p1 - p0)


def lag_test(frame, source, outcome, lag, rng, repetitions):
    pairs = []
    for _, part in frame.groupby("game_id"):
        part = part.sort_values("tick")
        a = part[source].to_numpy(dtype=bool)
        b = part[outcome].to_numpy(dtype=bool)
        ticks = part.tick.to_numpy()
        if len(a) <= lag + 1:
            continue
        left, right = (a[:-lag], b[lag:]) if lag else (a, b)
        valid = ticks[lag:] - ticks[:-lag] == lag if lag else np.ones(len(a), dtype=bool)
        pairs.append((left[valid], right[valid]))
    if not pairs:
        return {}
    x, y = (np.concatenate([pair[i] for pair in pairs]) for i in (0, 1))
    p1, p0, observed = conditional_effect(x, y)
    null = []
    # Shift each outcome series, preserving its runs and event count within each game.
    for _ in range(repetitions):
        shifted = np.concatenate([np.roll(b, rng.integers(len(b))) for _, b in pairs])
        null.append(conditional_effect(x, shifted)[2])
    pvalue = (1 + np.sum(np.abs(null) >= abs(observed))) / (repetitions + 1)
    return {
        "source": source, "outcome": outcome, "lag_ticks": lag, "n_pairs": len(x),
        "n_exposed": int(x.sum()), "p_after_event": p1, "p_after_no_event": p0,
        "risk_difference": observed, "risk_ratio": p1 / p0 if p0 else None,
        "permutation_p": pvalue,
    }


def run_lengths(frame, column) -> list[int]:
    lengths = []
    for _, part in frame.groupby("game_id"):
        length, last = 0, -2
        for tick, flag in zip(part.tick, part[column]):
            if tick != last + 1 and length:
                lengths.append(length)
                length = 0
            if flag:
                length += 1
            elif length:
                lengths.append(length)
                length = 0
            last = tick
        if length:
            lengths.append(length)
    return lengths


def compare_variants(ticks, rng, repetitions):
    arms = ticks[ticks.variant.isin(["baseline95", "pin95", "cut85_hold1", "cut85_hold3",
                                    "cut70_hold1", "cut70_hold3", "cut60_hold0"])]
    metrics, comparisons = [], []
    for name, part in arms.groupby("variant"):
        row = {
            "variant": name, "games": part.game_id.nunique(), "ticks": len(part),
            "timeouts": int(part.timeout.sum()), "over110": int(part.reply_ms.gt(110).sum()),
            "over130": int(part.reply_ms.gt(130).sum()),
            "over140": int(part.reply_ms.gt(140).sum()),
            **{f"reply_{key}": value for key, value in describe(part.reply_ms).items()},
        }
        row["late130_rate"] = part.late130.mean()
        row["rate_ci_low"], row["rate_ci_high"] = block_interval(part, "late130", rng, repetitions)
        metrics.append(row)
    per_game = arms.groupby(["game_id", "variant"]).late130.mean().reset_index()
    baseline = per_game[per_game.variant == "baseline95"].late130.to_numpy()
    for name in sorted(set(per_game.variant) - {"baseline95"}):
        arm = per_game[per_game.variant == name].late130.to_numpy()
        # Games, not individual ticks, are the units assigned a configuration.
        test = stats.permutation_test(
            (arm, baseline), lambda a, b: np.mean(a) - np.mean(b),
            permutation_type="independent", n_resamples=repetitions,
            alternative="two-sided", rng=rng,
        )
        comparisons.append({
            "variant": name, "baseline_games": len(baseline), "variant_games": len(arm),
            "rate_difference": float(arm.mean() - baseline.mean()),
            "permutation_p": float(test.pvalue),
        })
    if comparisons:
        adjusted = multipletests([row["permutation_p"] for row in comparisons], method="holm")[1]
        for row, p in zip(comparisons, adjusted):
            row["holm_p"] = float(p)
    return pd.DataFrame(metrics), pd.DataFrame(comparisons)


def analyze(ticks, games, budget, output, repetitions, seed):
    rng = np.random.default_rng(seed)
    eligible = ticks[ticks.analysis_eligible].copy()
    modern = eligible[eligible.variant != "historical_unknown"].copy()
    probe = eligible[eligible.has_probe].copy()
    probe = probe[probe.tick > 1]  # Sampler starts before tick 1.
    results = {
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "scipy": scipy.__version__,
                     "statsmodels": version("statsmodels"), "matplotlib": version("matplotlib")},
        "seed": seed, "resamples": repetitions,
        "coverage": {"listed_games": len(games), "archived_games": int((~games.missing_logs).sum()),
                     "ticks": len(ticks), "eligible_ticks": len(eligible),
                     "eligible_games": int(eligible.game_id.nunique()), "probe_ticks": len(probe),
                     "eligible_timeouts": int(eligible.timeout.sum()),
                     "exclusions": ticks.loc[~ticks.analysis_eligible, "exclusion_reason"]
                     .value_counts().to_dict()},
        "distributions": {},
    }
    distribution_rows = []
    for label, frame, columns in (
        ("all_eligible", eligible, ["reply_ms", "server_cycle_ms", "server_post_reply_ms",
                                  "server_pre_send_ms"]),
        ("modern", modern, ["reply_ms"]),
        ("probe", probe, ["max_stall_ms", "worker_wait_ms", "own_cpu_ms", "psi_ms",
                          "decision_ms", "post_decision_gap_ms", "gc_ms", "steal_ms",
                          "arrival_after_play_log_ms"]),
        ("probe_stall20", probe[probe.max_stall_ms.ge(20)], ["max_stall_ms", "psi_ms",
                                                          "worker_wait_ms", "post_decision_gap_ms"]),
    ):
        for column in columns:
            summary = describe(frame[column])
            results["distributions"][f"{label}.{column}"] = summary
            distribution_rows.append({"population": label, "metric": column, **summary})
    pd.DataFrame(distribution_rows).to_csv(output / "distributions.csv", index=False)
    variant_stats, variant_tests = compare_variants(eligible, rng, repetitions)
    variant_stats.to_csv(output / "variants.csv", index=False)
    variant_tests.to_csv(output / "variant-tests.csv", index=False)
    lag_rows = []
    for population, frame, source, outcome in (
        ("modern", modern, "late110", "late110"),
        ("modern", modern, "late130", "late130"),
        ("probe", probe, "stall20", "stall20"),
        ("probe", probe, "pressure20", "stall20"),
        ("probe", probe, "stall20", "late110"),
    ):
        for lag in (1, 2, 3, 5, 10):
            row = lag_test(frame, source, outcome, lag, rng, repetitions)
            if row:
                lag_rows.append({"population": population, **row})
    if lag_rows:
        adjusted = multipletests([row["permutation_p"] for row in lag_rows], method="holm")[1]
        for row, p in zip(lag_rows, adjusted):
            row["holm_p"] = float(p)
    pd.DataFrame(lag_rows).to_csv(output / "lag-tests.csv", index=False)
    events = probe[probe.max_stall_ms.ge(20)]
    probe_summary = {
        "stall20_count": len(events), "stall20_rate": len(events) / len(probe),
        "stall20_rate_block_ci": block_interval(probe, "stall20", rng, repetitions),
        "stall_phase_counts": events.stall_phase.value_counts().to_dict(),
        "stall_run_lengths": describe(run_lengths(probe, "stall20")),
        "throttle_count": int(probe.throttle_count.sum()),
        "steal_ms": float(probe.steal_ms.sum()),
        "gc_over_1ms_ticks": int(probe.gc_ms.gt(1).sum()),
        "stall_interarrival_ticks": describe(np.diff(events.tick)),
        "stall_interarrival_ms": describe(np.diff(events.bot_wall_epoch_ms)),
    }
    probe_summary["episodes"] = probe_episodes(probe, output)
    probe_summary["pressure20_sensitivity"] = float(events.pressure20.astype(bool).mean())
    probe_summary["pressure20_false_positive_rate"] = float(
        probe.loc[~probe.stall20.astype(bool), "pressure20"].astype(bool).mean()
    )
    probe["previous_gap_ms"] = probe.post_decision_gap_ms.shift(1)
    probe["previous_stall_ms"] = probe.max_stall_ms.shift(1)
    probe[probe.timeout].to_csv(output / "probe-timeouts.csv", index=False)
    searched = probe[probe.simulations.gt(0) & probe.worker_run_ms.gt(0)]
    probe_summary["worker_run_clean_search_ms"] = describe(
        searched.loc[searched.max_stall_ms.lt(20), "worker_run_ms"]
    )
    probe_summary["worker_run_stalled_search_ms"] = describe(
        searched.loc[searched.max_stall_ms.ge(20), "worker_run_ms"]
    )
    if len(events):
        probe_summary["sampled_stall_time_fraction"] = float(
            events.max_stall_ms.sum() / probe.cycle_ms.sum()
        )
    correlations = []
    for a, b in (("max_stall_ms", "psi_ms"), ("max_stall_ms", "worker_wait_ms"),
                 ("max_stall_ms", "gc_ms"), ("max_stall_ms", "minor_faults"),
                 ("max_stall_ms", "host_busy_ms"), ("max_stall_ms", "own_cpu_ms"),
                 ("max_stall_ms", "reply_ms"), ("post_decision_gap_ms", "reply_ms")):
        valid = probe[[a, b]].dropna()
        test = stats.spearmanr(valid[a], valid[b])
        correlations.append({
            "x": a, "y": b, "n": len(valid), "spearman_rho": float(test.statistic),
            "nominal_p_descriptive_only": float(test.pvalue),
        })
    pd.DataFrame(correlations).to_csv(output / "probe-correlations.csv", index=False)
    probe_summary["correlations"] = correlations
    results["probe"] = probe_summary
    results["probe"]["clustering_test"] = clustering_test(probe, rng, repetitions)
    # Both time series and phase tests use circular-shift nulls, preserving runs.
    pattern_rows = []
    patterns = [
        ("modern", modern, "late110", "map_shifted"),
        ("modern", modern, "late130", "map_shifted"),
        ("probe", probe, "stall20", "map_shifted"),
    ]
    for population, frame, event, predictor in patterns:
        clean = frame.dropna(subset=[predictor]).copy()
        clean[predictor] = clean[predictor].astype(bool)
        row = lag_test(clean, predictor, event, 0, rng, repetitions)
        pattern_rows.append({"population": population, "pattern": predictor, **row})
    # Search for a fixed 5/10/20/50/100-tick cadence, preserving local event counts.
    for population, frame, event in (("modern", modern, "late110"), ("probe", probe, "stall20")):
        for period in (5, 10, 20, 50, 100):
            phases = (frame.tick.to_numpy() % period).astype(int)
            flags = frame[event].to_numpy(dtype=bool)
            totals = np.bincount(phases, minlength=period)
            indices = list(frame.groupby(["game_id", "tick_decile"]).indices.values())

            def statistic(values):
                counts = np.bincount(phases, weights=values, minlength=period)
                expected = totals * values.mean()
                return float(np.sum((counts - expected) ** 2 / np.maximum(expected, 1e-12)))

            observed = statistic(flags)
            null = []
            for _ in range(repetitions):
                permuted = flags.copy()
                for index in indices:
                    permuted[index] = rng.permutation(flags[index])
                null.append(statistic(permuted))
            pattern_rows.append({
                "population": population, "pattern": f"tick_mod_{period}", "outcome": event,
                "statistic": observed,
                "permutation_p": (1 + np.sum(np.asarray(null) >= observed)) / (repetitions + 1),
            })
    adjusted = multipletests([row["permutation_p"] for row in pattern_rows], method="holm")[1]
    for row, p in zip(pattern_rows, adjusted):
        row["holm_p"] = float(p)
    pd.DataFrame(pattern_rows).to_csv(output / "pattern-tests.csv", index=False)
    temporal = eligible.groupby(["variant", "tick_decile"]).agg(
        ticks=("tick", "size"), games=("game_id", "nunique"),
        late110=("late110", "sum"), late130=("late130", "sum"),
        timeouts=("timeout", "sum"), median_reply_ms=("reply_ms", "median"),
    ).reset_index()
    temporal.to_csv(output / "match-phase.csv", index=False)
    probe.groupby("tick_decile").agg(
        ticks=("tick", "size"), stalls=("stall20", "sum"),
        pressure=("pressure20", "sum"), median_stall_ms=("max_stall_ms", "median"),
    ).to_csv(output / "probe-match-phase.csv")
    probe["previous_stall20"] = probe.stall20.shift(1)
    probe["next_stall20"] = probe.stall20.shift(-1)
    probe.to_csv(output / "probe-ticks.csv", index=False)
    # A descriptive periodogram, not a significance claim; sampling intervals are irregular.
    if len(probe) > 10:
        elapsed = (probe.bot_wall_epoch_ms.to_numpy() - probe.bot_wall_epoch_ms.iloc[0]) / 1000
        frequencies = np.linspace(0.1, 8, 2000)
        power = signal.lombscargle(
            elapsed, probe.stall20.to_numpy(dtype=float) - probe.stall20.mean(),
            2 * np.pi * frequencies, normalize=True,
        )
        pd.DataFrame({"frequency_hz": frequencies, "period_ms": 1000 / frequencies,
                      "power": power}).to_csv(output / "probe-periodogram.csv", index=False)
        peaks, _ = signal.find_peaks(power)
        best = sorted(peaks, key=lambda i: power[i], reverse=True)[:5]
        results["probe"]["periodogram_peaks_descriptive"] = [
            {"period_ms": float(1000 / frequencies[i]), "power": float(power[i])} for i in best
        ]
    results["variant_tests"] = variant_tests.to_dict("records")
    results["lag_tests"] = lag_rows
    results["pattern_tests"] = pattern_rows
    results["spectral_probe"] = spectral_test(
        probe, "stall20", rng, min(repetitions, 1999)
    )
    independent = eligible[eligible.variant.ne("env_probe")]
    results["five_second_replication"] = [
        fixed_cadence_test(independent, event, rng, min(repetitions, 1999))
        for event in ("dispatch_gap20", "late110")
    ]
    results["spectral_robustness"] = [
        fixed_cadence_test(
            probe.assign(threshold_event=probe.max_stall_ms.ge(threshold)),
            "threshold_event", rng, min(repetitions, 999)
        ) | {"threshold_ms": threshold}
        for threshold in (10, 30, 40)
    ]
    results["phase_alignment"] = phase_alignment(independent, output, rng)
    results["timing_model"] = timing_model(modern, output)
    (output / "statistics.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    plot(ticks, probe, output)
    return results


def plot(ticks, probe, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(3, 2, figsize=(14, 11), constrained_layout=True)
    events = probe[probe.max_stall_ms.ge(20)]
    axes[0, 0].hist(events.max_stall_ms, bins=np.arange(20, events.max_stall_ms.max() + 10, 5))
    axes[0, 0].set(xlabel="Longest sampled stall per affected cycle (ms)", ylabel="Cycles")
    elapsed = (probe.bot_wall_epoch_ms - probe.bot_wall_epoch_ms.iloc[0]) / 1000
    axes[0, 1].scatter(elapsed, probe.max_stall_ms, s=6)
    axes[0, 1].set(xlabel="Elapsed wall time (seconds)", ylabel="Longest sampled stall (ms)")
    axes[1, 0].scatter(probe.max_stall_ms, probe.psi_ms, s=6)
    axes[1, 0].set(xlabel="Longest sampled stall (ms)", ylabel="Host PSI delta (ms/cycle)")
    axes[1, 1].scatter(probe.tick, probe.post_decision_gap_ms, s=6)
    axes[1, 1].set(xlabel="Probe tick", ylabel="After-decision gap to next call (ms)")
    modern = ticks[ticks.analysis_eligible & ticks.variant.ne("historical_unknown")]
    for name, frame in modern.groupby("variant"):
        if name == "env_probe":
            continue
        values = np.sort(frame.reply_ms.dropna())
        axes[2, 0].step(values, 1 - np.arange(1, len(values) + 1) / len(values), label=name)
    axes[2, 0].set(xlim=(95, 150), ylim=(0.0001, 0.05), yscale="log",
                   xlabel="Accepted reply time (ms)", ylabel="Fraction slower (excludes timeouts)")
    axes[2, 0].legend(fontsize=7)
    for name, frame in modern.groupby("variant"):
        phase = frame.groupby("tick_decile").late110.mean()
        axes[2, 1].plot(phase.index, phase * 100, marker=".", label=name)
    axes[2, 1].set(xlabel="Match decile (0 = ticks 1–100)", ylabel="Late >110ms / timeout (%)")
    figure.savefig(output / "freeze-patterns.png", dpi=150)
    plt.close(figure)
    figure, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    spectrum = pd.read_csv(output / "probe-periodogram.csv")
    axes[0].plot(spectrum.period_ms / 1000, spectrum.power)
    axes[0].set(xlim=(0.5, 10), xlabel="Period (seconds)", ylabel="Lomb-Scargle power",
                title="Direct CPU probe: irregular-time spectrum")
    phases = pd.read_csv(output / "five-second-phases.csv")
    for reference, group in phases.groupby("reference"):
        axes[1].hist(group.preferred_phase_ms / 1000, bins=np.arange(0, 5.01, 0.25),
                     histtype="step", label=reference)
    axes[1].set(xlabel="Preferred phase in five-second cycle (seconds)", ylabel="Games",
                title="Independent server-overhead timing proxies")
    axes[1].legend()
    figure.savefig(output / "freeze-periodicity.png", dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "runs/freeze-analysis")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resamples", type=int, default=4999)
    parser.add_argument("--seed", type=int, default=20261106)
    args = parser.parse_args()
    output = args.output or args.archive
    output.mkdir(parents=True, exist_ok=True)
    ticks, games, budget = extract(args.archive)
    ticks.to_csv(output / "ticks.csv", index=False)
    games.to_csv(output / "games.csv", index=False)
    budget.to_csv(output / "budget-blocks.csv", index=False)
    results = analyze(ticks, games, budget, output, args.resamples, args.seed)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
