import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install-claude"
PINNED = re.search(r"^version=(\S+)$", SCRIPT.read_text(), re.MULTILINE)[1]


def fake_claude(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
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
        args = self.home / "npm-args"
        return args.read_text().split() if args.exists() else None

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


if __name__ == "__main__":
    unittest.main()
