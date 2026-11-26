"""The smoke test's output checks: whole output captured before matching, docker's own failures kept."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NEEDLE = "run t3-boat setup with T3_BOAT_PROFILE=codex-only to complete it"
# Far more than a pipe buffer after the match, so a reader that stops at the match leaves docker writing.
FILLER = 4 << 20

DOCKER = f"""#!/usr/bin/env python3
import os, sys
mode = os.environ["FAKE_DOCKER"]
if sys.argv[1] == "logs":
    if mode == "error":
        sys.exit("Error response from daemon: No such container: " + sys.argv[2])
    if mode != "missing":
        print("T3 Boat: {NEEDLE}.", file=sys.stderr, flush=True)
    for _ in range({FILLER} // 80):
        sys.stdout.write("x" * 79 + "\\n")
elif sys.argv[1] == "exec" and sys.argv[3:] == ["t3-boat", "status"]:
    if mode == "error":
        sys.exit(125)
    print("[missing] Boat account")
    print("  {NEEDLE}" if mode != "missing" else "  nothing")
    sys.stdout.write("y" * {FILLER})
    sys.exit(1)  # Incomplete setup.
else:
    sys.exit(2)
"""


class SmokeLibTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        bin_dir = Path(temporary.name)
        (bin_dir / "docker").write_text(DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def check(self, mode, script):
        return subprocess.run(["bash", "-c", "set -euo pipefail; source \"$1\"; " + script, "_",
                               str(ROOT / "tests/smoke-lib.sh")],
                              env={**os.environ, "PATH": self.path, "FAKE_DOCKER": mode},
                              capture_output=True, text=True, timeout=120)

    LOGS = f'logs=$(container_logs c1); require "docker logs" "$logs" "{NEEDLE}"; echo passed'
    STATUS = f'status=$(status_output c1); require "t3-boat status" "$status" "{NEEDLE}"; echo passed'

    def test_an_early_match_before_a_long_output_passes_every_time(self):
        for script in (self.LOGS, self.STATUS):
            for attempt in range(20):
                with self.subTest(script=script[:20], attempt=attempt):
                    result = self.check("match", script)
                    self.assertEqual((result.returncode, result.stdout), (0, "passed\n"), result.stderr[-500:])

    def test_a_missing_match_fails(self):
        for script, label in ((self.LOGS, "docker logs"), (self.STATUS, "t3-boat status")):
            with self.subTest(label=label):
                result = self.check("missing", script)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("passed", result.stdout)
                self.assertIn(f"{label} lacks: {NEEDLE}", result.stderr)

    def test_a_docker_failure_stops_the_check(self):
        result = self.check("error", self.LOGS)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("passed", result.stdout)
        self.assertIn("No such container: c1", result.stderr)
        self.assertIn("docker logs c1 failed (exit 1)", result.stderr)
        result = self.check("error", self.STATUS)
        self.assertEqual(result.returncode, 125)
        self.assertNotIn("passed", result.stdout)
        self.assertIn("docker exec c1 t3-boat status failed (exit 125)", result.stderr)

    def test_the_earlier_pipe_into_grep_q_fails_on_the_same_output(self):
        # The control: grep -q exits at the match and docker logs dies writing the rest, so pipefail fails.
        result = self.check("match", f'docker logs c1 2>&1 | grep -Fq "{NEEDLE}"; echo passed')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("passed", result.stdout)

    def test_the_smoke_test_matches_no_pipe_into_an_early_exit(self):
        # grep -q, or a shell swallowing the status of what feeds it, are what these helpers replace.
        smoke = (ROOT / "tests/smoke-image.sh").read_text()
        self.assertNotIn("| grep -Fq", smoke)
        self.assertNotIn("t3-boat status || true", smoke)


if __name__ == "__main__":
    unittest.main()
