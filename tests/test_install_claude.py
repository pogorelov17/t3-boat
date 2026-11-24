import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install-claude"
PINNED = re.search(r"^version=(\S+)$", SCRIPT.read_text(), re.MULTILINE)[1]


def fake_claude(path, version, output=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    output = f"{version} (Claude Code)\n" if output is None else output
    path.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\n")
    path.chmod(0o755)


class InstallClaudeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.claude = self.home / ".local/bin/claude"
        tools = self.home / "tools"
        tools.mkdir()
        npm = tools / "npm"
        # Record the request and install a binary reporting the requested version.
        npm.write_text('#!/bin/sh\necho "$*" > "$HOME/npm-args"\n'
                       'for package; do :; done\n'
                       'mkdir -p "$HOME/.local/bin"\n'
                       'printf \'#!/bin/sh\\necho "%s (Claude Code)"\\n\' "${package##*@}" > "$HOME/.local/bin/claude"\n'
                       'chmod 755 "$HOME/.local/bin/claude"\n')
        npm.chmod(0o755)
        self.env = {"HOME": str(self.home),
                    "PATH": os.pathsep.join([str(self.claude.parent), str(tools), os.environ["PATH"]])}

    def install(self):
        result = subprocess.run(["sh", str(SCRIPT)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.stderr = result.stderr
        args = self.home / "npm-args"
        try:
            return args.read_text().split()
        except FileNotFoundError:
            return None
        finally:
            args.unlink(missing_ok=True)

    def test_missing_claude_is_installed(self):
        args = self.install()
        self.assertIsNotNone(args)
        self.assertEqual(args[-1], f"@anthropic-ai/claude-code@{PINNED}")

    def test_older_claude_is_upgraded(self):
        major, minor, patch = map(int, PINNED.split("."))
        # 2.1.99 sorts after 2.1.330 as text but is older.
        for older in (f"{major}.{minor}.{patch - 1}", f"{major}.{minor}.{patch // 10}", f"{major}.{minor}.99",
                      f"{major}.{minor - 1}.999"):
            with self.subTest(installed=older):
                (self.home / "npm-args").unlink(missing_ok=True)
                fake_claude(self.claude, older)
                self.assertIsNotNone(self.install())

    def test_equal_or_newer_claude_is_kept(self):
        major, minor, patch = map(int, PINNED.split("."))
        for version in (PINNED, f"{major}.{minor}.{patch + 1}", f"{major}.{minor}.{patch + 1000}",
                        f"{major}.{minor + 1}.0", f"{major + 1}.0.0"):
            with self.subTest(installed=version):
                fake_claude(self.claude, version)
                self.assertIsNone(self.install())
                self.assertIn(version, self.claude.read_text())

    def test_versions_compare_numerically_with_an_optional_v(self):
        major, minor, patch = map(int, PINNED.split("."))
        cases = {
            f"{PINNED}-rc.1": True,  # a pre-release of the pin is older than the pin
            f"v{major}.{minor + 1}.0": False,
            f"v{major}.{minor}.{patch - 1}": True,
            f"{major}.{minor}.{patch + 1}-rc.1": False,
        }
        for version, installs in cases.items():
            with self.subTest(installed=version):
                fake_claude(self.claude, version)
                self.assertEqual(self.install() is not None, installs)
                if not installs:
                    self.assertIn(version, self.claude.read_text())

    def test_unrecognised_version_output_leaves_claude_alone_with_a_warning(self):
        for printed, output in (("9.9garbage", "9.9garbage (Claude Code)\n"), ("2.x.9", "2.x.9 (Claude Code)\n"),
                                ("", ""), ("Update available", f"Update available\n{PINNED} (Claude Code)\n")):
            with self.subTest(output=output):
                fake_claude(self.claude, None, output)
                before = self.claude.read_bytes()
                self.assertIsNone(self.install())
                self.assertEqual(self.claude.read_bytes(), before)
                lines = self.stderr.splitlines()
                self.assertEqual(len(lines), 1, self.stderr)
                self.assertIn("warning", lines[0].lower())
                self.assertIn(f"'{printed}", lines[0])

    def test_only_the_first_line_is_read(self):
        major, minor, patch = map(int, PINNED.split("."))
        fake_claude(self.claude, None, f"{major}.{minor}.{patch - 1} (Claude Code)\n{PINNED}\n")
        self.assertIsNotNone(self.install())


if __name__ == "__main__":
    unittest.main()
