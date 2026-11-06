import json
import tempfile
import unittest
from pathlib import Path

from analyze_freezes import extract, parse_probe, parse_server


class FreezeParserTests(unittest.TestCase):
    def test_timeout_and_late_ignored_command_remain_distinct(self):
        rows = parse_server(
            "[INFO] 2026-11-06T01:42:56.489Z: Playing tick 249.\n"
            "[INFO] 2026-11-06T01:42:56.640Z: No command was received in time "
            "for ([Team] 84 - Test) on tick 249\n"
            "[INFO] 2026-11-06T01:42:56.642Z: Playing tick 250.\n"
            "[WARN] 2026-11-06T01:42:56.643Z: ([Team] 84 - Test) "
            "Command for tick 249 ignored: waiting for tick 250\n"
            "[INFO] 2026-11-06T01:42:56.740Z: Command received from "
            "([Team] 84 - Test) for tick 250 after 98 ms\n"
        )
        self.assertTrue(rows[249]["timeout"])
        self.assertEqual(rows[249]["ignored_commands"], 1)
        self.assertNotIn("reply_ms", rows[249])
        self.assertEqual(rows[250]["reply_ms"], 98)
        self.assertFalse(rows[250]["timeout"])

    def test_disconnect_is_not_cpu_timeout(self):
        rows = parse_server(
            "[INFO] 2026-11-03T22:04:31.728Z: Playing tick 50.\n"
            "[WARN] 2026-11-03T22:04:31.728Z: Error while fetching "
            "([Team] 84 - Test) command for tick 50. Exception : "
            "Error: ([Team] 84 - Test) Socket connection lost\n"
            "[INFO] 2026-11-03T22:04:31.728Z: No command was received "
            "in time for ([Team] 84 - Test) on tick 50\n"
        )
        self.assertTrue(rows[50]["socket_error"])
        self.assertTrue(rows[50]["timeout"])

    def test_probe_units_and_sampler_alignment(self):
        row = parse_probe(
            "TICK 1 1793929360144.1 95.6 106.8 12.3@-17 1 93.5 0.5 "
            "0 100 98.4 1083 0/0 9 502 0.32 libsim_clx.so 13041"
        )
        self.assertEqual(row["max_stall_ms"], 12.3)
        self.assertEqual(row["stall_offset_ms"], -17)
        self.assertEqual(row["psi_ms"], 1.083)
        self.assertEqual(row["involuntary_switches"], 9)
        self.assertEqual(row["simulations"], 13041)
        with self.assertRaises(ValueError):
            parse_probe("TICK 1 missing fields")

    def test_final_log_entry_and_duplicate_replay_are_not_extra_trials(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary)
            folder = archive / "raw/1"
            folder.mkdir(parents=True)
            (archive / "server-games.tsv").write_text(
                "gameId\tgameTime\tscore\ttimedOut\n1\t2026-11-06 00:00:00\t100\tfalse\n"
            )
            (folder / "bot.log").write_text("")
            (folder / "game.log").write_text(
                "[INFO] 2026-11-06T00:00:00.000Z: Playing tick 1.\n"
                "[INFO] 2026-11-06T00:00:00.095Z: Command received from "
                "([Team] 84 - Test) for tick 1 after 95 ms\n"
                "[INFO] 2026-11-06T00:00:00.097Z: Playing tick 2.\n"
            )
            state = {"currentTick": 1, "map": {"origin": {"y": 0}},
                     "constants": {"mapShiftInterval": 5}}
            (folder / "replay.jsonl").write_text((json.dumps(state) + "\n") * 2)
            ticks, games, _ = extract(archive)
        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks.analysis_eligible.tolist(), [True, False])
        self.assertEqual(ticks.exclusion_reason.tolist(), ["", "no_command_window"])
        self.assertEqual(games.eligible_ticks.iloc[0], 1)


if __name__ == "__main__":
    unittest.main()
