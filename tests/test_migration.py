"""Saved 0.1.x profiles keep working after the parent role became `lead`."""
import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from profile_fixtures import FIXTURES, legacy_roles, seeded_roles

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
        # Hand-formatted on purpose: a re-serialised backup would differ from these bytes.
        text = "{\n" + ",\n".join(f"    {json.dumps(k)}:  {json.dumps(v)}" for k, v in reordered.items()) + "\n}  \n"
        path.write_text(text)
        role_profile.migrate_profile(path)
        migrated = json.loads(path.read_text())
        self.assertEqual(list(migrated), renamed(reordered))
        self.assertEqual(migrated["editor"], reordered["editor"])
        self.assertEqual(migrated["lead"], parent)
        self.assertEqual(path.with_name("codex+claude.json.pre-0.2").read_bytes(), text.encode())

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

    def untouched_0_1_0(self):
        original = (FIXTURES / "v0.1.0/claude-only.json").read_bytes()
        self.saved("claude-only").write_bytes(original)
        return original

    def assert_0_1_0_fully_migrated(self, original):
        path = self.saved("claude-only")
        old = json.loads(original)
        migrated = json.loads(path.read_text())
        self.assertEqual(list(migrated), renamed(old))
        self.assertEqual(migrated["lead"], old[LEGACY])
        self.assertEqual(migrated["reviewer2"]["model"], "claude-sonnet-5-5")
        self.assertEqual(migrated["adjudicator"]["model"], old["reviewer1"]["model"])
        self.assertEqual(path.with_name("claude-only.json.pre-0.2").read_bytes(), original)

    def assert_container_starts(self, original):
        result = self.prepare("claude-only", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_0_1_0_fully_migrated(original)
        selection = json.loads((self.home / ".t3/userdata/settings.json").read_text())["defaultModelSelection"]
        self.assertEqual(selection["model"], json.loads(original)[LEGACY]["model"])

    def test_worker_first_read_fully_migrates_the_untouched_0_1_0_profile(self):
        original = self.untouched_0_1_0()
        with patch.dict(os.environ, T3_BOAT_PROFILE="claude-only"), \
                patch.object(worker, "provision", side_effect=RuntimeError("stop before Boat")):
            with self.assertRaisesRegex(RuntimeError, "stop before Boat"):
                worker.verify("base")
        self.assert_0_1_0_fully_migrated(original)
        self.assert_container_starts(original)

    def test_status_first_read_fully_migrates_the_untouched_0_1_0_profile(self):
        original = self.untouched_0_1_0()
        settings = self.home / ".t3/userdata/settings.json"  # written by the 0.1.x start
        settings.parent.mkdir(parents=True)
        settings.write_text("{}\n")
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", return_value=None), \
                patch.object(cli, "logged_in", return_value=True), patch.object(cli, "projects", return_value=[]), \
                redirect_stdout(StringIO()):
            cli.status("claude-only", cli.PROFILES["claude-only"])
        self.assert_0_1_0_fully_migrated(original)
        self.assert_container_starts(original)

    def test_interrupted_first_start_of_the_untouched_0_1_0_profile_can_be_retried(self):
        original = self.untouched_0_1_0()
        instructions = self.home / ".claude/CLAUDE.md"
        instructions.parent.mkdir()
        instructions.write_text("My notes\n<!-- BEGIN t3-boat -->\n")
        result = self.prepare("claude-only", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker block", result.stderr)
        self.assert_0_1_0_fully_migrated(original)
        instructions.write_text("My notes\n")
        self.assert_container_starts(original)

    def run_child(self, script, *args):
        """Start a Python process that migrates with the given hooks installed."""
        source = self.home / "child.py"
        source.write_text("import os, subprocess, sys, time\n"
                          f"sys.path.insert(0, {str(ROOT / 'lib')!r})\n"
                          "import role_profile\n" + script)
        return subprocess.Popen([sys.executable, str(source), *map(str, args)], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_concurrent_migration_keeps_a_newer_saved_profile(self):
        path = self.saved("codex-only")
        legacy = legacy_roles(self.home, "codex-only")
        paused, go = self.home / "paused", self.home / "go"
        # The first migrator stops just before it replaces the profile.
        first = self.run_child(
            "target, paused, go = sys.argv[1:]\n"
            "replace = os.replace\n"
            "def held(source, destination, *args, **kwargs):\n"
            "    if os.path.realpath(destination) == os.path.realpath(target):\n"
            "        open(paused, 'w').close()\n"
            "        deadline = time.monotonic() + 60\n"
            "        while not os.path.exists(go) and time.monotonic() < deadline:\n"
            "            time.sleep(0.02)\n"
            "    return replace(source, destination, *args, **kwargs)\n"
            "os.replace = held\n"
            "role_profile.migrate_profile(role_profile.Path(target))\n", path, paused, go)
        self.addCleanup(first.kill)
        deadline = time.monotonic() + 30
        while not paused.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not paused.exists():
            self.fail(first.communicate(timeout=60))
        # A second process migrates and saves an extra role meanwhile.
        second = self.run_child(
            "rows = role_profile.read_profile('codex-only')\n"
            "rows['editor'] = {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'low'}\n"
            "role_profile.prepare('codex-only', rows)\n")
        self.addCleanup(second.kill)
        try:
            second.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass  # Expected: it waits for the first migrator.
        go.touch()
        for child in (first, second):
            out, err = child.communicate(timeout=60)
            self.assertEqual(child.returncode, 0, err)
        saved = json.loads(path.read_text())
        self.assertEqual(list(saved), renamed(legacy) + ["editor"])
        self.assertEqual(saved["lead"], legacy[LEGACY])

    def test_an_existing_backup_is_never_replaced_by_a_racing_migrator(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        backup = path.with_name("codex-only.json.pre-0.2")
        # Another process creates the backup right before this migrator publishes its own.
        child = self.run_child(
            "target, backup = sys.argv[1:]\n"
            "competitor = 'import sys; open(sys.argv[1], \"x\").write(\"competitor\\\\n\")'\n"
            "def racing(call):\n"
            "    def wrapped(source, destination, *args, **kwargs):\n"
            "        if os.path.realpath(destination) == os.path.realpath(backup) and not os.path.exists(backup):\n"
            "            subprocess.run([sys.executable, '-c', competitor, backup], check=True)\n"
            "        return call(source, destination, *args, **kwargs)\n"
            "    return wrapped\n"
            "os.replace, os.link = racing(os.replace), racing(os.link)\n"
            "role_profile.migrate_profile(role_profile.Path(target))\n", path, backup)
        self.addCleanup(child.kill)
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(backup.read_text(), "competitor\n")
        self.assertIn('"lead"', path.read_text())

    def test_a_symlinked_profile_stays_a_link_and_its_target_migrates(self):
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        legacy_roles(self.home, "codex-only")
        target = elsewhere / "mine.json"
        original = self.saved("codex-only").read_bytes()
        target.write_bytes(original)
        link = self.saved("codex-only")
        link.unlink()
        link.symlink_to(target)
        rows = role_profile.read_profile("codex-only")
        self.assertIn("lead", rows)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))
        self.assertEqual(list(json.loads(target.read_text())), renamed(json.loads(original)))
        self.assertEqual(target.with_name("mine.json.pre-0.2").read_bytes(), original)
        self.assertFalse(link.with_name("codex-only.json.pre-0.2").exists())
        # Saving a new selection writes through the link too.
        rows["executor"]["effort"] = "low"
        role_profile.prepare("codex-only", rows)
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text())["executor"]["effort"], "low")

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

    def serve(self, profile="codex-only", **env):
        return subprocess.run([str(ROOT / "bin/t3-boat"), "serve"], capture_output=True, text=True,
                              env=self.env | {"T3_BOAT_PROFILE": profile} | env)

    def test_serve_condenses_an_expected_profile_error(self):
        path = self.saved("codex-only")
        rows = legacy_roles(self.home, "codex-only")
        rows["lead"] = rows[LEGACY]
        path.write_text(json.dumps(rows))
        result = self.serve()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {path}: "), lines[0])
        self.assertTrue(lines[0].endswith(
            ". Next: repair the named file in the t3-boat-data volume, then restart the container."), lines[0])

    def test_serve_passes_an_unexpected_failure_through(self):
        legacy_roles(self.home, "codex-only")
        hooks = self.home / "hooks"
        hooks.mkdir()
        (hooks / "sitecustomize.py").write_text(
            "import json, sys\n"
            "if sys.argv[0].endswith('role_profile.py'):\n"
            "    def broken(*args, **kwargs):\n"
            "        raise RuntimeError('simulated defect')\n"
            "    json.loads = broken\n")
        result = self.serve(PYTHONPATH=str(hooks))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback (most recent call last):", result.stderr)
        self.assertTrue(result.stderr.endswith("RuntimeError: simulated defect\n"), result.stderr)
        self.assertNotIn("Next:", result.stderr)

    def test_serve_names_a_truncated_settings_file(self):
        legacy_roles(self.home, "codex-only")
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"providerInstances": {')
        result = self.serve()
        self.assertEqual(result.returncode, 1)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {settings}: "), lines[0])
        self.assertIn("Next:", lines[0])

    def test_status_reports_both_parent_keys_with_the_file_name(self):
        path = self.saved("codex-only")
        rows = legacy_roles(self.home, "codex-only")
        rows["lead"] = rows[LEGACY]
        path.write_text(json.dumps(rows))
        output = StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", return_value=None), \
                patch.object(cli, "logged_in", return_value=True), patch.object(cli, "projects", return_value=[]), \
                redirect_stdout(output):
            self.assertEqual(cli.status("codex-only", cli.PROFILES["codex-only"]), 1)
        report = output.getvalue()
        self.assertIn("[missing] Global profile codex-only applied", report)
        self.assertIn(f"{path}: both `{LEGACY}` and `lead` roles are present", report)
        self.assertIn("[missing] Boat account", report)

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


    # Repair round 2.

    def status_report(self, profile="codex-only"):
        output = StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", return_value=None), \
                patch.object(cli, "logged_in", return_value=True), patch.object(cli, "projects", return_value=[]), \
                redirect_stdout(output):
            code = cli.status(profile, cli.PROFILES[profile])
        return code, output.getvalue()

    def test_serve_names_settings_that_are_not_objects(self):
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        for text in ("[]\n", '{"providerInstances": []}\n', '{"providerInstances": {"codex": 3}}\n'):
            with self.subTest(settings=text):
                legacy_roles(self.home, "codex-only")
                settings.write_text(text)
                result = self.serve()
                self.assertEqual(result.returncode, 1)
                lines = result.stderr.splitlines()
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertTrue(lines[0].startswith(f"T3 Boat profile: {settings}: "), lines[0])
                self.assertIn("Next:", lines[0])
                self.assertEqual(settings.read_text(), text)

    def test_a_profile_that_is_not_utf8_is_named(self):
        path = self.saved("codex-only")
        path.write_bytes(b'{"lead": "\xff"}\n')
        with self.assertRaisesRegex(ValueError, str(path)):
            role_profile.read_profile("codex-only")
        lines = self.serve().stderr.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {path}: "), lines[0])

    def test_programming_errors_are_not_reported_as_profile_errors(self):
        legacy_roles(self.home, "codex-only")
        hooks = self.home / "hooks"
        hooks.mkdir()
        for error in ("TypeError", "AttributeError", "ValueError", "OSError"):
            with self.subTest(error=error):
                (hooks / "sitecustomize.py").write_text(
                    "import json, sys\n"
                    "if sys.argv[0].endswith('role_profile.py'):\n"
                    "    def broken(*args, **kwargs):\n"
                    f"        raise {error}('simulated defect')\n"
                    "    json.dumps = broken\n")
                result = self.serve(PYTHONPATH=str(hooks))
                self.assertEqual(result.returncode, 1)
                self.assertIn("Traceback (most recent call last):", result.stderr)
                self.assertTrue(result.stderr.endswith(f"{error}: simulated defect\n"), result.stderr)
                self.assertNotIn("T3 Boat profile:", result.stderr)
                self.assertNotIn("Next:", result.stderr)

    def test_serve_keeps_earlier_helper_output_before_the_condensed_line(self):
        path = self.saved("codex-only")
        rows = legacy_roles(self.home, "codex-only")
        rows["lead"] = rows[LEGACY]
        path.write_text(json.dumps(rows))
        hooks = self.home / "hooks"
        hooks.mkdir()
        (hooks / "sitecustomize.py").write_text(
            "import sys\n"
            "if sys.argv[0].endswith('role_profile.py'):\n"
            "    sys.stderr.write('warning: first\\nwarning: second\\n')\n")
        lines = self.serve(PYTHONPATH=str(hooks)).stderr.splitlines()
        self.assertEqual(lines[:2], ["warning: first", "warning: second"])
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(lines[2].startswith(f"T3 Boat profile: {path}: "), lines[2])
        self.assertIn("Next:", lines[2])

    def test_status_reports_migration_io_errors(self):
        legacy_roles(self.home, "codex-only")
        with patch.object(cli, "migrate_profile", side_effect=PermissionError(13, "Permission denied", "/x/lockfile")):
            code, report = self.status_report()
        self.assertEqual(code, 1)
        self.assertIn("[missing] Global profile codex-only applied", report)
        self.assertIn("Permission denied", report)
        self.assertIn("/x/lockfile", report)

    def read_only_profiles(self):
        (self.profiles / "active").write_text("codex-only\n")  # written by an earlier start
        self.profiles.chmod(0o555)
        self.addCleanup(self.profiles.chmod, 0o755)
        if os.access(self.profiles, os.W_OK):
            self.skipTest("running as a user that ignores directory permissions")

    def assert_reads_without_lock(self, profile="codex-only"):
        self.assertIn("lead", role_profile.read_profile(profile))
        self.assertEqual(self.prepare(profile, check=False).returncode, 0)
        code, report = self.status_report(profile)
        self.assertIn(f"[ok] Global profile {profile} applied", report)

    def assert_legacy_needs_lock(self, path, reason):
        with self.assertRaisesRegex(ValueError, f"{path}.*{reason}"):
            role_profile.read_profile("codex-only")
        lines = self.serve().stderr.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {path}: "), lines[0])
        self.assertIn(reason, lines[0])
        code, report = self.status_report()
        self.assertIn(str(path), report)
        self.assertIn(LEGACY, path.read_text())

    def test_a_read_only_profiles_directory_with_a_migrated_profile_works(self):
        seeded_roles(self.home, "codex-only")
        self.read_only_profiles()
        self.assert_reads_without_lock()

    def test_a_read_only_profiles_directory_with_a_legacy_profile_is_a_clear_error(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        self.read_only_profiles()
        self.assert_legacy_needs_lock(path, "Permission denied")

    def unsupported_locks(self):
        hooks = self.home / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "sitecustomize.py").write_text(
            "import errno, fcntl\n"
            "def unsupported(*args, **kwargs):\n"
            "    raise OSError(errno.ENOLCK, 'No locks available')\n"
            "fcntl.flock = unsupported\n")
        self.env["PYTHONPATH"] = str(hooks)
        flock = patch.object(role_profile.fcntl, "flock", side_effect=OSError(37, "No locks available"))
        flock.start()
        self.addCleanup(flock.stop)

    def test_unsupported_locks_with_a_migrated_profile_work(self):
        seeded_roles(self.home, "codex-only")
        self.unsupported_locks()
        self.assert_reads_without_lock()

    def test_unsupported_locks_with_a_legacy_profile_are_a_clear_error(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        self.unsupported_locks()
        self.assert_legacy_needs_lock(path, "No locks available")

    def test_an_empty_backup_from_an_interrupted_run_is_replaced(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        original = path.read_bytes()
        backup = path.with_name("codex-only.json.pre-0.2")
        backup.touch()
        self.assertTrue(role_profile.migrate_profile(path))
        self.assertEqual(backup.read_bytes(), original)

    def test_backup_without_hard_links_survives_an_interruption(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        original = path.read_bytes()
        backup = path.with_name("codex-only.json.pre-0.2")
        no_links = ("import errno\n"
                    "def no_link(*args, **kwargs):\n"
                    "    raise OSError(errno.EPERM, 'Operation not permitted')\n"
                    "os.link = no_link\n")
        # The first run is killed when it publishes the backup, after its temporary file is written.
        first = self.run_child(
            no_links + "def killed(*args, **kwargs):\n"
            "    os._exit(9)\n"
            "role_profile.no_replace = killed\n"
            "role_profile.migrate_profile(role_profile.Path(sys.argv[1]))\n", path)
        first.communicate(timeout=60)
        self.assertEqual(first.returncode, 9)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(backup.exists())
        second = self.run_child(no_links + "role_profile.migrate_profile(role_profile.Path(sys.argv[1]))\n", path)
        out, err = second.communicate(timeout=60)
        self.assertEqual(second.returncode, 0, err)
        self.assertEqual(backup.read_bytes(), original)
        self.assertIn('"lead"', path.read_text())

    def test_the_backup_exists_before_the_profile_is_replaced(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        original = path.read_bytes()
        backup = path.with_name("codex-only.json.pre-0.2")
        seen = []
        replace = os.replace

        def checked(source, destination, *args, **kwargs):
            if os.path.realpath(destination) == os.path.realpath(path):
                seen.append(backup.read_bytes() if backup.exists() else None)
            return replace(source, destination, *args, **kwargs)

        with patch.object(role_profile.os, "replace", checked):
            self.assertTrue(role_profile.migrate_profile(path))
        self.assertEqual(seen, [original])

    def test_saving_a_profile_waits_for_the_lock(self):
        path = self.saved("codex-only")
        rows = seeded_roles(self.home, "codex-only")
        held, release = self.home / "held", self.home / "release"
        holder = self.run_child(
            "target, held, release = sys.argv[1:]\n"
            "with role_profile.profile_lock(role_profile.Path(target)):\n"
            "    open(held, 'w').close()\n"
            "    deadline = time.monotonic() + 60\n"
            "    while not os.path.exists(release) and time.monotonic() < deadline:\n"
            "        time.sleep(0.02)\n", path, held, release)
        self.addCleanup(holder.kill)
        deadline = time.monotonic() + 30
        while not held.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not held.exists():
            self.fail(holder.communicate(timeout=60))
        rows["executor"]["effort"] = "low"
        saver = self.run_child(
            "import json\n"
            "role_profile.save_profile(role_profile.Path(sys.argv[1]), json.loads(sys.argv[2]))\n",
            path, json.dumps(rows))
        self.addCleanup(saver.kill)
        with self.assertRaises(subprocess.TimeoutExpired):
            saver.wait(timeout=2)
        self.assertNotEqual(json.loads(path.read_text())["executor"]["effort"], "low")
        release.touch()
        out, err = saver.communicate(timeout=60)
        self.assertEqual(saver.returncode, 0, err)
        self.assertEqual(json.loads(path.read_text())["executor"]["effort"], "low")
        holder.communicate(timeout=60)

    def test_stale_temporary_files_are_removed_under_the_lock(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        before = set(os.listdir(self.profiles))
        # A migrator killed just before it replaces the profile leaves its temporary file behind.
        child = self.run_child(
            "target = sys.argv[1]\n"
            "replace = os.replace\n"
            "def killed(source, destination, *args, **kwargs):\n"
            "    if os.path.realpath(destination) == os.path.realpath(target):\n"
            "        os._exit(9)\n"
            "    return replace(source, destination, *args, **kwargs)\n"
            "os.replace = killed\n"
            "role_profile.migrate_profile(role_profile.Path(target))\n", path)
        child.communicate(timeout=60)
        leftovers = set(os.listdir(self.profiles)) - before - {"codex-only.json.pre-0.2", ".codex-only.json.lock"}
        self.assertTrue(leftovers)
        for name in leftovers:
            self.assertTrue(name.startswith(".codex-only.json.t3-boat-tmp-"), name)
        unrelated = self.profiles / "notes.tmp"
        unrelated.write_text("mine\n")
        self.assertIn("lead", role_profile.read_profile("codex-only"))
        self.assertEqual(set(os.listdir(self.profiles)) - before,
                         {"codex-only.json.pre-0.2", ".codex-only.json.lock", "notes.tmp"})


    # Repair round 3

    def changed_rows(self):
        rows = seeded_roles(self.home, "codex-only")
        rows["executor"]["effort"] = "low"
        return rows

    def assert_save_refused(self, path, rows, reason):
        before = path.read_bytes()
        with patch.object(role_profile, "durable_temporary") as temporary:
            with self.assertRaisesRegex(role_profile.ProfileError, f"{path}.*{reason}"):
                role_profile.save_profile(path, rows)
            with self.assertRaisesRegex(role_profile.ProfileError, f"{path}.*{reason}"):
                role_profile.prepare("codex-only", rows)
        temporary.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_a_changed_save_is_refused_when_the_held_lock_file_is_read_only(self):
        path = self.saved("codex-only")
        rows = self.changed_rows()
        lock = self.profiles / ".codex-only.json.lock"
        lock.touch()
        lock.chmod(0o444)
        if os.access(lock, os.W_OK):
            self.skipTest("running as a user that ignores file permissions")
        held, release = self.home / "held", self.home / "release"
        holder = self.run_child(
            "import fcntl\n"
            "lock, held, release = sys.argv[1:]\n"
            "with open(lock) as handle:\n"
            "    fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "    open(held, 'w').close()\n"
            "    deadline = time.monotonic() + 60\n"
            "    while not os.path.exists(release) and time.monotonic() < deadline:\n"
            "        time.sleep(0.02)\n", lock, held, release)
        self.addCleanup(holder.kill)
        deadline = time.monotonic() + 30
        while not held.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not held.exists():
            self.fail(holder.communicate(timeout=60))
        self.assert_save_refused(path, rows, "Permission denied")
        release.touch()
        holder.communicate(timeout=60)

    def test_a_changed_save_is_refused_when_locks_are_unsupported(self):
        path = self.saved("codex-only")
        rows = self.changed_rows()
        with patch.object(role_profile.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            self.assert_save_refused(path, rows, "No locks available")

    def test_an_identical_save_without_a_lock_returns_quietly(self):
        path = self.saved("codex-only")
        rows = seeded_roles(self.home, "codex-only")
        before = path.read_bytes()
        with patch.object(role_profile.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")), \
                patch.object(role_profile, "durable_temporary") as temporary:
            role_profile.save_profile(path, rows)
        temporary.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_tables_in_other_unicode_encodings_are_rejected(self):
        for encoding in ("utf-16", "utf-16-le", "utf-32"):
            with self.subTest(encoding=encoding):
                path = self.saved("codex-only")
                legacy = legacy_roles(self.home, "codex-only")
                path.write_bytes((json.dumps(legacy, indent=2) + "\n").encode(encoding))
                before = path.read_bytes()
                backup = path.with_name("codex-only.json.pre-0.2")
                with self.assertRaisesRegex(role_profile.ProfileError, str(path)):
                    role_profile.read_profile("codex-only")
                result = self.serve()
                self.assertEqual(result.returncode, 1)
                lines = result.stderr.splitlines()
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertTrue(lines[0].startswith(f"T3 Boat profile: {path}: "), lines[0])
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(backup.exists())

    def test_status_shows_an_unparsable_profile(self):
        path = self.saved("codex-only")
        path.write_text('{"lead": ')
        code, report = self.status_report()
        self.assertEqual(code, 1)
        self.assertIn(str(path), report)
        self.assertIn("Expecting value", report)

    def test_a_defect_in_the_first_parse_is_not_retried(self):
        seeded_roles(self.home, "codex-only")
        loads = json.loads
        calls = []

        def broken_once(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise ValueError("defect")
            return loads(*args, **kwargs)

        with patch.object(role_profile.json, "loads", broken_once):
            with self.assertRaises(ValueError) as raised:
                role_profile.read_profile("codex-only")
        self.assertNotIsInstance(raised.exception, role_profile.ProfileError)
        self.assertEqual(str(raised.exception), "defect")

    def defect_hook(self, source):
        hooks = self.home / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "sitecustomize.py").write_text("import sys\nif sys.argv[0].endswith('role_profile.py'):\n" + source)
        return str(hooks)

    def test_a_defect_in_validation_keeps_its_traceback(self):
        seeded_roles(self.home, "codex-only")
        hooks = self.defect_hook(
            "    def entered(frame, event, arg):\n"
            "        if event == 'call' and frame.f_code.co_name == 'validate_profile':\n"
            "            sys.setprofile(None)\n"
            "            raise ValueError('simulated defect')\n"
            "    sys.setprofile(entered)\n")
        result = self.serve(PYTHONPATH=hooks)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback (most recent call last):", result.stderr)
        self.assertTrue(result.stderr.endswith("ValueError: simulated defect\n"), result.stderr)
        self.assertNotIn("T3 Boat profile:", result.stderr)
        self.assertNotIn("Next:", result.stderr)

    def test_invalid_rows_are_still_named(self):
        path = self.saved("codex-only")
        rows = seeded_roles(self.home, "codex-only")
        rows["lead"]["provider"] = "claudeAgent"
        path.write_text(json.dumps(rows, indent=2) + "\n")
        lines = self.serve().stderr.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {path}: invalid provider for lead"), lines[0])

    def test_a_defect_reading_settings_keeps_its_traceback(self):
        seeded_roles(self.home, "codex-only")
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}\n")
        hooks = self.defect_hook(
            "    import pathlib\n"
            "    read_bytes = pathlib.Path.read_bytes\n"
            "    def broken(self):\n"
            "        if self.name == 'settings.json':\n"
            "            raise ValueError('simulated defect')\n"
            "        return read_bytes(self)\n"
            "    pathlib.Path.read_bytes = broken\n")
        result = self.serve(PYTHONPATH=hooks)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback (most recent call last):", result.stderr)
        self.assertTrue(result.stderr.endswith("ValueError: simulated defect\n"), result.stderr)
        self.assertNotIn("T3 Boat profile:", result.stderr)

    def test_settings_that_are_not_utf8_are_named(self):
        seeded_roles(self.home, "codex-only")
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_bytes(b'{"x": "\xff"}\n')
        lines = self.serve().stderr.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"T3 Boat profile: {settings}: "), lines[0])
        self.assertIn("Next:", lines[0])

    def test_stale_temporary_files_in_a_read_only_directory_are_left(self):
        seeded_roles(self.home, "codex-only")
        (self.profiles / ".codex-only.json.lock").touch()
        stale = self.profiles / ".codex-only.json.t3-boat-tmp-stale"
        stale.write_text("partial")
        self.read_only_profiles()
        self.assertIn("lead", role_profile.read_profile("codex-only"))
        self.assertTrue(stale.exists())

    def test_a_refused_final_replacement_is_a_clear_error(self):
        path = self.saved("codex-only")
        legacy_roles(self.home, "codex-only")
        original = path.read_bytes()
        before = set(os.listdir(self.profiles))
        replace = os.replace

        def refused(source, destination, *args, **kwargs):
            if os.path.realpath(destination) == os.path.realpath(path):
                raise PermissionError(errno.EACCES, "Permission denied", str(destination))
            return replace(source, destination, *args, **kwargs)

        with patch.object(role_profile.os, "replace", refused):
            with self.assertRaisesRegex(role_profile.ProfileError,
                                        f"{path}: needs migration to 0.2 but cannot be written: Permission denied"):
                role_profile.migrate_profile(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(set(os.listdir(self.profiles)) - before, {"codex-only.json.pre-0.2", ".codex-only.json.lock"})

    def test_settings_entries_this_tool_does_not_use_are_left_alone(self):
        seeded_roles(self.home, "codex-only")
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"providerInstances": {"custom": 7}}\n')
        result = self.prepare("codex-only", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(settings.read_text())["providerInstances"]["custom"], 7)


    # Repair round 4.

    def test_status_reads_a_non_ascii_profile_in_the_c_locale(self):
        profile = "codex-only"
        rows = seeded_roles(self.home, profile)
        rows["executor"]["note"] = "caf\u00e9"
        self.saved(profile).write_bytes((json.dumps(rows, indent=2, ensure_ascii=False) + "\n").encode())
        self.prepare(profile)
        script = (
            "import sys\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from unittest.mock import patch\n"
            "cli = SourceFileLoader('cli', sys.argv[1]).load_module()\n"
            "print('parent', cli.parent_role(sys.argv[2])['model'])\n"
            "with patch.object(cli, 't3_online', return_value=True), patch.object(cli, 'probe', return_value=None), \\\n"
            "        patch.object(cli, 'logged_in', return_value=True), patch.object(cli, 'projects', return_value=[]):\n"
            "    cli.status(sys.argv[2], cli.PROFILES[sys.argv[2]])\n"
        )
        env = dict(self.env, LC_ALL="C", PYTHONUTF8="0")
        result = subprocess.run([sys.executable, "-c", script, str(ROOT / "bin/t3-boat"), profile],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertIn(f"parent {rows['lead']['model']}", result.stdout, result.stderr)
        self.assertIn(f"[ok] Global profile {profile} applied", result.stdout, result.stderr)

    def test_setup_names_an_unreadable_profile_before_installing_or_signing_in(self):
        profile = "codex+claude"
        path = self.saved(profile)
        path.write_bytes(b'{"lead": "\xff"}\n')
        before = path.read_bytes()
        output = StringIO()
        prompts = []
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "run") as run, \
                patch.object(cli, "probe", return_value=None) as probe, \
                patch.object(cli, "logged_in", return_value=False), \
                patch.object(sys.modules["role_picker"], "catalog", return_value={}), \
                patch("builtins.input", side_effect=lambda prompt: prompts.append(prompt) or ""), \
                redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, re.escape(str(path))) as raised:
                cli.setup(profile, cli.PROFILES[profile])
        self.assertNotIsInstance(raised.exception, UnicodeDecodeError)
        self.assertEqual(len(str(raised.exception).splitlines()), 1)
        self.assertEqual(run.call_args_list, [])
        self.assertEqual(probe.call_args_list, [])
        self.assertNotIn("Sign in", output.getvalue())
        self.assertEqual(prompts, [])
        self.assertEqual(path.read_bytes(), before)

    def test_main_setup_exits_non_zero_naming_an_unreadable_profile(self):
        path = self.saved("claude-only")
        path.write_bytes(b'{"lead": "\xff"}\n')
        tools = self.home / "tools"
        tools.mkdir()
        for name in ("install-claude", "claude", "codex"):
            tool = tools / name
            tool.write_text('#!/bin/sh\necho "$0 $*" >> "$HOME/invoked"\nexit 1\n')
            tool.chmod(0o755)
        script = (
            "import sys\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from unittest.mock import patch\n"
            "sys.argv = [sys.argv[1], 'setup']\n"
            "cli = SourceFileLoader('__main__', sys.argv[0])\n"
            "import urllib.request, io, json\n"
            "class Online(io.BytesIO):\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *a): return False\n"
            "with patch('urllib.request.urlopen', return_value=Online(json.dumps({'environmentId': 'e'}).encode())):\n"
            "    cli.load_module()\n"
        )
        env = dict(self.env, T3_BOAT_PROFILE="claude-only", PATH=os.pathsep.join([str(tools), self.env["PATH"]]))
        result = subprocess.run([sys.executable, "-c", script, str(ROOT / "bin/t3-boat")], env=env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(path), result.stderr.splitlines()[0])
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Sign in", result.stdout)
        self.assertFalse((self.home / "invoked").exists(), (self.home / "invoked").read_text()
                         if (self.home / "invoked").exists() else "")


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
