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
        self.stock.with_name("codex-only.json").write_text(json.dumps(role_picker.playbook.templates("codex-only")))
        families = {
            "lead": ("codex", "astra", "high"),
            "executor": ("claudeAgent", "opus", "high"),
            "reviewer1": ("claudeAgent", "fable", "high"),
            "reviewer2": ("codex", "astra", "high"),
            "scout": ("claudeAgent", "sonnet", "medium"),
            "researcher": ("codex", "sol", "high"),
            "arbiter": ("codex", "astra", "xhigh"),
        }
        self.stock.write_text(json.dumps({
            role: dict(provider=p, model=m, effort=e) for role, (p, m, e) in families.items()
        }))
        for replacement in (
            patch.dict(os.environ, {"HOME": str(self.home)}, clear=True),
            patch.object(role_picker, "stock_templates",
                         lambda profile: json.loads(self.stock.with_name(profile + ".json").read_text())),
            patch.object(role_picker, "catalog", return_value=MODELS),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def choose(self, *entries, profile=PROFILE, workflow="playbook"):
        answers = iter(entries)
        output = io.StringIO()

        def answer(prompt):
            print(prompt, end="")
            try:
                return next(answers)
            except StopIteration:
                raise EOFError("Interrupted test input") from None

        with patch("builtins.input", answer), redirect_stdout(output):
            self.configure(profile, workflow)
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

    def workflow(self):
        return (self.home / "profiles/workflow").read_text().strip()

    def test_generic_setup_picks_one_model_and_saves_only_the_lead_row(self):
        # A fresh generic setup must not need a model for every role, nor a valid reviewer roster.
        output = self.choose("", workflow="generic")
        rows = json.loads(self.saved.read_text())
        self.assertEqual(list(rows), ["lead"])
        self.assertEqual(rows["lead"], dict(provider="codex", model="gpt-6.2-astra", effort="high"))
        self.assertNotIn("reviewer1", output)
        self.assertNotIn("executor", output)
        self.assertEqual(self.workflow(), "generic")
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertEqual(settings["defaultModelSelection"]["model"], "gpt-6.2-astra")

    def test_generic_setup_edits_only_the_lead_row(self):
        with self.assertRaises(EOFError):  # Only the lead row is listed; another number is refused.
            self.choose("2", "executor", workflow="generic")
        self.assertFalse(self.saved.exists())
        self.choose("lead", "claudeAgent", "claude-opus-4-5", "high", "", workflow="generic")
        self.assertEqual(json.loads(self.saved.read_text()),
                         {"lead": dict(provider="claudeAgent", model="claude-opus-4-5", effort="high")})

    def test_generic_setup_keeps_saved_presets_without_validating_their_families(self):
        self.choose("")
        before = json.loads(self.saved.read_text())
        # Presets that break the playbook's model rules or name a model no longer listed stay as saved.
        before["reviewer1"] = dict(before["executor"])
        before["scout"]["model"] = "retired-model"
        before["lead"]["model"] = "gpt-6.10-sol"
        before["lead"]["effort"] = "medium"
        self.saved.write_text(json.dumps(before))
        output = self.choose("refresh", "", workflow="generic")
        after = json.loads(self.saved.read_text())
        self.assertEqual(after["lead"], dict(provider="codex", model="gpt-6.2-astra", effort="high"))
        self.assertEqual({k: v for k, v in after.items() if k != "lead"},
                         {k: v for k, v in before.items() if k != "lead"})
        self.assertNotIn("retired-model", output)
        self.assertEqual(self.workflow(), "generic")

    def test_switching_a_generic_profile_to_the_playbook_fills_and_checks_every_role(self):
        self.choose("", workflow="generic")
        lead = json.loads(self.saved.read_text())["lead"]
        output = self.choose("", workflow="playbook")
        rows = json.loads(self.saved.read_text())
        self.assertEqual(set(rows), set(role_profile_required()))
        self.assertEqual(rows["lead"], lead)
        self.assertIn("lacks", output)
        self.assertEqual(self.workflow(), "playbook")
        # The role workflow enforces the model rules that generic presets skip.
        rows["reviewer1"] = dict(rows["executor"])
        self.saved.write_text(json.dumps(rows))
        with self.assertRaises(EOFError):
            self.choose("", workflow="playbook")
        self.assertEqual(json.loads(self.saved.read_text()), rows)


def role_profile_required():
    import role_profile
    return role_profile.REQUIRED


if __name__ == "__main__":
    unittest.main()
