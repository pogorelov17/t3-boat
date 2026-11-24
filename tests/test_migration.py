"""Saved 0.1.x profiles keep working after the parent role became `lead`."""
import json
import os
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
        # The first run is killed as soon as it opens the backup itself for writing.
        first = self.run_child(
            no_links + "target, backup = sys.argv[1:]\n"
            "def killed(event, args):\n"
            "    if event == 'open' and args[0] == backup and args[1] not in (None, 'r', 'rb'):\n"
            "        os.close(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY))\n"
            "        os._exit(9)\n"
            "sys.addaudithook(killed)\n"
            "role_profile.migrate_profile(role_profile.Path(target))\n", path, backup)
        first.communicate(timeout=60)
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
