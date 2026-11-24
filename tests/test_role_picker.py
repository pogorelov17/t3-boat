import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

PROFILE = "codex+claude"
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
        "claude-fable-5-2": ["high"],
    },
}


class RolePickerTests(unittest.TestCase):
    def setUp(self):
        import role_picker
        self.configure = role_picker.configure_roles
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.saved = self.home / "profiles" / (PROFILE + ".json")
        stock = self.home / "stock"
        (stock / "profiles").mkdir(parents=True)
        self.stock = stock / "profiles" / (PROFILE + ".json")
        self.stock.with_name("codex-only.json").write_bytes((ROOT / "profiles/codex-only.json").read_bytes())
        families = {
            "lead": ("codex", "astra", "high"),
            "executor": ("claudeAgent", "opus", "high"),
            "reviewer1": ("claudeAgent", "fable", "high"),
            "reviewer2": ("codex", "astra", "high"),
            "scout": ("claudeAgent", "sonnet", "medium"),
            "researcher": ("codex", "sol", "high"),
            "adjudicator": ("codex", "astra", "xhigh"),
        }
        self.stock.write_text(json.dumps({
            role: dict(provider=p, model=m, effort=e) for role, (p, m, e) in families.items()
        }))
        for replacement in (
            patch.dict(os.environ, {"HOME": str(self.home)}, clear=True),
            patch.object(role_picker, "ROOT", stock),
            patch.object(role_picker, "catalog", return_value=MODELS),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def choose(self, *entries, profile=PROFILE):
        answers = iter(entries)
        output = io.StringIO()

        def answer(prompt):
            print(prompt, end="")
            try:
                return next(answers)
            except StopIteration:
                raise EOFError("Interrupted test input") from None

        with patch("builtins.input", answer), redirect_stdout(output):
            self.configure(profile)
        return output.getvalue()

    def test_enter_resolves_latest_stable_compatible_family_and_applies_parent(self):
        output = self.choose("")
        rows = json.loads(self.saved.read_text())
        self.assertEqual(rows["researcher"]["model"], "gpt-6.10-sol")
        self.assertEqual(rows["executor"]["model"], "claude-opus-4-5")
        self.assertIn("gpt-6.10-sol", output)
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertEqual(settings["defaultModelSelection"]["model"], "gpt-6.2-astra")

    def test_rerun_keeps_custom_pins_until_explicit_refresh(self):
        self.choose("")
        before = json.loads(self.saved.read_text())
        before["researcher"]["model"] = "gpt-6.2-sol"
        before["editor"] = dict(before["researcher"])
        self.saved.write_text(json.dumps(before))
        self.choose("")
        self.assertEqual(json.loads(self.saved.read_text()), before)
        output = self.choose("refresh", "")
        after = json.loads(self.saved.read_text())
        self.assertEqual(after["researcher"]["model"], "gpt-6.10-sol")
        self.assertEqual(after["editor"], before["editor"])
        self.assertIn("gpt-6.2-sol", output)
        self.assertIn("gpt-6.10-sol", output)

    def test_setup_migrates_a_saved_0_1_x_profile(self):
        self.choose("")
        rows = json.loads(self.saved.read_text())
        legacy = {("orchestrator" if role == "lead" else role): row for role, row in rows.items()}
        self.saved.write_text(json.dumps(legacy))
        output = self.choose("")
        self.assertIn("Saved role assignments", output)
        self.assertEqual(json.loads(self.saved.read_text()), rows)
        self.assertEqual(list(json.loads(self.saved.read_text())), list(rows))
        self.assertEqual(json.loads(self.saved.with_name(self.saved.name + ".pre-0.2").read_text()), legacy)

    def test_numbered_options_allow_editing_effort_without_changing_model(self):
        self.choose("1", "", "unlisted-model", "", "1", "")
        rows = json.loads(self.saved.read_text())
        self.assertEqual(rows["lead"], dict(provider="codex", model="gpt-6.2-astra", effort="medium"))

    def test_unavailable_pin_requires_explicit_replacement_and_abort_preserves_file(self):
        self.choose("")
        before = json.loads(self.saved.read_text())
        before["scout"]["effort"] = "unsupported"
        before["researcher"]["model"] = "gpt-unknown"
        raw = json.dumps(before)
        self.saved.write_text(raw)
        with self.assertRaises(EOFError):
            self.choose("")
        self.assertEqual(self.saved.read_text(), raw)
        self.choose(
            "scout", "claudeAgent", "claude-sonnet-5-2", "medium",
            "researcher", "claudeAgent", "claude-opus-4-5", "high", "",
        )
        rows = json.loads(self.saved.read_text())
        self.assertEqual(rows["scout"]["effort"], "medium")
        self.assertEqual(rows["researcher"], dict(provider="claudeAgent", model="claude-opus-4-5", effort="high"))

    def test_codex_default_sol_pair_uses_newest_version_supporting_both_efforts(self):
        profile = "codex-only"
        self.choose("", profile=profile)
        path = self.saved.parent / (profile + ".json")
        rows = json.loads(path.read_text())
        self.assertEqual(rows["executor"]["model"], "gpt-6.10-sol")
        self.assertEqual(rows["reviewer2"]["model"], "gpt-6.10-sol")
        self.choose("executor", "codex", "gpt-8-sol", "medium", "", profile=profile)
        rows = json.loads(path.read_text())
        self.assertEqual(rows["executor"]["model"], "gpt-8-sol")
        self.assertEqual(rows["reviewer2"]["model"], "gpt-6.10-sol")

    def test_codex_sol_pair_with_no_shared_release_requires_manual_choices(self):
        profile = "codex-only"
        disjoint = {**MODELS["codex"], "gpt-6.2-sol": ["medium"], "gpt-6.10-sol": ["high"]}
        with patch("role_picker.catalog", return_value={"codex": disjoint}):
            with self.assertRaises(EOFError):
                self.choose("", profile=profile)
        self.assertFalse((self.saved.parent / (profile + ".json")).exists())

    def test_missing_family_cannot_be_accepted_by_pressing_enter(self):
        stock = json.loads(self.stock.read_text())
        stock["executor"]["model"] = "absent"
        self.stock.write_text(json.dumps(stock))
        alias_only = {**MODELS, "claudeAgent": {**MODELS["claudeAgent"], "absent": ["high"]}}
        with patch("role_picker.catalog", return_value=alias_only):
            with self.assertRaises(EOFError):
                self.choose("")
        self.assertFalse(self.saved.exists())

    def test_review_collision_is_repairable_before_saving(self):
        self.choose("")
        output = self.choose(
            "reviewer1", "claudeAgent", "claude-opus-4-5", "high", "",
            "reviewer1", "claudeAgent", "claude-fable-5-2", "high", "",
        )
        self.assertIn("reviewer1", output)
        rows = json.loads(self.saved.read_text())
        self.assertEqual(rows["reviewer1"]["model"], "claude-fable-5-2")


if __name__ == "__main__":
    unittest.main()
