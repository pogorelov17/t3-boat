"""Saved 0.1.x profiles keep working after the parent role became `lead`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from profile_fixtures import FIXTURES, legacy_roles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import role_profile  # noqa: E402

cli = SourceFileLoader("t3_boat_migration", str(ROOT / "bin/t3-boat")).load_module()
worker = SourceFileLoader("boat_worker_migration", str(ROOT / "bin/boat-worker")).load_module()
LEGACY = "orchestrator"


def renamed(keys):
    return ["lead" if key == LEGACY else key for key in keys]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.profiles = self.home / "profiles"
        self.profiles.mkdir()
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
        environment = patch.dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1")
        environment.start()
        self.addCleanup(environment.stop)

    def prepare(self, profile, check=True):
        return subprocess.run([sys.executable, str(ROOT / "lib/role_profile.py"), profile], env=self.env,
                              capture_output=True, text=True, check=check)

    def saved(self, profile):
        return self.profiles / (profile + ".json")

    def test_stock_0_1_5_profiles_migrate_with_a_backup(self):
        for fixture in sorted((FIXTURES / "v0.1.5").glob("*.json")):
            with self.subTest(profile=fixture.stem):
                original = fixture.read_bytes()
                path = self.saved(fixture.stem)
                path.write_bytes(original)
                self.assertTrue(role_profile.migrate_profile(path))
                migrated = json.loads(path.read_text())
                self.assertEqual(list(migrated), renamed(json.loads(original)))
                self.assertEqual(migrated, json.loads((ROOT / "profiles" / fixture.name).read_text()))
                self.assertEqual(path.with_name(fixture.name + ".pre-0.2").read_bytes(), original)

    def test_migration_is_idempotent_and_keeps_the_first_backup(self):
        path = self.saved("codex-only")
        path.write_bytes((FIXTURES / "v0.1.5/codex-only.json").read_bytes())
        backup = path.with_name("codex-only.json.pre-0.2")
        backup.write_text("earlier backup\n")
        self.assertTrue(role_profile.migrate_profile(path))
        self.assertEqual(backup.read_text(), "earlier backup\n")
        migrated = path.read_bytes()
        self.assertFalse(role_profile.migrate_profile(path))
        self.assertEqual(path.read_bytes(), migrated)
        self.assertEqual(backup.read_text(), "earlier backup\n")

    def test_backup_is_written_once(self):
        path = self.saved("codex-only")
        first = (FIXTURES / "v0.1.5/codex-only.json").read_bytes()
        path.write_bytes(first)
        role_profile.migrate_profile(path)
        path.write_text(json.dumps({LEGACY: {"provider": "codex", "model": "x", "effort": "low"}}))
        role_profile.migrate_profile(path)
        self.assertEqual(path.with_name("codex-only.json.pre-0.2").read_bytes(), first)

    def test_user_roles_and_key_order_are_preserved(self):
        path = self.saved("codex+claude")
        rows = json.loads((FIXTURES / "v0.1.5/codex+claude.json").read_text())
        parent = rows.pop(LEGACY)
        keys = list(rows)
        reordered = {k: rows[k] for k in keys[:2]}
        reordered["editor"] = {"provider": "codex", "model": "gpt-6.2-sol", "effort": "low", "note": "mine"}
        reordered[LEGACY] = parent
        reordered.update({k: rows[k] for k in keys[2:]})
        path.write_text(json.dumps(reordered))
        role_profile.migrate_profile(path)
        migrated = json.loads(path.read_text())
        self.assertEqual(list(migrated), renamed(reordered))
        self.assertEqual(migrated["editor"], reordered["editor"])
        self.assertEqual(migrated["lead"], parent)

    def test_both_parent_keys_are_an_error_naming_the_file(self):
        path = self.saved("claude-only")
        rows = json.loads((FIXTURES / "v0.1.5/claude-only.json").read_text())
        rows["lead"] = dict(rows[LEGACY])
        raw = json.dumps(rows)
        path.write_text(raw)
        with self.assertRaisesRegex(ValueError, str(path)):
            role_profile.migrate_profile(path)
        result = self.prepare("claude-only", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(path), result.stderr)
        self.assertIn("lead", result.stderr)
        self.assertEqual(path.read_text(), raw)
        self.assertFalse(path.with_name("claude-only.json.pre-0.2").exists())

    def test_untouched_0_1_0_claude_profile_gets_both_migrations(self):
        original = (FIXTURES / "v0.1.0/claude-only.json").read_bytes()
        path = self.saved("claude-only")
        path.write_bytes(original)
        self.prepare("claude-only")
        old = json.loads(original)
        migrated = json.loads(path.read_text())
        self.assertEqual(list(migrated), renamed(old))
        self.assertEqual(migrated["lead"], old[LEGACY])
        self.assertEqual(migrated["reviewer2"]["model"], "claude-sonnet-5-5")
        self.assertEqual(migrated["adjudicator"]["model"], old["reviewer1"]["model"])
        self.assertEqual(path.with_name("claude-only.json.pre-0.2").read_bytes(), original)
        selection = json.loads((self.home / ".t3/userdata/settings.json").read_text())["defaultModelSelection"]
        self.assertEqual(selection["model"], old[LEGACY]["model"])

    def test_container_start_migrates_saved_profiles(self):
        for profile in role_profile.PROVIDERS:
            with self.subTest(profile=profile):
                legacy = legacy_roles(self.home, profile)
                self.prepare(profile)
                migrated = json.loads(self.saved(profile).read_text())
                self.assertEqual(list(migrated), renamed(legacy))
                selection = json.loads((self.home / ".t3/userdata/settings.json").read_text())["defaultModelSelection"]
                self.assertEqual(selection["model"], legacy[LEGACY]["model"])

    def test_global_instructions_name_the_team_lead(self):
        legacy_roles(self.home, "codex+claude")
        self.prepare("codex+claude")
        for path in (self.home / ".codex/AGENTS.md", self.home / ".claude/CLAUDE.md"):
            text = path.read_text()
            self.assertIn("You are the team lead, unless your task assigns you another role. ", text)
            self.assertNotIn(LEGACY, text)
            self.assertIn("| lead | codex |", (path.parent / "skills/t3-boat/SKILL.md").read_text())

    def test_serve_reports_profile_errors_without_a_traceback(self):
        path = self.saved("codex-only")
        rows = legacy_roles(self.home, "codex-only")
        rows["lead"] = rows[LEGACY]
        path.write_text(json.dumps(rows))
        result = subprocess.run([str(ROOT / "bin/t3-boat"), "serve"], capture_output=True, text=True,
                                env=self.env | {"T3_BOAT_PROFILE": "codex-only"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        lines = result.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn(str(path), lines[0])
        self.assertIn("Next:", lines[0])

    def test_status_reads_an_unmigrated_profile(self):
        profile = "codex-only"
        legacy_roles(self.home, profile)
        self.prepare(profile)
        legacy = legacy_roles(self.home, profile)
        self.saved(profile).with_name(profile + ".json.pre-0.2").unlink()
        output = StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", return_value=None), \
                patch.object(cli, "logged_in", return_value=True), patch.object(cli, "projects", return_value=[]), \
                redirect_stdout(output):
            cli.status(profile, cli.PROFILES[profile])
        self.assertIn(f"[ok] Global profile {profile} applied", output.getvalue())
        self.assertEqual(list(json.loads(self.saved(profile).read_text())), renamed(legacy))
        self.assertTrue(self.saved(profile).with_name(profile + ".json.pre-0.2").exists())

    def test_worker_verify_reads_an_unmigrated_profile(self):
        legacy = legacy_roles(self.home, "codex-only")
        with patch.dict(os.environ, T3_BOAT_PROFILE="codex-only"), \
                patch.object(worker, "provision", side_effect=RuntimeError("stop before Boat")):
            with self.assertRaisesRegex(RuntimeError, "stop before Boat"):
                worker.verify("base")
        self.assertEqual(list(json.loads(self.saved("codex-only").read_text())), renamed(legacy))


class WorkerStartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = self.home / "project"
        self.project.mkdir()
        self.env = {"HOME": str(self.home), "PATH": str(self.home) + ":" + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1", "T3_BOAT_PROFILE": "codex-only"}
        for command in (["git", "init", "-q"], ["git", "config", "remote.origin.url", "https://github.com/o/r.git"],
                        ["git", "config", "boat.environment", "team"]):
            subprocess.run(command, cwd=self.project, env=self.env, check=True)
        boat = self.home / "boat"
        boat.write_text("#!/bin/sh\necho \"$*\" >> \"$HOME/calls\"\nexit 3\n")
        boat.chmod(0o755)

    def start(self, role):
        return subprocess.run([ROOT / "bin/boat-worker", "start", role], cwd=self.project, env=self.env,
                              capture_output=True, text=True)

    def test_start_reads_an_unmigrated_profile(self):
        legacy = legacy_roles(self.home, "codex-only")
        result = self.start("reviewer1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.home / "calls").read_text().startswith("new --json"), result.stderr)
        self.assertEqual(list(json.loads((self.home / "profiles/codex-only.json").read_text())), renamed(legacy))

    def test_parent_roles_are_refused(self):
        legacy_roles(self.home, "codex-only")
        for role, message in (("lead", "child role"), (LEGACY, "now `lead`")):
            with self.subTest(role=role):
                result = self.start(role)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.home / "calls").exists())


if __name__ == "__main__":
    unittest.main()
