"""Windows/Linux artifact selection and build-command regression tests."""

import unittest
from pathlib import Path
from unittest.mock import patch

import build_sim
from platform_paths import BUILD, ROOT, executable_suffix, launcher_path, library_name, simulator_path


class PlatformPathsTest(unittest.TestCase):
    def test_artifacts_match_the_selected_platform(self):
        for platform, suffix, extension, launcher in (
            ("win32", ".exe", ".dll", "blitz-challenge-win.exe"),
            ("linux", "_linux", ".so", "blitz-challenge-linux"),
        ):
            with self.subTest(platform=platform):
                self.assertEqual(executable_suffix(platform), suffix)
                self.assertEqual(simulator_path("sim_live", platform), BUILD / f"sim_live{suffix}")
                self.assertEqual(library_name("sim", platform), f"libsim{extension}")
                self.assertEqual(library_name("probe", platform), f"libprobe{extension}")
                self.assertEqual(launcher_path(platform), ROOT / "localRunner" / launcher)

    def test_native_and_cross_targets_are_not_duplicated(self):
        for host in ("linux", "win32"):
            with self.subTest(host=host), patch.object(build_sim.sys, "platform", host):
                self.assertEqual(build_sim.build_targets(False, False), [(host, None)])
                targets = dict(build_sim.build_targets(True, True))
                self.assertEqual(
                    targets, {"linux": build_sim.LINUX_TARGET, "win32": build_sim.WINDOWS_TARGET},
                )

    def test_main_builds_matching_executables_and_libraries(self):
        for host in ("linux", "win32"):
            with (
                self.subTest(host=host),
                patch.object(build_sim.sys, "platform", host),
                patch.object(build_sim.sys, "argv", ["build_sim.py"]),
                patch.object(build_sim, "compile_program") as program,
                patch.object(build_sim, "compile_library") as library,
                patch.object(build_sim, "compile_shared") as shared,
                patch.object(Path, "mkdir"),
            ):
                build_sim.main()
                self.assertEqual(program.call_count, len(build_sim.PROGRAMS))
                for call in program.call_args_list:
                    self.assertEqual(call.args[2:], (None, executable_suffix(host)))
                library.assert_called_once_with(build_sim.BOT_DIR / library_name("sim", host), None)
                shared.assert_called_once_with(
                    build_sim.PROBE_DIR / library_name("probe", host),
                    [build_sim.PROBE_DIR / "probe_native.c"], None, c=True,
                )

    def test_shared_library_flags_match_target_not_host(self):
        for host in ("linux", "win32"):
            for target, windows in ((None, host == "win32"), (build_sim.LINUX_TARGET, False),
                                    (build_sim.WINDOWS_TARGET, True)):
                with (
                    self.subTest(host=host, target=target),
                    patch.object(build_sim.sys, "platform", host),
                    patch.object(build_sim, "zig_python", return_value="python"),
                    patch.object(build_sim.subprocess, "run") as run,
                    patch("builtins.print"),
                ):
                    output = Path("folder with spaces/libtest.dll" if windows else "folder with spaces/libtest.so")
                    build_sim.compile_shared(output, [Path("source with spaces.c")], target, c=True)
                    command = run.call_args.args[0]
                    self.assertEqual("-fPIC" in command, not windows)
                    self.assertEqual("-target" in command, target is not None)
                    self.assertIn(str(output), command)
                    self.assertEqual(command[-1], "source with spaces.c")
                    self.assertEqual(command[3], "cc")
                    run.assert_called_once_with(command, check=True)

    def test_zig_fallback_uses_host_virtualenv_layout(self):
        for host, relative in (("linux", ".venv/bin/python"), ("win32", ".venv/Scripts/python.exe")):
            with (
                self.subTest(host=host),
                patch.object(build_sim.sys, "platform", host),
                patch.object(build_sim.importlib.util, "find_spec", return_value=None),
                patch.object(Path, "exists", return_value=True),
            ):
                self.assertEqual(build_sim.zig_python(), str(ROOT / relative))


if __name__ == "__main__":
    unittest.main()
