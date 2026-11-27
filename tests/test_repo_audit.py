"""Guards for the shipped files: T3 Boat launches workers and leaves every workflow rule to an attached package."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [path for path in (*sorted((ROOT / "bin").iterdir()), *sorted((ROOT / "lib").glob("*.py")),
                             ROOT / "lib/model_snapshot.mjs", *sorted((ROOT / "skills").rglob("*.md")),
                             ROOT / "Dockerfile", ROOT / ".dockerignore", *sorted((ROOT / "scripts").iterdir()),
                             ROOT / "README.md", ROOT / ".github/workflows/release.yml") if path.is_file()]

# Role names and model families of agent-playbook, and the words of its fixed review sequence.
PACKAGE_WORDS = re.compile(r"\b(executor|reviewers?[12]?|arbiter|scout|researcher|adjudicator|orchestrator|"
                           r"[Aa]stra|Sol|Luna|APPROVE|REQUEST_CHANGES|model rules? between)\b")
# Branches for earlier installations: T3 Boat supports clean installs only.
LEGACY_WORDS = re.compile(r"(?i)(migrat|legacy|downgrad|earlier (release|image|installation)|0\.1\.x|"
                          r"setup-pending|profiles/workflow|playbook-skills|playbook\.md|upgrad)")


def without_pin_table(path):
    """A shipped file's text, without the pinned release's file table: content addresses of the suggested
    playbook's files, which name them only so a changed or missing file can be reported. The table must hold
    nothing but path and hash rows."""
    text = path.read_text()
    if path.name != "playbook_pin.py":
        return text
    start = text.index("FILES = {")
    end = text.index("\n}\n", start) + 3
    rows = text[start:end].splitlines()[1:-1]
    row = re.compile(r'    "[A-Za-z0-9._/+-]+": \("100(644|755)", "[0-9a-f]{40}", "[0-9a-f]{64}"\),')
    assert rows and all(row.fullmatch(line) for line in rows), "the pin table holds more than path and hash rows"
    return text[:start] + text[end:]


class AuditTests(unittest.TestCase):
    def test_ai_installer_waits_for_owner_choices_before_commands(self):
        readme = (ROOT / "README.md").read_text()
        start = readme.index("**Installing with an AI agent?**")
        requirements = readme.index("You need a Linux server")
        rule = readme[start:requirements]
        self.assertIn("Before it runs any command, it must ask for and wait for your explicit choices", rule)
        self.assertIn("not consent to choose any of them", rule)
        self.assertIn("must not infer answers", rule)
        for outcome in ("codex-only", "claude-only", "codex+claude", "neither stops the installation"):
            self.assertIn(outcome, rule)
        for choice in ("no playbook", "suggested `agent-playbook`", "custom package path",
                       "explicitly defer it", "only the installation source"):
            self.assertIn(choice, rule)
        start = readme[readme.index("### 1. Start the container"):readme.index("### 2. Choose a playbook")]
        self.assertNotIn("T3_BOAT_PROFILE", start)
        self.assertIn("No provider is selected by this command", start)

    def test_ai_installer_cannot_silently_choose_playbook_or_repository(self):
        readme = (ROOT / "README.md").read_text()
        playbook = readme[readme.index("### 2. Choose a playbook"):readme.index("### 3. Connect your accounts")]
        setup = readme[readme.index("### 3. Connect your accounts"):readme.index("### 4. Connect your devices")]
        self.assertIn("wait for the owner", playbook)
        for choice in ("enter `agent-playbook`", "choose a lead role", "choose a model set"):
            self.assertIn(choice, playbook)
        self.assertIn("complete both sides", setup)
        self.assertIn("**T3:**", setup)
        self.assertIn("**Boat:**", setup)
        self.assertIn("must not silently defer", setup)
        self.assertIn("read the repository's refs", setup)
        self.assertIn("must pause at each undecided choice", setup)
        self.assertIn("status says access is deferred", setup)

    def test_remote_server_uses_connect_before_an_explicit_verified_ssh_fallback(self):
        readme = (ROOT / "README.md").read_text()
        section = readme[readme.index("### 4. Connect your devices"):]
        connect = section.index("t3 connect --headless")
        fallback = section.index("Use a direct SSH route")
        self.assertLess(connect, fallback)
        self.assertIn("needs no public port, SSH tunnel or localhost pairing link", section[:fallback])
        self.assertIn("Creating a pairing link does not open an SSH tunnel", section[:fallback])
        self.assertIn("ssh -N -L 127.0.0.1:3773:127.0.0.1:3773", section[fallback:])
        self.assertIn("must never say the tunnel is open unless it started and verified", section[fallback:])

    def test_shipped_files_name_no_package_role_family_or_review_rule(self):
        for path in SHIPPED:
            text = without_pin_table(path)
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual([m[0] for m in PACKAGE_WORDS.finditer(text)], [])

    def test_shipped_files_have_no_migration_or_downgrade_paths(self):
        for path in SHIPPED:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual([m[0] for m in LEGACY_WORDS.finditer(path.read_text())], [])

    def test_only_the_generic_workflow_and_the_shared_skill_ship_as_instructions(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "skills/t3-boat").iterdir()), ["SKILL.md", "generic.md"])

    def test_no_role_or_model_set_name_is_special(self):
        # No package role (such as agent-playbook's `lead`) or set name is a default; setup asks for each.
        for path in SHIPPED:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("OWNER_ROLE", path.read_text())
                self.assertEqual(re.findall(r"""["'](?:lead|owner)["']\s*(?:\)|,|in\b)""",
                                            path.read_text()) if path.suffix == ".py" else [], [])

    def test_generic_instructions_set_no_branch_pull_request_or_merge_rule(self):
        # What a worker delivers, and how, comes from the brief or an attached package.
        worker = (ROOT / "bin/boat-worker").read_text()
        texts = {
            "SKILL.md": (ROOT / "skills/t3-boat/SKILL.md").read_text(),
            "generic.md": (ROOT / "skills/t3-boat/generic.md").read_text(),
            "WORKER_NOTE": worker[worker.index("WORKER_NOTE ="):worker.index("def worker_instructions")],
            "playbook_skills": (ROOT / "lib/playbook_skills.py").read_text(),
            "boat_profile": (ROOT / "lib/boat_profile.py").read_text(),
        }
        rule = re.compile(r"(?i)(draft|pull request|\bPR\b|task branch|target branch|head branch|\bpush|merge)")
        for label, text in texts.items():
            with self.subTest(label):
                self.assertEqual(rule.findall(text), [])

    def test_the_default_image_build_never_fetches_a_playbook(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        step = next(block for block in dockerfile.split("\n\n") if "fetch-playbook" in block)
        self.assertIn('if [ "$BUNDLE_PLAYBOOK" = 1 ]; then', step)
        self.assertIn("ARG BUNDLE_PLAYBOOK=0", step)


if __name__ == "__main__":
    unittest.main()
