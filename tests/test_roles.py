import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import boat_profile
import playbook
import playbook_skills
from packages import write_package
from test_model_picker import MODELS
from test_profile import save

cli = SourceFileLoader("t3_boat_role_tests", str(ROOT / "bin/t3-boat")).load_module()
worker = SourceFileLoader("boat_worker_role_tests", str(ROOT / "bin/boat-worker")).load_module()


class RoleModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
        patcher = patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.package_path = write_package(
            self.home / "teams/roles",
            roles={"lead": 1, "builder": 1, "reviewer": 1},
            sets={"codex-team": {
                "lead": {"provider": "codex", "family": "astra", "effort": "high"},
                "builder": {"provider": "codex", "family": "sol", "effort": "medium"},
                "reviewer": {"provider": "codex", "family": "astra", "effort": "high"},
            }},
            rules=[{"roles": ["builder"], "must_differ_from": "reviewer"}],
        )
        self.attachment = {"path": str(self.package_path), "model_set": "codex-team", "lead_role": "lead"}
        save(self.home, "codex-only", self.attachment)
        boat_profile.select_profile("codex-only")
        boat_profile.prepare("codex-only")
        self.available = {"codex": MODELS["codex"]}

    def state(self):
        return json.loads((self.home / "profiles/codex-only.json").read_text())

    def applied(self):
        return json.loads((self.home / "profiles/codex-only.applied.json").read_text())

    def test_show_reports_effective_source_and_cli_defaults_to_show(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["t3-boat", "roles"]), redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        report = output.getvalue()
        self.assertIn("lead\t-\tcodex\tmodel:gpt-6.2-astra\thigh\tprofile lead model", report)
        self.assertIn("builder\t1\tcodex\tfamily:sol\tmedium\tmodel set codex-team", report)

    def test_live_catalog_can_be_filtered(self):
        output = io.StringIO()
        with patch.object(cli, "catalog", return_value=self.available), redirect_stdout(output):
            self.assertEqual(cli.show_models("codex-only", "codex"), 0)
        self.assertIn("gpt-6.2-sol: medium, high", output.getvalue())

    def test_family_override_is_applied_immediately_to_future_workers(self):
        output = io.StringIO()
        with patch.object(cli, "catalog", return_value=self.available), redirect_stdout(output):
            self.assertEqual(cli.set_role("codex-only", "builder", "codex", "luna", None, "low"), 0)
        override = {"provider": "codex", "family": "luna", "effort": "low"}
        self.assertEqual(self.state()["playbook"]["role_overrides"], {"builder": override})
        assignment = self.applied()["playbook"]["assignments"]["builder"]
        self.assertEqual(assignment, {"entries": [override], "source": "owner override"})
        skill = (self.home / ".codex/skills/t3-boat/SKILL.md").read_text()
        self.assertIn("| builder | 1 | codex | luna | low | owner override |", skill)
        self.assertIn("owner override: codex family:luna low", worker.worker_plan("builder")[2])
        self.assertIn("Currently resolves to codex / model:gpt-6-luna / low", output.getvalue())
        self.assertIn("no image rebuild or container restart was needed", output.getvalue())

    def test_exact_model_override_and_reset(self):
        with patch.object(cli, "catalog", return_value=self.available):
            cli.set_role("codex-only", "builder", "codex", None, "gpt-6-luna", "low")
        self.assertEqual(self.state()["playbook"]["role_overrides"]["builder"]["model"], "gpt-6-luna")
        cli.reset_roles("codex-only", "builder", False)
        self.assertNotIn("role_overrides", self.state()["playbook"])
        self.assertEqual(self.applied()["playbook"]["assignments"]["builder"]["source"], "model set codex-team")

    def test_lead_role_cannot_be_reset_through_worker_overrides(self):
        with self.assertRaisesRegex(cli.RoleError, "team lead"):
            cli.reset_roles("codex-only", "lead", False)

    def test_reset_all_removes_every_override(self):
        state = self.state()
        state["playbook"]["role_overrides"] = {
            "builder": {"provider": "codex", "family": "luna", "effort": "low"},
            "reviewer": {"provider": "codex", "family": "sol", "effort": "high"},
        }
        boat_profile.prepare("codex-only", state)
        cli.reset_roles("codex-only", None, True)
        self.assertNotIn("role_overrides", self.state()["playbook"])

    def test_invalid_or_unsafe_overrides_are_rejected_without_changing_state(self):
        before = self.state()
        cases = [
            (("lead", "codex", "luna", None, "low"), "team lead"),
            (("builder", "claude", "opus", None, "high"), "provider must be one of codex"),
            (("builder", "codex", "missing", None, "high"), "does not resolve"),
            (("builder", "codex", None, "gpt-6.2-astra", "high"), "must differ from reviewer"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments), patch.object(cli, "catalog", return_value=self.available), \
                    self.assertRaisesRegex(cli.RoleError, message):
                cli.set_role("codex-only", *arguments)
            self.assertEqual(self.state(), before)

    def test_legacy_attachment_without_overrides_remains_valid(self):
        package, model_set, lead_role = boat_profile.check_attachment("codex-only", self.attachment)
        self.assertEqual((package.name, model_set, lead_role), ("harbor-crew", "codex-team", "lead"))
        self.assertNotIn("role_overrides", self.state()["playbook"])

    def test_generated_skill_requires_owner_approval_for_persistent_changes(self):
        skill = (self.home / ".codex/skills/t3-boat/SKILL.md").read_text()
        self.assertIn("another listed model for one delegation without saving it", skill)
        self.assertIn("only on explicit owner approval run `t3-boat roles set ROLE ...`", skill)
        self.assertIn("Never persist a model change on your own initiative", skill)


if __name__ == "__main__":
    unittest.main()
