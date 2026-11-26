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
import boat_profile
import model_picker
import playbook
import playbook_skills
from packages import write_package
from test_model_picker import MODELS
from test_profile import save

cli = SourceFileLoader("t3_boat_setup_tests", str(ROOT / "bin/t3-boat")).load_module()
worker = SourceFileLoader("boat_worker_setup_tests", str(ROOT / "bin/boat-worker")).load_module()


def answering(*entries):
    """Answer each prompt in turn; a callable entry runs at its prompt and returns the answer."""
    answers = iter(entries)

    def answer(prompt):
        print(prompt, end="")
        try:
            entry = next(answers)
        except StopIteration:
            raise EOFError("Interrupted test input") from None
        return entry() if callable(entry) else entry
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
            result, package = cli.choose_playbook(profile, current)
        # The package the owner was shown travels with the attachment it was read for.
        self.assertEqual(package and str(package.root), result and result["path"])
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

    def test_numbered_options_reach_roles_and_sets_with_numeric_and_reserved_names(self):
        assignment = {"2": {"provider": "codex", "family": "test", "effort": "high"}}
        path = write_package(self.home / "teams/numbers", roles={"2": 1, "none": 1},
                             sets={"2": assignment, "none": assignment})
        for reply, expected in (("1", "2"), ("2", "none"), ("none", None)):
            with self.subTest(reply=reply):
                result, _ = self.choose("codex-only", None, str(path), reply, reply)
                self.assertEqual(result["lead_role"], expected)
                self.assertEqual(result["model_set"], expected)

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


class ReleaseSwitchTests(unittest.TestCase):
    """A configured path that names the current release, such as a link the owner switches to deploy one."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "PATH": os.environ["PATH"],
                                          "GIT_CONFIG_NOSYSTEM": "1"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Release A runs one rigger and suggests codex sol high for the lead; release B runs three and luna low.
        for release, riggers, family, effort in (("A", 1, "sol", "high"), ("B", 3, "luna", "low")):
            root = write_package(self.home / "teams/releases" / release, roles={"harbor-master": 1, "rigger": riggers},
                                 sets={"crew": {"harbor-master": {"provider": "codex", "family": family,
                                                                  "effort": effort}}},
                                 version=f"{ord(release) - 64}.0.0")
            with (root / "crew/rigger/ROLE.md").open("a") as guide:
                guide.write(f"RELEASE-{release}\n")
        self.current = self.home / "teams/current"
        self.current.symlink_to("releases/A")
        self.env = {"id": "abc", "name": "base", "safeForThirdParties": False, "passGithub": True,
                    "passAgentsCredentials": True, "credentialProfileId": None, "selectedRepositories": []}
        self.credentials = [{"id": "p", "isDefault": True, "connected": ["chatgpt-subscription"]}]

    def switch(self, release):
        """Point the link at another release in one step, as a deploy does."""
        swap = self.home / "teams/.current.new"
        swap.symlink_to("releases/" + release)
        os.replace(swap, self.current)

    def switching_to(self, release, answer):
        def reply():
            self.switch(release)
            return answer
        return reply

    def run_setup(self, *answers):
        output = io.StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "logged_in", return_value=True), \
                patch.object(cli, "add_workspace"), patch.object(cli, "run"), patch.object(cli, "probe", return_value=""), \
                patch.object(cli, "boat_records", side_effect=lambda command, key: {
                    "environments": [self.env], "profiles": self.credentials}[key]), \
                patch.object(model_picker, "catalog", return_value=MODELS), answering(*answers), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            cli.setup("codex-only", cli.PROFILES["codex-only"])
        return output.getvalue()

    def status(self):
        output = io.StringIO()
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "logged_in", return_value=True), \
                patch.object(cli, "probe", return_value=None), redirect_stdout(output):
            code = cli.status("codex-only", cli.PROFILES["codex-only"])
        return code, output.getvalue()

    def generated(self):
        """What the lead and its workers get: the saved choice, the applied record, the lead skill, the rigger
        guide installed on this server and the guide a rigger worker receives."""
        record = json.loads((self.home / "profiles/codex-only.applied.json").read_text())
        guides = list((self.home / ".codex/skills").glob("playbook-harbor-crew-rigger-*/SKILL.md"))
        self.assertEqual(len(guides), 1)
        return (json.loads((self.home / "profiles/codex-only.json").read_text()), record,
                (self.home / ".codex/skills/t3-boat/SKILL.md").read_text(), guides[0].read_text(),
                worker.worker_plan("rigger")[3])

    def assert_release(self, release, riggers, model):
        state, record, skill, guide, worker_guide = self.generated()
        other = "B" if release == "A" else "A"
        self.assertEqual(state["playbook"], {"path": str(self.current), "model_set": "crew",
                                             "lead_role": "harbor-master"})
        self.assertEqual(state["lead"]["model"], model)
        self.assertEqual(record["profile"], state)
        self.assertEqual(record["playbook"]["label"], f"harbor-crew (version {ord(release) - 64}.0.0)")
        self.assertIn(f"| rigger | {riggers} |", skill)
        for text in (record["playbook"]["roles"]["rigger"], guide, worker_guide):
            self.assertIn(f"RELEASE-{release}", text)
            self.assertNotIn(f"RELEASE-{other}", text)

    def test_setup_applies_the_release_it_displayed_when_the_link_switches_during_the_prompts(self):
        output = self.run_setup(str(self.current), self.switching_to("B", "harbor-master"), "crew", "", "", "")
        self.assertEqual(os.readlink(self.current), "releases/B")  # The switch happened mid-setup.
        self.assertIn(f"Attaching harbor-crew (version 1.0.0) at {self.current}: roles harbor-master (1), "
                      "rigger (1).", output)
        self.assertIn("Suggested by harbor-crew model set crew for harbor-master: codex sol high", output)
        self.assertNotIn("luna", output)
        # Model selection, the lead's files and the workers' guides all come from release A, as displayed.
        self.assertIn("lead: codex / gpt-6.10-sol / high", output)
        self.assertIn("| harbor-master | 1 | codex | sol | high |", self.generated()[2])
        self.assert_release("A", 1, "gpt-6.10-sol")
        # The configured path now names B: status says so instead of calling A current.
        code, report = self.status()
        self.assertEqual(code, 1)
        self.assertIn("[missing] Global profile codex-only applied", report)

        # The next container start reads what the saved path names: release B, for the lead and the workers.
        with redirect_stderr(io.StringIO()):
            boat_profile.prepare("codex-only")
        self.assert_release("B", 3, "gpt-6.10-sol")  # The saved lead model changes only by the owner's choice.
        self.assertIn("[ok] Global profile codex-only applied", self.status()[1])

        # Setup offers B's suggestion, and refresh takes it.
        output = self.run_setup("", "", "", "refresh", "", "", "")
        self.assertIn(f"Attaching harbor-crew (version 2.0.0) at {self.current}: roles harbor-master (1), "
                      "rigger (3).", output)
        self.assertIn("Suggested by harbor-crew model set crew for harbor-master: codex luna low", output)
        self.assert_release("B", 3, "gpt-6-luna")
        self.assertIn("| harbor-master | 1 | codex | luna | low |", self.generated()[2])
        self.assertIn("[ok] Global profile codex-only applied", self.status()[1])

    def test_cancelling_setup_after_the_link_switched_keeps_the_applied_choice(self):
        self.run_setup(str(self.current), "harbor-master", "crew", "", "", "")
        self.assert_release("A", 1, "gpt-6.10-sol")
        files = [self.home / "profiles/codex-only.json", self.home / "profiles/codex-only.applied.json",
                 self.home / ".codex/skills/t3-boat/SKILL.md", self.home / ".codex/AGENTS.md"]
        before = [path.read_bytes() for path in files]
        with self.assertRaises(EOFError):
            self.run_setup("", self.switching_to("B", "harbor-master"))
        self.assertEqual([path.read_bytes() for path in files], before)
        self.assert_release("A", 1, "gpt-6.10-sol")

    def test_the_attachment_is_checked_against_the_package_it_was_read_for(self):
        package = playbook.load(str(self.current))
        self.switch("B")
        attachment = {"path": str(self.current), "model_set": "crew", "lead_role": "harbor-master"}
        self.assertIs(boat_profile.check_attachment("codex-only", attachment, package)[0], package)
        with self.assertRaises(ValueError):
            boat_profile.check_attachment("codex-only", dict(attachment, path=str(self.home / "teams/releases/B")),
                                          package)


if __name__ == "__main__":
    unittest.main()
