import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import model_picker
from packages import write_package

MODELS = {
    "codex": {
        "gpt-6.2-sol": ["medium", "high"],
        "gpt-6.10-sol": ["medium", "high"],
        "gpt-7-sol-preview": ["high"],
        "gpt-8-sol": ["medium"],
        "gpt-6.2-astra": ["medium", "high", "xhigh"],
        "gpt-6-luna": ["low"],
    },
    "claudeAgent": {
        "claude-opus-4-20250514": ["high"],
        "claude-opus-4-5": ["high"],
        "claude-sonnet-5-2": ["medium", "high"],
    },
}


class PickerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for replacement in (patch.dict(os.environ, {"HOME": str(self.home)}, clear=True),
                            patch.object(model_picker, "catalog", side_effect=lambda profile: {
                                p: m for p, m in MODELS.items()
                                if p in model_picker.PROVIDERS[profile]})):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.package = write_package(self.home / "team", sets={
            "codex-crew": {"harbor-master": {"provider": "codex", "family": "sol", "effort": "high"}},
            "mixed": {"harbor-master": {"provider": "claude", "family": "opus", "effort": "high"}},
        })

    def saved(self, profile):
        return json.loads((self.home / "profiles" / (profile + ".json")).read_text())

    def choose(self, *entries, profile="codex+claude", attachment=None):
        answers = iter(entries)
        output = io.StringIO()

        def answer(prompt):
            print(prompt, end="")
            try:
                return next(answers)
            except StopIteration:
                raise EOFError("Interrupted test input") from None

        with patch("builtins.input", answer), redirect_stdout(output), redirect_stderr(io.StringIO()):
            model_picker.configure(profile, attachment)
        return output.getvalue()

    def test_without_a_playbook_nothing_is_suggested_and_the_owner_chooses(self):
        output = self.choose("2", "unlisted", "3", "2", "")
        self.assertNotIn("Suggested", output)
        self.assertIn("Select a listed model", output)
        self.assertEqual(self.saved("codex+claude"), {
            "lead": {"provider": "claudeAgent", "model": "claude-sonnet-5-2", "effort": "high"}, "playbook": None})
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertEqual(settings["defaultModelSelection"]["model"], "claude-sonnet-5-2")

    def test_a_single_provider_is_selected_without_asking(self):
        output = self.choose("gpt-6-luna", "", profile="codex-only")
        self.assertIn("Provider: codex", output)
        self.assertIn("Effort: low", output)
        self.assertEqual(self.saved("codex-only")["lead"], {"provider": "codex", "model": "gpt-6-luna", "effort": "low"})

    def test_playbook_suggestion_resolves_the_newest_stable_release_for_the_chosen_role(self):
        attachment = {"path": str(self.package), "model_set": "codex-crew", "lead_role": "harbor-master"}
        output = self.choose("", profile="codex-only", attachment=attachment)
        self.assertIn("Suggested by harbor-crew model set codex-crew for harbor-master: codex sol high", output)
        self.assertEqual(self.saved("codex-only"), {
            "lead": {"provider": "codex", "model": "gpt-6.10-sol", "effort": "high"}, "playbook": attachment})

    def test_saved_choice_is_kept_until_refresh(self):
        self.choose("gpt-6.2-astra", "medium", "", profile="codex-only")
        attachment = {"path": str(self.package), "model_set": "codex-crew", "lead_role": "harbor-master"}
        self.choose("", profile="codex-only", attachment=attachment)
        self.assertEqual(self.saved("codex-only")["lead"]["model"], "gpt-6.2-astra")
        self.choose("refresh", "", profile="codex-only", attachment=attachment)
        self.assertEqual(self.saved("codex-only")["lead"]["model"], "gpt-6.10-sol")

    def test_edit_changes_the_row_and_unavailable_choices_are_asked_again(self):
        self.choose("gpt-6.2-astra", "medium", "", profile="codex-only")
        self.choose("edit", "gpt-6.2-astra", "xhigh", "", profile="codex-only")
        self.assertEqual(self.saved("codex-only")["lead"]["effort"], "xhigh")
        path = self.home / "profiles/codex-only.json"
        state = self.saved("codex-only")
        state["lead"]["model"] = "gpt-retired"
        path.write_text(json.dumps(state))
        raw = path.read_text()
        with self.assertRaises(EOFError):
            self.choose(profile="codex-only")
        self.assertEqual(path.read_text(), raw)  # An interrupted choice changes nothing.
        output = self.choose("gpt-6-luna", "", profile="codex-only")
        self.assertIn("gpt-retired / xhigh is not available", output)
        self.assertEqual(self.saved("codex-only")["lead"]["model"], "gpt-6-luna")

    def test_role_without_an_assignment_or_release_suggests_nothing_usable(self):
        attachment = {"path": str(self.package), "model_set": "mixed", "lead_role": "rigger"}
        output = self.choose("1", "gpt-6-luna", "", attachment=attachment)
        self.assertNotIn("Suggested", output)
        attachment["lead_role"] = "harbor-master"
        catalog = {"codex": MODELS["codex"], "claudeAgent": {"claude-sonnet-5-2": ["high"]}}
        (self.home / "profiles/codex+claude.json").unlink()
        with patch.object(model_picker, "catalog", return_value=catalog):
            output = self.choose("1", "gpt-6-luna", "", attachment=attachment)
        self.assertIn("no listed release supports it", output)


class VersionTests(unittest.TestCase):
    def test_stable_versions_only(self):
        self.assertEqual(model_picker.stable_version("codex", "sol", "gpt-6.10-sol"), ((6, 10), ""))
        self.assertIsNone(model_picker.stable_version("codex", "sol", "gpt-7-sol-preview"))
        self.assertEqual(model_picker.stable_version("claudeAgent", "opus", "claude-opus-4-20250514"),
                         ((4,), "20250514"))
        self.assertEqual(model_picker.stable_version("claudeAgent", "opus", "claude-3-5-opus"), ((3, 5), ""))


if __name__ == "__main__":
    unittest.main()
