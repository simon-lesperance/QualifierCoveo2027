"""Focused PGO build and fixed-work validation regressions."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_pgo
import build_sim


class PgoBuildTest(unittest.TestCase):
    def test_instrumented_library_keeps_v3_without_cpu_tuning(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "profile.a"
            runtime.touch()
            with (
                patch.object(build_sim, "zig_python", return_value="python"),
                patch.object(build_sim.subprocess, "run") as run,
                patch("builtins.print"),
            ):
                build_sim.compile_library(
                    Path(folder) / "libsim.so", build_sim.LINUX_TARGET,
                    extra_flags=build_pgo.profile_flags(True, runtime=runtime),
                )
                command = run.call_args.args[0]
            self.assertIn("-march=x86_64_v3", command)
            self.assertNotIn("-mtune=cascadelake", command)
            self.assertNotIn("-tune-cpu", command)
            self.assertNotIn("cascadelake", command)
            self.assertIn("-fprofile-generate", command)
            self.assertIn("-Wl,-u,__llvm_profile_runtime", command)
            self.assertIn(str(runtime), command)
            self.assertIn("-fPIC", command)

    def test_profile_use_requires_data_and_never_links_the_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "sim.profdata"
            with self.assertRaisesRegex(ValueError, "profile not found"):
                build_pgo.profile_flags(profile=profile)
            profile.touch()
            flags = build_pgo.profile_flags(profile=profile)
            self.assertEqual(flags, [
                f"-fprofile-use={profile}", "-Werror=profile-instr-out-of-date",
            ])
        with self.assertRaisesRegex(ValueError, "profile-runtime"):
            build_pgo.profile_flags(True)

    def test_training_driver_links_library_instead_of_compiling_engine_again(self):
        with (
            patch.object(build_sim, "zig_python", return_value="python"),
            patch.object(build_sim.subprocess, "run") as run,
            patch("builtins.print"),
        ):
            library = Path("/build/instrumented/libsim.so")
            build_sim.compile_program(
                "sim_live", "live_main.cpp", build_sim.LINUX_TARGET, "_linux",
                output_dir=library.parent, library=library,
            )
            command = run.call_args.args[0]
        self.assertIn(str(library.parent / "sim_live_linux"), command)
        self.assertIn(str(build_sim.SIM / "live_main.cpp"), command)
        self.assertNotIn(str(build_sim.SIM / "game.cpp"), command)
        self.assertIn("-lsim", command)
        self.assertIn("-Wl,-rpath,$ORIGIN", command)

    def test_scoped_profile_rebuild_does_not_replace_other_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "sim.profdata"
            profile.touch()
            with (
                patch.object(build_sim.sys, "platform", "linux"),
                patch.object(build_sim.sys, "argv", [
                    "build_sim.py", "--linux", "--only", "libsim",
                    "--pgo-use", str(profile),
                ]),
                patch.object(build_sim, "compile_program") as program,
                patch.object(build_sim, "compile_library") as library,
                patch.object(build_sim, "compile_shared") as shared,
                patch.object(build_sim, "compile_vec_env") as vec_env,
                patch.object(Path, "mkdir"),
            ):
                build_sim.main()
            library.assert_called_once_with(
                build_sim.BOT_DIR / "libsim.so", build_sim.LINUX_TARGET,
                extra_flags=build_sim.profile_flags(profile=profile),
            )
            program.assert_not_called()
            shared.assert_not_called()
            vec_env.assert_not_called()

    def test_merge_excludes_inference_and_rejects_empty_profiles(self):
        summary = "Instrumentation level: IR\nMaximum function count: 100\n"
        with (
            patch.object(build_pgo.subprocess, "run") as run,
            patch.object(build_pgo.subprocess, "check_output", return_value=summary) as show,
        ):
            result = build_pgo.merge_profile("llvm-profdata", [Path("game.profraw")],
                                             Path("sim.profdata"))
            self.assertEqual(result, summary)
            run.assert_called_once_with([
                "llvm-profdata", "merge", "--no-function=ValueNet", "-o", "sim.profdata",
                "game.profraw",
            ], check=True)
            run.reset_mock()
            build_pgo.merge_profile("llvm-profdata", [Path("game.profraw")],
                                    Path("full.profdata"), include_value_net=True)
            run.assert_called_once_with([
                "llvm-profdata", "merge", "-o", "full.profdata", "game.profraw",
            ], check=True)
            show.return_value = "Instrumentation level: IR\nMaximum function count: 0\n"
            with self.assertRaisesRegex(RuntimeError, "empty or incompatible profile"):
                build_pgo.merge_profile("llvm-profdata", [Path("game.profraw")],
                                        Path("sim.profdata"))

    def test_server_tuning_only_changes_linux_bot_library(self):
        for host in ("linux", "win32"):
            with (
                self.subTest(host=host),
                patch.object(build_sim.sys, "platform", host),
                patch.object(build_sim.sys, "argv", [
                    "build_sim.py", "--linux", "--windows", "--server-tune",
                ]),
                patch.object(build_sim, "compile_program") as program,
                patch.object(build_sim, "compile_library") as library,
                patch.object(build_sim, "compile_shared") as shared,
                patch.object(build_sim, "compile_vec_env") as vec_env,
                patch.object(Path, "mkdir"),
            ):
                build_sim.main()
            linux_calls = [
                call for call in library.call_args_list
                if call.args[0].suffix == ".so"
            ]
            self.assertEqual(len(linux_calls), 1)
            self.assertEqual(linux_calls[0].kwargs,
                             {"extra_flags": build_sim.SERVER_TUNE_FLAGS})
            for call in library.call_args_list:
                if call.args[0].suffix == ".dll":
                    self.assertEqual(call.kwargs, {})
            for mock in (program, shared, vec_env):
                for call in mock.call_args_list:
                    self.assertNotIn("extra_flags", call.kwargs)

    def test_server_tuning_combines_with_profile_use(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "sim.profdata"
            profile.touch()
            with (
                patch.object(build_sim.sys, "platform", "linux"),
                patch.object(build_sim.sys, "argv", [
                    "build_sim.py", "--linux", "--only", "libsim", "--server-tune",
                    "--pgo-use", str(profile),
                ]),
                patch.object(build_sim, "compile_library") as library,
                patch.object(Path, "mkdir"),
            ):
                build_sim.main()
            library.assert_called_once_with(
                build_sim.BOT_DIR / "libsim.so", build_sim.LINUX_TARGET,
                extra_flags=[*build_sim.profile_flags(profile=profile),
                             *build_sim.SERVER_TUNE_FLAGS],
            )

    def test_server_tuning_rejects_commands_without_linux_bot_library(self):
        for options in (["--server-tune"], ["--server-tune", "--linux", "--only", "sim_live"]):
            with (
                self.subTest(options=options),
                patch.object(build_sim.sys, "argv", ["build_sim.py", *options]),
                patch.object(build_sim, "compile_library") as library,
                patch("sys.stderr"),
                self.assertRaises(SystemExit) as error,
            ):
                build_sim.main()
            self.assertEqual(error.exception.code, 2)
            library.assert_not_called()

    def test_validation_rejects_changed_intermediate_ticks_even_if_score_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("baseline", "optimized"):
                traces = root / name / "traces"
                traces.mkdir(parents=True)
                for biome in ("deep_crevasse", "mountain_range", "volcanic_plains"):
                    (traces / f"{biome}-live-10.csv").write_text("1,0,0\n1000,123,1\n")
            self.assertEqual(
                build_pgo.compare_games(root / "baseline", root / "optimized", 1, 10),
                [0, 0, 0],
            )
            (root / "optimized/traces/deep_crevasse-live-10.csv").write_text(
                "1,1,0\n1000,123,1\n"
            )
            with self.assertRaisesRegex(RuntimeError, "changed the fixed-work game trace"):
                build_pgo.compare_games(root / "baseline", root / "optimized", 1, 10)


if __name__ == "__main__":
    unittest.main()
