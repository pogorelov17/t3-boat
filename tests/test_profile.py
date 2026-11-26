import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import boat_profile
import playbook
import playbook_skills
from packages import CUSTOM_ROLES, write_package

LEADS = {
    "codex-only": {"provider": "codex", "model": "gpt-6.2-astra", "effort": "high"},
    "claude-only": {"provider": "claudeAgent", "model": "claude-opus-5-5", "effort": "high"},
    "codex+claude": {"provider": "codex", "model": "gpt-6.2-astra", "effort": "high"},
}
BASES = {"codex": ".codex", "claudeAgent": ".claude"}
FILES = {"codex": "AGENTS.md", "claudeAgent": "CLAUDE.md"}


def save(home, profile, attachment=None, lead=None):
    path = Path(home) / "profiles" / (profile + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lead": lead or LEADS[profile], "playbook": attachment}))
    return path


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
        patcher = patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.package = write_package(self.home / "teams/harbor")

    def serve(self, profile, check=True):
        """What the container start runs before T3."""
        return subprocess.run([sys.executable, str(ROOT / "lib/boat_profile.py"), profile], env=self.env,
                              capture_output=True, text=True, check=check)

    def files(self):
        return {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and "teams" not in p.parts}

    def skill(self, provider):
        return (self.home / BASES[provider] / "skills/t3-boat/SKILL.md").read_text()

    def test_fresh_volume_waits_for_setup_before_selecting_a_model(self):
        self.serve("codex-only")
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertTrue(settings["providerInstances"]["codex"]["enabled"])
        self.assertNotIn("defaultModelSelection", settings)
        self.assertFalse((self.home / "profiles/codex-only.json").exists())
        self.assertFalse((self.home / ".codex/skills").exists())

    def test_every_profile_without_a_playbook_is_idempotent_and_keeps_user_settings(self):
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"existingSetting": 5, "providerInstances": {"codex": {"name": "My Codex"}}}))
        instructions = self.home / ".codex/AGENTS.md"
        instructions.parent.mkdir()
        instructions.write_text("Personal instructions\n")
        for name in ("codex-only", "codex+claude", "claude-only"):
            with self.subTest(name=name):
                save(self.home, name)
                self.serve(name)
                snapshot = self.files()
                self.serve(name)
                self.assertEqual(snapshot, self.files())
                self.assertTrue(boat_profile.applied(name))
                cfg = json.loads(settings.read_text())
                self.assertEqual(cfg["existingSetting"], 5)
                self.assertEqual(cfg["providerInstances"]["codex"]["name"], "My Codex")
                self.assertEqual(cfg["providerInstances"]["codex"]["enabled"], name != "claude-only")
                self.assertEqual(cfg["providerInstances"]["claudeAgent"]["enabled"], name != "codex-only")
                self.assertEqual(cfg["defaultModelSelection"]["model"], LEADS[name]["model"])
                self.assertTrue(instructions.read_text().startswith("Personal instructions\n"))
                for provider in boat_profile.OPTION:
                    enabled = provider in boat_profile.PROVIDERS[name]
                    path = self.home / BASES[provider] / FILES[provider]
                    self.assertEqual(path.exists() and boat_profile.BEGIN in path.read_text(), enabled)
                    if enabled:
                        skill = self.skill(provider)
                        for heading in ("## Machine boundary", "## Parent updates", "## Workers and briefs",
                                        "## Lead model", "## Cleanup"):
                            self.assertIn(heading, skill)
                        self.assertNotIn("## Attached playbook", skill)
                        self.assertNotIn("playbook", path.read_text())

    def test_custom_team_is_installed_for_every_enabled_provider_with_its_own_names_and_counts(self):
        save(self.home, "codex+claude", {"path": str(self.package), "model_set": "both-providers",
                                         "lead_role": "harbor-master"})
        self.serve("codex+claude")
        package = playbook.load(str(self.package))
        for provider in boat_profile.OPTION:
            base = self.home / BASES[provider]
            for role in CUSTOM_ROLES:
                guide = playbook_skills.skill_path(base, package, role)
                text = guide.read_text()
                self.assertIn(f"name: {playbook.skill_name('harbor-crew', role)}\n", text)
                self.assertTrue(guide.parent.name.startswith(f"playbook-harbor-crew-{playbook.slug(role)}-"))
                self.assertTrue(text.endswith(package.bundle(role)))
                self.assertIn(playbook_skills.MARK, text)
            skill = self.skill(provider)
            self.assertIn("## Attached playbook", skill)
            self.assertNotIn("## Workers and briefs", skill)
            self.assertIn("follow the `harbor-master` guide", skill)
            for role, count in CUSTOM_ROLES.items():
                self.assertIn(f"| {role} | {count} | {playbook_skills.skill_path(base, package, role)} |", skill)
            self.assertIn("### Model assignment: `both-providers`", skill)
            self.assertIn("| rigger | 3 | claudeAgent | haiku | medium |", skill)
            self.assertIn('"reason": "test"', skill)
            note = (base / FILES[provider]).read_text()
            self.assertIn("load and follow its harbor-master guide at "
                          + str(playbook_skills.skill_path(base, package, "harbor-master")), note)
        self.assertTrue(boat_profile.applied("codex+claude"))

    def test_without_a_lead_role_the_lead_is_sent_to_no_guide(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": None})
        self.serve("codex-only")
        self.assertNotIn("guide at", (self.home / ".codex/AGENTS.md").read_text())
        skill = self.skill("codex")
        self.assertIn("Setup assigned you none of these roles", skill)
        self.assertIn("No model set of the package is selected", skill)
        self.assertFalse((self.home / ".claude/skills").exists())

    def test_generic_start_preserves_owner_skills_that_quote_the_marker(self):
        save(self.home, "codex-only")
        for name in ("team-notes", "playbook-custom-team-01234567"):
            path = self.home / ".codex/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            contents = (f"---\nname: {name}\ndescription: Notes\n---\n\nExample:\n"
                        f"{playbook_skills.MARK} example. t3-boat replaces this file; delete this line to keep your edits. -->\n")
            path.write_text(contents)
            self.serve("codex-only")
            self.assertEqual(path.read_text(), contents)

    def test_detaching_removes_only_generated_guides(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": "codex-crew", "lead_role": None})
        self.serve("codex-only")
        skills = self.home / ".codex/skills"
        package = playbook.load(str(self.package))
        rigger = playbook.skill_name(package.name, "rigger")
        qa_bot = playbook.skill_name(package.name, "qa_bot")
        extra = skills / rigger / "notes.md"
        extra.write_text("mine\n")
        own = skills / qa_bot / "SKILL.md"
        own.write_text("my own guide\n")
        save(self.home, "codex-only", None)
        self.serve("codex-only")
        self.assertEqual(sorted(p.relative_to(skills).as_posix() for p in skills.rglob("*") if p.is_file()),
                         sorted([f"{qa_bot}/SKILL.md", f"{rigger}/notes.md", "t3-boat/SKILL.md"]))
        self.assertEqual(own.read_text(), "my own guide\n")
        self.assertIn("## Workers and briefs", self.skill("codex"))

    def test_symlinked_role_directory_is_occupied_during_attach_refresh_and_detach(self):
        role = self.home / ".codex/skills" / playbook.skill_name("harbor-crew", "rigger")
        role.parent.mkdir(parents=True)
        target = self.home / "owner-directory"
        target.mkdir()
        role.symlink_to(target, target_is_directory=True)
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": "rigger"})
        for _ in range(2):
            self.serve("codex-only")
            self.assertIn("| rigger | 3 | not installed |", self.skill("codex"))
            self.assertEqual(list(target.iterdir()), [])
        save(self.home, "codex-only", None)
        self.serve("codex-only")
        self.assertTrue(role.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_owner_file_at_a_guide_path_is_kept_and_a_blocked_path_is_not_named(self):
        skills = self.home / ".codex/skills"
        master = skills / playbook.skill_name("harbor-crew", "harbor-master")
        master.mkdir(parents=True)
        (master / "SKILL.md").write_text("owner guide\n")
        (skills / playbook.skill_name("harbor-crew", "rigger")).write_text("a file where a folder goes\n")
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": "harbor-master"})
        result = self.serve("codex-only")
        self.assertIn("kept your own", result.stderr)
        self.assertIn("is not installed", result.stderr)
        self.assertEqual((master / "SKILL.md").read_text(), "owner guide\n")
        skill = self.skill("codex")
        self.assertIn("| rigger | 3 | not installed |", skill)
        self.assertIn("harbor-master guide at", (self.home / ".codex/AGENTS.md").read_text())

    def assert_disabled(self, profile, message):
        """T3 starts with only a note pointing to the log and status: no Boat skill, guides, model or applied record."""
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertNotIn("defaultModelSelection", settings)
        for provider in boat_profile.PROVIDERS[profile]:
            base = self.home / BASES[provider]
            note = (base / FILES[provider]).read_text()
            self.assertIn("T3 Boat is not ready", note)
            self.assertIn("t3-boat status` name the file", note)
            self.assertNotIn(message, note)  # The diagnostic stays in the log and status, out of the managed block.
            self.assertNotIn("skills/", note)
            self.assertEqual([p for p in (base / "skills").rglob("*") if p.is_file()] if (base / "skills").exists()
                             else [], [])
        self.assertFalse(boat_profile.applied_path(profile).exists())
        with self.assertRaisesRegex(playbook.PlaybookError, message):
            boat_profile.applied(profile)

    def test_an_unreadable_package_leaves_the_profile_unapplied_but_t3_starts(self):
        save(self.home, "codex+claude", {"path": str(self.package), "model_set": "both-providers",
                                         "lead_role": "harbor-master"})
        self.serve("codex+claude")
        self.assertTrue(boat_profile.applied("codex+claude"))
        (self.package / "team.json").unlink()
        result = self.serve("codex+claude")
        self.assertIn("team.json is missing. T3 starts without the codex+claude profile", result.stderr)
        self.assert_disabled("codex+claude", "team.json is missing")

    def test_every_unreadable_package_file_is_found_before_anything_is_generated_and_restoring_it_recovers(self):
        save(self.home, "codex+claude", {"path": str(self.package), "model_set": "both-providers",
                                         "lead_role": "harbor-master"})
        self.serve("codex+claude")
        applied = self.files()
        rules = self.package / "crew/qa_bot/ROLE.md"  # The last role's guide, so earlier guides read fine.
        original = rules.read_bytes()
        damage = {
            "not UTF-8": (lambda: rules.write_bytes(b"# qa_bot\n\xff\xfe broken\n"), "not UTF-8 text"),
            "permission": (lambda: rules.chmod(0), "Permission denied"),
            "model set": (lambda: (self.package / "assignments/both-providers.json").write_text(json.dumps(
                {"roles": {"rigger": {"provider": ["codex"], "family": "luna", "effort": "low"}}})), "provider"),
            "deep": (lambda: (self.package / "assignments/codex-crew.json").write_text("[" * 100000),
                     "nested too deeply"),
        }
        for label, (corrupt, message) in damage.items():
            if label == "permission" and os.geteuid() == 0:
                continue  # Root reads every file.
            with self.subTest(label):
                corrupt()
                result = self.serve("codex+claude")
                self.assertIn(message, result.stderr)
                self.assertIn("T3 starts without the codex+claude profile", result.stderr)
                self.assert_disabled("codex+claude", message)
                rules.chmod(0o644)
                rules.write_bytes(original)
                write_package(self.package)
                self.serve("codex+claude")
                # The same files as before the damage; T3's settings may list their keys in another order.
                recovered = self.files()
                settings = self.home / ".t3/userdata/settings.json"
                self.assertEqual(json.loads(recovered.pop(settings)), json.loads(applied[settings]))
                self.assertEqual(recovered, {path: data for path, data in applied.items() if path != settings})
                self.assertTrue(boat_profile.applied("codex+claude"))

    def test_a_fresh_volume_has_only_the_note_to_run_setup(self):
        self.serve("codex+claude")
        for provider in boat_profile.OPTION:
            self.assertIn("setup has not saved it yet", (self.home / BASES[provider] / FILES[provider]).read_text())
        self.assertFalse((self.home / ".codex/skills").exists())

    def test_a_provider_the_profile_no_longer_enables_loses_its_boat_skill_and_note(self):
        save(self.home, "codex+claude")
        self.serve("codex+claude")
        save(self.home, "claude-only")
        self.serve("claude-only")
        self.assertFalse((self.home / ".codex/skills/t3-boat").exists())
        self.assertNotIn(boat_profile.BEGIN, (self.home / ".codex/AGENTS.md").read_text())
        self.assertIn(boat_profile.SKILL_MARK, self.skill("claudeAgent"))

    def test_package_and_role_names_cannot_take_the_boat_skill_or_each_others_names(self):
        package = write_package(self.home / "teams/t3", name="t3", roles={"boat": 1, "qa_bot": 1, "QA-bot": 2}, sets={})
        save(self.home, "codex-only", {"path": str(package), "model_set": None, "lead_role": "boat"})
        self.serve("codex-only")
        self.assertIn("# Boat project workflow", self.skill("codex"))
        names = sorted(p.parent.name for p in (self.home / ".codex/skills").glob("*/SKILL.md"))
        self.assertEqual(len(names), 4)
        self.assertEqual(names[-1], "t3-boat")
        self.assertTrue(all(name.startswith("playbook-t3-") for name in names[:-1]), names)
        loaded = playbook.load(str(package))
        for role in loaded.roles:
            guide = playbook_skills.skill_path(self.home / ".codex", loaded, role)
            self.assertTrue(guide.read_text().endswith(loaded.bundle(role)))

    def test_workers_get_the_package_as_applied_until_the_next_start(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": "harbor-master"})
        self.serve("codex-only")
        steps = self.package / "crew/rigger/how/steps.md"
        steps.write_text("EDITED-STEPS\n")
        state, applied = boat_profile.read_applied("codex-only")
        self.assertIn("STEPS-rigger", applied["roles"]["rigger"])  # What the lead's skills were made from.
        self.assertFalse(boat_profile.applied("codex-only"))  # Status reports the edit as not applied yet.
        self.serve("codex-only")
        self.assertIn("EDITED-STEPS", boat_profile.read_applied("codex-only")[1]["roles"]["rigger"])
        guide = playbook_skills.skill_path(self.home / ".codex", playbook.load(str(self.package)), "rigger")
        self.assertIn("EDITED-STEPS", guide.read_text())

    def test_workers_are_refused_until_the_saved_choice_is_applied(self):
        save(self.home, "codex-only")
        with self.assertRaisesRegex(boat_profile.ProfileError, "not applied"):
            boat_profile.read_applied("codex-only")
        self.serve("codex-only")
        self.assertIsNone(boat_profile.read_applied("codex-only")[1])
        save(self.home, "codex-only", lead=dict(LEADS["codex-only"], effort="low"))  # Saved, not yet applied.
        with self.assertRaisesRegex(boat_profile.ProfileError, "not applied"):
            boat_profile.read_applied("codex-only")

    def test_an_invalid_profile_stops_before_any_change_and_names_the_file(self):
        save(self.home, "claude-only")
        self.serve("claude-only")
        before = self.files()
        cases = {
            "provider": {"lead": dict(LEADS["claude-only"], provider="codex"), "playbook": None},
            "keys": {"lead": LEADS["claude-only"], "playbook": None, "roles": {}},
            "no lead": {"playbook": None},
            "attachment": {"lead": LEADS["claude-only"], "playbook": {"path": str(self.package)}},
            "array": [],
        }
        path = self.home / "profiles/claude-only.json"
        for label, data in cases.items():
            with self.subTest(label):
                path.write_text(json.dumps(data))
                result = self.serve("claude-only", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(path), result.stderr)
                after = self.files()
                after.pop(path)
                expected = dict(before)
                expected.pop(path)
                self.assertEqual(after, expected)

    def test_setup_choices_are_checked_against_the_package_before_saving(self):
        cases = [
            ({"path": str(self.package), "model_set": "both-providers", "lead_role": None}, "claude-only", "provider"),
            ({"path": str(self.package), "model_set": "absent", "lead_role": None}, "codex-only", "no model set"),
            ({"path": str(self.package), "model_set": None, "lead_role": "lead"}, "codex-only", "no role 'lead'"),
            ({"path": str(self.home / "nowhere"), "model_set": None, "lead_role": None}, "codex-only", "not a directory"),
        ]
        for attachment, profile, message in cases:
            with self.subTest(message), redirect_stderr(StringIO()):
                with self.assertRaisesRegex(playbook.PlaybookError, message):
                    boat_profile.prepare(profile, {"lead": LEADS[profile], "playbook": attachment})
                self.assertFalse((self.home / "profiles" / (profile + ".json")).exists())

    def test_an_interrupted_regeneration_is_reported_and_finished_by_the_next_start(self):
        save(self.home, "codex-only")
        self.serve("codex-only")
        attachment = {"path": str(self.package), "model_set": "codex-crew", "lead_role": "harbor-master"}
        written = []
        original = boat_profile.write

        def interrupted(path, data):
            if written:
                raise KeyboardInterrupt
            written.append(path)
            original(path, data)

        with patch.object(boat_profile, "write", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            boat_profile.prepare("codex-only", {"lead": LEADS["codex-only"], "playbook": attachment})
        self.assertEqual(written, [self.home / "profiles/codex-only.json"])  # The whole choice, in one write.
        self.assertFalse(boat_profile.applied("codex-only"))
        with self.assertRaisesRegex(boat_profile.ProfileError, "not applied"):
            boat_profile.read_applied("codex-only")  # No worker starts from the half-written choice.
        self.serve("codex-only")
        self.assertTrue(boat_profile.applied("codex-only"))
        self.assertIn("## Attached playbook", self.skill("codex"))

    def test_an_interrupted_reapply_of_an_edited_package_leaves_no_record_until_it_completes(self):
        # The saved choice stays the same while the package's contents change, so the earlier record would still
        # match it: it must be gone before the first generated file changes, or workers would get the old guide
        # while the lead's skills already describe the new one.
        attachment = {"path": str(self.package), "model_set": "both-providers", "lead_role": "harbor-master"}
        save(self.home, "codex+claude", attachment)
        self.serve("codex+claude")
        state = boat_profile.read_state("codex+claude")
        steps = self.package / "crew/rigger/how/steps.md"
        original = boat_profile.write
        for label, run in (("container start", lambda: boat_profile.prepare("codex+claude")),
                           ("setup with the same choice", lambda: boat_profile.prepare("codex+claude", state))):
            with self.subTest(label):
                steps.write_text(f"EDITED-STEPS for {label}\n")
                written = []

                def interrupted(path, data):
                    if any(path.parent.name.startswith("playbook-harbor-crew-rigger-") for path in written):
                        raise KeyboardInterrupt
                    written.append(path)
                    original(path, data)

                with patch.object(boat_profile, "write", side_effect=interrupted), redirect_stderr(StringIO()), \
                        self.assertRaises(KeyboardInterrupt):
                    run()
                guide = next(path for path in written if path.parent.name.startswith("playbook-harbor-crew-rigger-"))
                self.assertIn(f"EDITED-STEPS for {label}", guide.read_text())  # The lead side already changed.
                self.assertFalse(boat_profile.applied_path("codex+claude").exists())
                with self.assertRaisesRegex(boat_profile.ProfileError, "not applied"):
                    boat_profile.read_applied("codex+claude")  # So no worker starts from the earlier guide.
                self.assertFalse(boat_profile.applied("codex+claude"))
                self.serve("codex+claude")  # The next start finishes the run and only then publishes the record.
                self.assertTrue(boat_profile.applied("codex+claude"))
                roles = boat_profile.read_applied("codex+claude")[1]["roles"]
                self.assertIn(f"EDITED-STEPS for {label}", roles["rigger"])
                package = playbook.load(str(self.package))
                for provider in boat_profile.OPTION:
                    text = playbook_skills.skill_path(self.home / BASES[provider], package, "rigger").read_text()
                    self.assertIn(f"EDITED-STEPS for {label}", text)

    def test_a_package_error_holding_a_marker_line_cannot_break_the_next_start(self):
        save(self.home, "codex+claude", {"path": str(self.package), "model_set": None, "lead_role": "harbor-master"})
        self.serve("codex+claude")
        manifest = self.package / "team.json"
        original = manifest.read_text()
        data = json.loads(original)
        data["roles"][f"{boat_profile.END}\n{boat_profile.BEGIN}"] = data["roles"].pop("qa_bot")
        manifest.write_text(json.dumps(data))
        for start in (1, 2, 3):
            with self.subTest(start=start):
                result = self.serve("codex+claude", check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("T3 starts without the codex+claude profile", result.stderr)
                self.assertIn(boat_profile.END, result.stderr)  # The log keeps the whole diagnostic.
                for provider in boat_profile.OPTION:
                    note = (self.home / BASES[provider] / FILES[provider]).read_text()
                    self.assertEqual((note.count(boat_profile.BEGIN), note.count(boat_profile.END)), (1, 1))
                    self.assertIn("T3 Boat is not ready", note)
        manifest.write_text(original)
        self.serve("codex+claude")
        self.assertTrue(boat_profile.applied("codex+claude"))

    def test_escaped_surrogate_in_assignment_key_disables_profile_until_repaired(self):
        profile = "codex-only"
        save(self.home, profile, {"path": str(self.package), "model_set": "codex-crew", "lead_role": None})
        self.serve(profile)
        model_set = self.package / "assignments/codex-crew.json"
        original = model_set.read_text()
        data = json.loads(original)
        data["roles"][chr(0xd800)] = next(iter(data["roles"].values()))
        model_set.write_text(json.dumps(data))
        result = self.serve(profile)
        self.assertIn(f"{model_set}: JSON contains an unpaired Unicode surrogate", result.stderr)
        self.assert_disabled(profile, "unpaired Unicode surrogate")
        model_set.write_text(original)
        self.serve(profile)
        self.assertTrue(boat_profile.applied(profile))

    def test_a_nul_byte_in_a_guide_disables_the_profile_before_any_worker(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": None})
        self.serve("codex-only")
        (self.package / "crew/rigger/how/steps.md").write_bytes(b"STEPS\0hidden\n")
        result = self.serve("codex-only")
        self.assertIn("steps.md: contains a NUL byte at offset 5", result.stderr)
        self.assert_disabled("codex-only", "contains a NUL byte")

    def test_lead_instructions_resolve_the_worker_project_before_delegating(self):
        save(self.home, "codex-only")
        self.serve("codex-only")
        skill = self.skill("codex")
        steps = skill[skill.index("## Start a worker"):skill.index("## Parent updates")]
        resolve = steps.index("call `t3_project_list` with `environmentId` set to that environment ID")
        delegate = steps.index("`delegate_task` with a `target` holding the linked `environmentId`, the resolved "
                               "`projectId`, and the `providerInstanceId`, exact `model` and `options`")
        self.assertLess(steps.index("**worker T3 project folder**"), resolve)
        self.assertLess(resolve, delegate)
        self.assertIn("whose `workspaceRoot` is the printed worker project folder", steps)
        self.assertIn("never derive the project from the repository name, from the checkout path", steps)
        worker = (ROOT / "bin/boat-worker").read_text()
        self.assertIn('print(f"Worker T3 project folder: {folder}"', worker)  # The folder the skill matches.

    def test_an_unreadable_generated_file_is_replaced_and_an_unreadable_owner_file_is_named(self):
        save(self.home, "codex-only")
        self.serve("codex-only")
        if os.geteuid() == 0:
            self.skipTest("root reads every file")
        skill = self.home / ".codex/skills/t3-boat/SKILL.md"
        skill.chmod(0)
        self.serve("codex-only")
        self.assertIn("# Boat project workflow", skill.read_text())
        settings = self.home / ".t3/userdata/settings.json"
        settings.chmod(0)
        self.addCleanup(settings.chmod, 0o600)
        result = self.serve("codex-only", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{settings}: cannot be read: Permission denied", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_codex_override_on_the_server_is_refused(self):
        save(self.home, "codex-only")
        override = self.home / ".codex/AGENTS.override.md"
        override.parent.mkdir()
        override.write_text("mine\n")
        result = self.serve("codex-only", check=False)
        self.assertIn("masks the required global profile", result.stderr)


if __name__ == "__main__":
    unittest.main()
