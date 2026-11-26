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
                self.assertIn(f"name: harbor-crew-{playbook.slug(role)}\n", text)
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

    def test_detaching_removes_only_generated_guides(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": "codex-crew", "lead_role": None})
        self.serve("codex-only")
        skills = self.home / ".codex/skills"
        extra = skills / "harbor-crew-rigger/notes.md"
        extra.write_text("mine\n")
        own = skills / "harbor-crew-qa-bot/SKILL.md"
        own.write_text("my own guide\n")
        save(self.home, "codex-only", None)
        self.serve("codex-only")
        self.assertEqual(sorted(p.relative_to(skills).as_posix() for p in skills.rglob("*") if p.is_file()),
                         ["harbor-crew-qa-bot/SKILL.md", "harbor-crew-rigger/notes.md", "t3-boat/SKILL.md"])
        self.assertEqual(own.read_text(), "my own guide\n")
        self.assertIn("## Workers and briefs", self.skill("codex"))

    def test_owner_file_at_a_guide_path_is_kept_and_a_blocked_path_is_not_named(self):
        skills = self.home / ".codex/skills"
        (skills / "harbor-crew-harbor-master").mkdir(parents=True)
        (skills / "harbor-crew-harbor-master/SKILL.md").write_text("owner guide\n")
        (skills / "harbor-crew-rigger").write_text("a file where a folder goes\n")
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": "harbor-master"})
        result = self.serve("codex-only")
        self.assertIn("kept your own", result.stderr)
        self.assertIn("is not installed", result.stderr)
        self.assertEqual((skills / "harbor-crew-harbor-master/SKILL.md").read_text(), "owner guide\n")
        skill = self.skill("codex")
        self.assertIn("| rigger | 3 | not installed |", skill)
        self.assertIn("harbor-master guide at", (self.home / ".codex/AGENTS.md").read_text())

    def test_an_unreadable_package_leaves_the_profile_unapplied_but_t3_starts(self):
        save(self.home, "codex-only", {"path": str(self.package), "model_set": None, "lead_role": None})
        self.serve("codex-only")
        (self.package / "team.json").unlink()
        result = self.serve("codex-only")
        self.assertIn("team.json is missing. T3 starts without the codex-only profile", result.stderr)
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertNotIn("defaultModelSelection", settings)
        self.assertNotIn(boat_profile.BEGIN, (self.home / ".codex/AGENTS.md").read_text())
        self.assertFalse(list((self.home / ".codex/skills").glob("harbor-crew-*")))
        with self.assertRaisesRegex(playbook.PlaybookError, "team.json is missing"):
            boat_profile.applied("codex-only")

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
        self.serve("codex-only")
        self.assertTrue(boat_profile.applied("codex-only"))
        self.assertIn("## Attached playbook", self.skill("codex"))

    def test_codex_override_on_the_server_is_refused(self):
        save(self.home, "codex-only")
        override = self.home / ".codex/AGENTS.override.md"
        override.parent.mkdir()
        override.write_text("mine\n")
        result = self.serve("codex-only", check=False)
        self.assertIn("masks the required global profile", result.stderr)


if __name__ == "__main__":
    unittest.main()
