import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import model_picker
import playbook
import playbook_skills
from packages import write_package
from test_model_picker import MODELS
from test_profile import save

cli = SourceFileLoader("t3_boat_setup_tests", str(ROOT / "bin/t3-boat")).load_module()


def answering(*entries):
    answers = iter(entries)

    def answer(prompt):
        print(prompt, end="")
        try:
            return next(answers)
        except StopIteration:
            raise EOFError("Interrupted test input") from None
    return patch("builtins.input", answer)


class ChoosePlaybookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "PATH": os.environ["PATH"]}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.custom = write_package(self.home / "teams/harbor")
        # A stand-in for the image's bundled copy, shaped like agent-playbook: a `lead` role and per-profile sets.
        self.bundled = write_package(self.home / "bundled", name="agent-playbook", roles={"lead": 1, "builder": 2},
                                     sets={"codex-only": {"lead": {"provider": "codex", "family": "astra", "effort": "high"}},
                                           "claude-only": {"lead": {"provider": "claude", "family": "opus", "effort": "high"}}})
        bundled = patch.object(playbook, "BUNDLED", self.bundled)
        bundled.start()
        self.addCleanup(bundled.stop)

    def choose(self, profile, current, *entries):
        output = io.StringIO()
        with answering(*entries), redirect_stdout(output):
            result = cli.choose_playbook(profile, current)
        return result, output.getvalue()

    def test_enter_on_a_fresh_install_attaches_nothing(self):
        result, output = self.choose("codex-only", None, "")
        self.assertIsNone(result)
        self.assertIn("Current playbook: none", output)
        self.assertIn("'bundled' for agent-playbook (version 7.1.0)", output)

    def test_no_role_or_model_set_name_is_a_default_and_each_is_chosen_explicitly(self):
        # Neither a role named `lead` nor a set named like the profile is preselected.
        result, output = self.choose("claude-only", None, "bundled", "", "lead", "", "claude-only")
        self.assertEqual(result, {"path": str(self.bundled), "model_set": "claude-only", "lead_role": "lead"})
        self.assertIn("Role the T3 team lead follows ('none' for no role): ", output)
        self.assertIn("Select a listed role by number or name, or 'none'.", output)
        self.assertIn("Select a listed model set by number or name, or 'none'.", output)
        self.assertNotIn("[lead]", output)
        self.assertNotIn("[claude-only]", output)
        self.assertIn("Model sets that need a provider claude-only does not enable: codex-only", output)
        self.assertIn("roles lead (1), builder (2)", output)

    def test_none_selects_no_model_set_even_when_one_fits(self):
        result, _ = self.choose("codex-only", None, str(self.custom), "rigger", "none")
        self.assertEqual(result, {"path": str(self.custom), "model_set": None, "lead_role": "rigger"})

    def test_the_bundled_choice_is_offered_only_when_the_image_has_one(self):
        with patch.object(playbook, "BUNDLED", self.home / "absent"):
            result, output = self.choose("codex-only", None, "bundled", "")
        self.assertIsNone(result)
        self.assertNotIn("'bundled'", output)
        self.assertIn("bundled: enter an absolute directory path", output)

    def test_custom_path_after_an_invalid_one_with_explicit_role_choice(self):
        broken = write_package(self.home / "teams/broken")
        (broken / "assignments/codex-crew.json").write_text("{")
        result, output = self.choose("codex-only", None, str(self.home / "nowhere"), "teams/harbor", str(broken),
                                     str(self.custom), "", "rigger", "", "codex-crew")
        self.assertIn("is not a directory. Enter another path", output)
        self.assertIn("codex-crew.json: Expecting", output)
        self.assertIn("enter an absolute directory path", output)
        self.assertIn("Select a listed role", output)  # No `lead` role, so nothing is the default.
        self.assertEqual(result, {"path": str(self.custom), "model_set": "codex-crew", "lead_role": "rigger"})

    def test_no_role_and_no_fitting_set(self):
        result, output = self.choose("claude-only", None, str(self.custom), "none")
        self.assertEqual(result, {"path": str(self.custom), "model_set": None, "lead_role": None})
        self.assertIn("No model set of harbor-crew fits claude-only", output)

    def test_enter_keeps_the_saved_attachment_and_none_detaches_it(self):
        current = {"path": str(self.custom), "model_set": "codex-crew", "lead_role": "qa_bot"}
        result, output = self.choose("codex+claude", current, "", "", "")
        self.assertEqual(result, current)
        self.assertIn("team lead follows qa_bot", output)
        self.assertEqual(self.choose("codex+claude", current, "none")[0], None)


class SetupWithPlaybookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "PATH": os.environ["PATH"],
                                          "GIT_CONFIG_NOSYSTEM": "1"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.custom = write_package(self.home / "teams/harbor")
        self.env = {"id": "abc", "name": "base", "safeForThirdParties": False, "passGithub": True,
                    "passAgentsCredentials": True, "credentialProfileId": None, "selectedRepositories": []}
        self.credentials = [{"id": "p", "isDefault": True, "connected": ["chatgpt-subscription", "claude-subscription"]}]

    def run_setup(self, profile, *answers):
        output = io.StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "logged_in", return_value=True), \
                patch.object(cli, "add_workspace"), patch.object(cli, "run"), patch.object(cli, "probe", return_value=""), \
                patch.object(cli, "boat_records", side_effect=lambda command, key: {
                    "environments": [self.env], "profiles": self.credentials}[key]), \
                patch.object(model_picker, "catalog", return_value=MODELS), answering(*answers), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            cli.setup(profile, cli.PROFILES[profile])
            code = cli.status(profile, cli.PROFILES[profile])
        return code, output.getvalue()

    def test_setup_attaches_a_custom_team_and_status_reports_it(self):
        code, output = self.run_setup("codex+claude", str(self.custom), "cartographer", "both-providers", "", "", "")
        self.assertEqual(code, 0, output)
        self.assertIn("Playbook: harbor-crew (version 7.1.0) at " + str(self.custom)
                      + ", model set both-providers, team lead follows cartographer", output)
        self.assertIn("Suggested by harbor-crew model set both-providers for cartographer: claudeAgent sonnet high",
                      output)
        state = json.loads((self.home / "profiles/codex+claude.json").read_text())
        self.assertEqual(state["playbook"], {"path": str(self.custom), "model_set": "both-providers",
                                             "lead_role": "cartographer"})
        self.assertEqual(state["lead"], {"provider": "claudeAgent", "model": "claude-sonnet-5-2", "effort": "high"})
        guide = playbook_skills.skill_path(self.home / ".claude", playbook.load(str(self.custom)), "cartographer")
        self.assertTrue(guide.is_file())

    def test_a_package_that_breaks_later_is_reported_and_setup_can_detach_it(self):
        save(self.home, "codex-only", {"path": str(self.custom), "model_set": None, "lead_role": None})
        (self.custom / "team.json").unlink()
        code, output = self.run_setup("codex-only", "none", "", "", "")
        self.assertIn("Current playbook: cannot be read", output)
        self.assertEqual(code, 0, output)
        self.assertIsNone(json.loads((self.home / "profiles/codex-only.json").read_text())["playbook"])

    def test_status_names_a_broken_package_and_keeps_the_vm_check_receipt_valid(self):
        code, _ = self.run_setup("codex-only", str(self.custom), "none", "none", "gpt-6-luna", "", "", "")
        self.assertEqual(code, 0)
        receipt = cli.read_receipt("codex-only", None)
        # Attaching another playbook does not change what the VM check covered.
        attachment = {"path": str(self.custom), "model_set": "codex-crew", "lead_role": "rigger"}
        state = json.loads((self.home / "profiles/codex-only.json").read_text())
        save(self.home, "codex-only", attachment, lead=state["lead"])
        self.assertEqual(cli.check_signature("codex-only", self.env, "p"), receipt["signature"])
        (self.custom / "team.json").write_text("{")
        output = io.StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "logged_in", return_value=True), \
                patch.object(cli, "probe", return_value=None), redirect_stdout(output):
            self.assertEqual(cli.status("codex-only", ("codex",)), 1)
        self.assertIn("[missing] Global profile codex-only applied", output.getvalue())
        self.assertIn("team.json", output.getvalue())


if __name__ == "__main__":
    unittest.main()
