import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install-claude"
PINNED = re.search(r"^version=(\S+)$", SCRIPT.read_text(), re.MULTILINE)[1]
SHELL = shutil.which("dash") or "sh"  # The image runs the script with dash.


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
        result = subprocess.run([SHELL, str(SCRIPT)], env=self.env, capture_output=True, text=True)
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

    def assert_kept_with_one_warning(self, output, quoted):
        fake_claude(self.claude, None, output)
        before = self.claude.read_bytes()
        self.assertIsNone(self.install())
        self.assertEqual(self.claude.read_bytes(), before)
        lines = self.stderr.splitlines()
        self.assertEqual(len(lines), 1, self.stderr)
        self.assertIn("warning", lines[0].lower())
        self.assertIn(f"'{quoted}'", lines[0])

    def test_malformed_version_tokens_are_kept_with_a_warning(self):
        major, minor, patch = map(int, PINNED.split("."))
        for token in (f"{PINNED}-!!!", f"{PINNED}-rc..1", "1.0.0+..", f"{PINNED}.1", f"{PINNED}-", f"{PINNED}+",
                      f"{PINNED}-rc.", f"{PINNED}+build+5", f"vv{PINNED}", f"{major}.{minor}", f"{PINNED}x"):
            with self.subTest(token=token):
                self.assert_kept_with_one_warning(f"{token} (Claude Code)\n", f"{token} (Claude Code)")

    def test_numeric_fields_have_no_length_limit(self):
        major, minor, patch = map(int, PINNED.split("."))
        cases = {
            f"{major - 1}.1000000000.0": True,
            f"{major}.{minor}.{'0' * 12}{patch - 1}": True,  # leading zeros do not make a number larger
            "99999999999999999999.0.0": False,
            f"{major}.{minor}.{'0' * 12}{patch}": False,
            f"{major}.{minor}.99999999999999999999999": False,
        }
        for version, installs in cases.items():
            with self.subTest(installed=version):
                fake_claude(self.claude, version)
                self.assertEqual(self.install() is not None, installs, self.stderr)
                self.assertEqual(self.stderr, "")

    def test_pre_release_and_build_identifiers(self):
        major, minor, patch = map(int, PINNED.split("."))
        cases = {
            f"{PINNED}-rc.1": True,
            f"{PINNED}-alpha-2.x-y.0": True,
            f"{major}.{minor}.{patch + 1}-rc.1": False,
            f"v{major}.{minor + 1}.0": False,
            f"{PINNED}+build.5": False,
            f"{PINNED}-rc.1+build-7.a": True,
        }
        for version, installs in cases.items():
            with self.subTest(installed=version):
                fake_claude(self.claude, version)
                self.assertEqual(self.install() is not None, installs, self.stderr)
                self.assertEqual(self.stderr, "")

    def test_a_literal_backslash_n_is_quoted_unchanged(self):
        self.assert_kept_with_one_warning("bad\\nversion\n", "bad\\nversion")

    def test_only_the_first_line_is_read(self):
        major, minor, patch = map(int, PINNED.split("."))
        fake_claude(self.claude, None, f"{major}.{minor}.{patch - 1} (Claude Code)\n{PINNED}\n")
        self.assertIsNotNone(self.install())


if __name__ == "__main__":
    unittest.main()
