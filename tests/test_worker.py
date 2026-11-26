import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import playbook
from packages import CUSTOM_ROLES, write_package
from test_profile import LEADS, save

worker = SourceFileLoader("boat_worker_tests", str(ROOT / "bin/boat-worker")).load_module()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.package = write_package(self.home / "team")
        self.attachment = {"path": str(self.package), "model_set": None, "lead_role": "harbor-master"}

    def test_a_worker_without_a_role_gets_the_note_the_lead_model_and_every_provider(self):
        for profile in LEADS:
            with self.subTest(profile=profile), patch.dict(os.environ, T3_BOAT_PROFILE=profile):
                save(self.home, profile, self.attachment if profile == "codex+claude" else None)
                lead, providers, label, note = worker.worker_plan()
                self.assertEqual(lead, LEADS[profile])
                self.assertEqual(providers, worker.PROVIDERS[profile])
                self.assertIs(note, worker.WORKER_NOTE)

    def test_any_role_of_the_attached_package_gets_its_own_guide(self):
        save(self.home, "codex+claude", self.attachment)
        package = playbook.load(str(self.package))
        with patch.dict(os.environ, T3_BOAT_PROFILE="codex+claude"):
            for role in CUSTOM_ROLES:
                with self.subTest(role=role):
                    lead, providers, label, note = worker.worker_plan(role)
                    self.assertEqual(lead, LEADS["codex+claude"])
                    self.assertEqual(providers, ("codex", "claudeAgent"))
                    self.assertTrue(note.endswith(package.bundle(role)))
                    self.assertIn(f"`{role}` role of the playbook harbor-crew (version 7.1.0)", note)
                    self.assertNotIn(str(self.package), note)  # A server path means nothing on the VM.
            with self.assertRaisesRegex(ValueError, "Its roles: harbor-master, rigger, cartographer, qa_bot"):
                worker.worker_plan("executor")

    def test_a_role_without_an_attached_package_is_refused(self):
        save(self.home, "codex-only")
        with patch.dict(os.environ, T3_BOAT_PROFILE="codex-only"), \
                self.assertRaisesRegex(ValueError, "No playbook is attached"):
            worker.worker_plan("rigger")

    def test_the_profile_is_read_from_the_active_file_when_the_variable_is_missing(self):
        save(self.home, "claude-only")
        (self.home / "profiles/active").write_text("claude-only\n")
        self.assertEqual(worker.worker_plan()[0], LEADS["claude-only"])

    def test_bootstrap_opens_with_the_lead_model_and_enables_every_listed_provider(self):
        script = worker.bootstrap(LEADS["codex+claude"], "GUIDE", ("codex", "claudeAgent"))
        settings = json.loads(shlex.split(script.split("printf '%s\\n' ", 1)[1].split("\n", 1)[0])[0])
        self.assertEqual(settings["defaultModelSelection"]["model"], "gpt-6.2-astra")
        self.assertTrue(all(row["enabled"] for row in settings["providerInstances"].values()))
        self.assertIn("codex login status", script)
        self.assertIn("claude auth status", script)
        self.assertEqual(script.count("GUIDE"), 2)

    def test_instructions_reach_agents_md_or_a_nonempty_codex_override_after_its_text(self):
        for override, target in (("", ".codex/AGENTS.md"), ("Owner text\n", ".codex/AGENTS.override.md")):
            with self.subTest(override=bool(override)), tempfile.TemporaryDirectory() as vm:
                path = Path(vm, ".codex/AGENTS.override.md")
                path.parent.mkdir()
                path.write_text(override)
                Path(vm, ".codex/AGENTS.md").write_text("Existing\n")
                for provider in ("codex", "claudeAgent"):
                    subprocess.run(["sh", "-ec", worker.worker_instructions(provider, "THE GUIDE")],
                                   env={"HOME": vm, "PATH": os.environ["PATH"]}, check=True, capture_output=True)
                text = Path(vm, target).read_text()
                self.assertTrue(text.startswith(override or "Existing\n"))
                self.assertTrue(text.endswith("<!-- BEGIN t3-boat -->\nTHE GUIDE\n<!-- END t3-boat -->\n"))
                self.assertIn("THE GUIDE", Path(vm, ".claude/CLAUDE.md").read_text())

    def test_verify_probes_the_lead_model_and_the_cli_default_of_other_providers(self):
        rows = worker.probe_rows(LEADS["codex+claude"], "codex+claude")
        self.assertEqual(rows, {"codex": LEADS["codex+claude"], "claudeAgent": None})
        self.assertEqual(worker.probe_rows(LEADS["claude-only"], "claude-only"), {"claudeAgent": LEADS["claude-only"]})

    def test_start_uses_the_bound_environment_writes_the_guide_and_deletes_on_failure(self):
        project = self.home / "project"
        project.mkdir()
        for command in (["git", "init", "-q"], ["git", "config", "remote.origin.url", "https://github.com/sample/repo.git"],
                        ["git", "config", "boat.environment", "team"]):
            subprocess.run(command, cwd=project, env=self.env, check=True)
        boat = self.home / "bin/boat"
        boat.parent.mkdir()
        boat.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
home = Path(os.environ['HOME'])
with (home/'calls').open('a') as f: f.write(json.dumps(a)+'\\n')
if a[0] == 'new':
    (home/'name').write_text(a[a.index('--name')+1])
    if os.environ['T3_BOAT_PROFILE'] != 'claude-only': print(json.dumps({'event':'ready','id':'bx_1'}))
elif a[0] == 'list': print(json.dumps({'sandboxes':[{'id':'bx_1','name':(home/'name').read_text()}]}))
elif a[0] == 'ssh': sys.exit(9)
elif a[0] != 'delete': sys.exit(10)
""")
        boat.chmod(0o755)
        for profile, role, expected in [
            ("codex-only", None, ["codex login status"]),
            ("claude-only", "cartographer", ["claude auth status", "STEPS-cartographer"]),
            ("codex+claude", "qa_bot", ["codex login status", "claude auth status", "ROLE-RULES-qa_bot"]),
        ]:
            with self.subTest(profile=profile):
                save(self.home, profile, None if role is None else self.attachment)
                calls = self.home / "calls"
                calls.unlink(missing_ok=True)
                env = self.env | {"PATH": str(boat.parent) + ":" + self.env["PATH"], "T3_BOAT_PROFILE": profile}
                result = subprocess.run([ROOT / "bin/boat-worker", "start", *([role] if role else [])], cwd=project,
                                        env=env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"for {'a worker' if role is None else 'the harbor-crew ' + role + ' role'}", result.stdout)
                invoked = [json.loads(line) for line in calls.read_text().splitlines()]
                self.assertIn("--no-snapshots", invoked[0])
                self.assertEqual(invoked[0][-2:], ["--environment", "team"])
                self.assertEqual(invoked[-1], ["delete", "--yes", "bx_1"])
                script = next(cmd for cmd in invoked if cmd[0] == "ssh")[2]
                for text in expected:
                    self.assertIn(text, script)
                self.assertEqual("You are a worker on a disposable Boat VM, started by the T3 team lead." in script,
                                 role is None)
                self.assertNotIn("git clone", script)

    def test_an_unbound_stub_refuses_before_any_vm(self):
        save(self.home, "codex-only")
        stub = self.home / "start-here"
        stub.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=stub, env=self.env, check=True)
        result = subprocess.run([ROOT / "bin/boat-worker", "start"], cwd=stub, text=True, capture_output=True,
                                env=self.env | {"T3_BOAT_PROFILE": "codex-only", "PATH": "/nonexistent:" + self.env["PATH"]})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Run t3-boat setup with a GitHub URL", result.stderr)


if __name__ == "__main__":
    unittest.main()
