"""The team lead follows the canonical agent-playbook lead guide from its first response, only when installed."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from profile_fixtures import seeded_roles
import playbook
import playbook_skills

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("codex-only", "codex+claude", "claude-only")
TARGETS = {"codex": (".codex", "AGENTS.md"), "claudeAgent": (".claude", "CLAUDE.md")}
FIRST_RESPONSE = "before your first response, including ordinary discussion and questions"


def stance():
    """The playbook's own reasoning-stance paragraph of the lead, read from the pinned ROLE.md."""
    text = (playbook.location() / playbook.manifest()["roles"]["lead"]["bundle"][0]).read_text(encoding="utf-8")
    found = [p for p in text.split("\n\n") if p.startswith("Start from the owner's goal and the result they need.")]
    assert len(found) == 1, "the pinned playbook must carry the lead's reasoning stance once"
    return found[0]


class LeadGuideTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}

    def opt(self, answer):
        path = playbook_skills.state_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(answer + "\n")

    def prepare(self, profile):
        result = subprocess.run([sys.executable, str(ROOT / "lib/role_profile.py"), profile], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def instructions(self, provider):
        folder, filename = TARGETS[provider]
        path = self.home / folder / filename
        return path.read_text() if path.exists() else ""

    def guide(self, provider):
        return playbook_skills.skill_path(self.home / TARGETS[provider][0], "lead")

    def test_pinned_playbook_is_the_one_fetched(self):
        pinned = re.search(r"^commit=([0-9a-f]{40})$", (ROOT / "scripts/fetch-playbook").read_text(), re.M)[1]
        self.assertEqual(playbook.commit(), pinned)

    def test_opted_in_lead_follows_canonical_guide_from_first_response_in_every_profile(self):
        self.opt("yes")
        for name in PROFILES:
            with self.subTest(profile=name):
                seeded_roles(self.home, name)
                self.prepare(name)
                for provider in TARGETS:
                    text, guide = self.instructions(provider), self.guide(provider)
                    if provider not in playbook.providers(name):
                        self.assertNotIn("agent-playbook", text)
                        self.assertFalse(guide.exists())
                        continue
                    # The global instruction file is loaded for every conversation, ordinary discussion included,
                    # and the note no longer waits for planning or delegation.
                    self.assertIn(f"As the team lead, load and follow your agent-playbook role guide at {guide} "
                                  f"{FIRST_RESPONSE}, and keep following it for the whole conversation.", text)
                    self.assertNotIn("Before you plan or delegate", text)
                    self.assertEqual(text.count(str(guide)), 1)
                    installed = guide.read_text()
                    self.assertIn("before your first response as the T3 Boat team lead (`lead`), including "
                                  "ordinary discussion", installed.split("\n---\n", 1)[0])
                    self.assertIn(playbook.bundle("lead"), installed)
                    self.assertEqual(installed.count(stance()), 1)

    def test_paragraph_is_not_duplicated_outside_the_playbook_guide(self):
        self.assertNotIn(stance(), (ROOT / "skills/t3-boat/SKILL.md").read_text())
        self.opt("yes")
        seeded_roles(self.home, "codex+claude")
        self.prepare("codex+claude")
        for provider, (folder, _) in TARGETS.items():
            with self.subTest(provider=provider):
                self.assertNotIn(stance(), self.instructions(provider))
                self.assertNotIn(stance(), (self.home / folder / "skills/t3-boat/SKILL.md").read_text())

    def test_opted_out_or_unanswered_owner_gets_no_lead_guide(self):
        for answer in (None, "no"):
            for name in PROFILES:
                with self.subTest(answer=answer, profile=name):
                    if answer:
                        self.opt(answer)
                    seeded_roles(self.home, name)
                    self.prepare(name)
                    for provider in playbook.providers(name):
                        text = self.instructions(provider)
                        self.assertIn("You are the team lead", text)
                        self.assertNotIn("agent-playbook", text)
                        self.assertNotIn(FIRST_RESPONSE, text)
                        self.assertFalse(self.guide(provider).exists())

    def test_opting_out_later_removes_the_note_and_the_guide(self):
        seeded_roles(self.home, "codex+claude")
        self.opt("yes")
        self.prepare("codex+claude")
        self.assertIn(FIRST_RESPONSE, self.instructions("codex"))
        self.opt("no")
        self.prepare("codex+claude")
        for provider in TARGETS:
            with self.subTest(provider=provider):
                self.assertNotIn("agent-playbook", self.instructions(provider))
                self.assertFalse(self.guide(provider).exists())

    def test_no_lead_note_before_a_profile_is_selected(self):
        self.opt("yes")
        self.prepare("codex-only")  # A fresh volume: setup has not saved role rows yet.
        self.assertNotIn(FIRST_RESPONSE, self.instructions("codex"))

    def test_blocked_guide_path_is_never_named(self):
        self.opt("yes")
        seeded_roles(self.home, "codex+claude")
        blocker = self.guide("codex").parent
        blocker.parent.mkdir(parents=True)
        blocker.write_text("not a skill folder\n")
        result = self.prepare("codex+claude")
        self.assertIn(str(blocker), result.stderr)
        self.assertNotIn("agent-playbook-lead", self.instructions("codex"))
        self.assertNotIn(FIRST_RESPONSE, self.instructions("codex"))
        self.assertIn(f"guide at {self.guide('claudeAgent')} {FIRST_RESPONSE}", self.instructions("claudeAgent"))

    def test_owner_guide_at_the_lead_path_is_kept_and_followed(self):
        self.opt("yes")
        seeded_roles(self.home, "claude-only")
        guide = self.guide("claudeAgent")
        guide.parent.mkdir(parents=True)
        guide.write_text("My own lead guide\n")
        self.prepare("claude-only")
        self.assertEqual(guide.read_text(), "My own lead guide\n")
        self.assertIn(f"guide at {guide} {FIRST_RESPONSE}", self.instructions("claudeAgent"))

    def test_only_the_lead_is_sent_to_the_lead_guide(self):
        self.opt("yes")
        self.assertIn("When your task assigns you another role, follow that role's guide instead of this one",
                      playbook_skills.lead_note(self.home, {playbook_skills.skill_path(self.home, "lead")}))
        self.assertIsNone(playbook_skills.worker_guide(self.home, "lead"))
        for key in playbook.role_keys():
            role = playbook.role_of(key)
            if role == "lead":
                continue
            with self.subTest(row=key):
                guide = playbook_skills.worker_guide(self.home, key)
                self.assertIn(playbook.bundle(role), guide)
                self.assertNotIn(stance(), guide)
                self.assertNotIn("agent-playbook-lead", guide)
                self.assertNotIn("first response", playbook_skills.description(role))
        self.opt("no")
        for key in playbook.role_keys():
            self.assertIsNone(playbook_skills.worker_guide(self.home, key))


if __name__ == "__main__":
    unittest.main()
