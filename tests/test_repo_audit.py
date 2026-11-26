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


class AuditTests(unittest.TestCase):
    def test_shipped_files_name_no_package_role_family_or_review_rule(self):
        for path in SHIPPED:
            text = path.read_text()
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual([m[0] for m in PACKAGE_WORDS.finditer(text)], [])

    def test_shipped_files_have_no_migration_or_downgrade_paths(self):
        for path in SHIPPED:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual([m[0] for m in LEGACY_WORDS.finditer(path.read_text())], [])

    def test_only_the_generic_workflow_and_the_shared_skill_ship_as_instructions(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "skills/t3-boat").iterdir()), ["SKILL.md", "generic.md"])

    def test_the_lead_role_name_is_only_a_setup_suggestion(self):
        # agent-playbook's schema calls its owner-facing role `lead`; setup only suggests it as the default.
        uses = [path.relative_to(ROOT).as_posix() for path in SHIPPED if "OWNER_ROLE" in path.read_text()]
        self.assertEqual(sorted(uses), ["bin/t3-boat", "lib/playbook.py"])


if __name__ == "__main__":
    unittest.main()
