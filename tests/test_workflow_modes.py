"""Generic workers without the agent-playbook, and the playbook's role workflow when the owner opts in."""
import json
import os
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from profile_fixtures import seeded_roles
import playbook
import playbook_skills

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("codex-only", "codex+claude", "claude-only")
TARGETS = {"codex": (".codex", "AGENTS.md"), "claudeAgent": (".claude", "CLAUDE.md")}
worker = SourceFileLoader("boat_worker_modes", str(ROOT / "bin/boat-worker")).load_module()
cli = SourceFileLoader("t3_boat_modes", str(ROOT / "bin/t3-boat")).load_module()


class Home(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_CONFIG_NOSYSTEM": "1"}

    def opt(self, answer):
        path = playbook_skills.state_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(answer + "\n")

    def prepare(self, profile):
        result = subprocess.run([sys.executable, str(ROOT / "lib/role_profile.py"), profile], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def skill(self, provider):
        return (self.home / TARGETS[provider][0] / "skills/t3-boat/SKILL.md").read_text()

    def note(self, provider):
        folder, filename = TARGETS[provider]
        return (self.home / folder / filename).read_text()


class InstalledWorkflowTests(Home):
    def test_fresh_no_playbook_install_delegates_to_generic_workers(self):
        for answer in (None, "no"):
            for name in PROFILES:
                with self.subTest(answer=answer, profile=name):
                    if answer:
                        self.opt(answer)
                    seeded_roles(self.home, name)
                    self.prepare(name)
                    for provider in playbook.providers(name):
                        skill = self.skill(provider)
                        policy = skill.split("## Model presets", 1)[0]
                        self.assertIn("## Generic workers", policy)
                        self.assertIn("No roles, reviews or fixed sequence are required", policy)
                        self.assertIn("boat-worker start` (a generic worker)", policy)
                        for role_step in ("## Role playbook", "reviewer1", "arbiter", "executor"):
                            self.assertNotIn(role_step, policy)
                        self.assertNotIn("## Model rules", skill)
                        # The generic brief does not ban a worker from splitting its work further.
                        self.assertIn("A worker may split its own work further when the brief allows it", policy)
                        self.assertIn("| Preset | Provider | Model | Option |", skill)
                        self.assertTrue(skill.endswith("|\n"), skill[-80:])

    def test_explicit_playbook_keeps_the_role_workflow_and_model_rules(self):
        self.opt("yes")
        for name in PROFILES:
            with self.subTest(profile=name):
                rows = seeded_roles(self.home, name)
                self.prepare(name)
                for provider in playbook.providers(name):
                    skill = self.skill(provider)
                    self.assertIn("## Role playbook", skill)
                    self.assertIn("Start reviewer1 and reviewer2 on separate fresh VMs.", skill)
                    self.assertIn("Start the arbiter on a fresh VM", skill)
                    self.assertNotIn("## Generic workers", skill)
                    self.assertIn("## Active roles", skill)
                    self.assertIn("## Model rules", skill)
                    for line in playbook.rule_lines(name):
                        self.assertIn(line, skill)
                    self.assertIn("## Agent playbook", skill)
                    for role, row in rows.items():
                        self.assertIn(f"| {role} | {row['provider']} | {row['model']} |", skill)

    def test_shared_boundary_and_parent_updates_apply_in_both_modes(self):
        for answer in ("no", "yes"):
            with self.subTest(answer=answer):
                self.opt(answer)
                seeded_roles(self.home, "codex+claude")
                self.prepare("codex+claude")
                for provider in TARGETS:
                    skill, note = self.skill(provider), self.note(provider)
                    for text in ("## Machine boundary", "Targeted web fact checks are fine here",
                                 "Delegate extended research to a Boat worker",
                                 "all source inspection, edits, project commands, tests, dependency installs, "
                                 "builds and clones", "**Control plane, on this T3 server:**",
                                 "## Parent updates", "**Who → whom:**", "**Depends on:**", "**Decision:**",
                                 "State only observed facts.", "instead of pasting shell output, logs or transcripts",
                                 "It does not stream a child's progress here", "## Cleanup"):
                        self.assertIn(text, skill)
                    self.assertIn("targeted web fact checks are fine here", note)
                    self.assertIn("project source inspection, edits, project commands, tests, installs, clones "
                                  "or extended research", note)
                    self.assertIn("Post compact, factual updates in the parent.", note)

    def test_switching_modes_rewrites_only_generated_files_and_keeps_projects(self):
        rows = seeded_roles(self.home, "codex+claude")
        profile = self.home / "profiles/codex+claude.json"
        stub = self.home / "projects/owner/example"
        stub.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(stub)], env=self.env, check=True)
        for key, value in (("remote.origin.url", "https://github.com/owner/example.git"), ("boat.environment", "base")):
            subprocess.run(["git", "-C", str(stub), "config", "--local", key, value], env=self.env, check=True)
        project = {p: p.read_bytes() for p in stub.rglob("*") if p.is_file()}
        before = profile.read_bytes()
        snapshots = {}
        for answer in ("no", "yes", "no", "yes"):
            with self.subTest(answer=answer):
                self.opt(answer)
                self.prepare("codex+claude")
                current = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and stub not in p.parents}
                snapshots.setdefault(answer, current)
                self.assertEqual(snapshots[answer], current)
                self.assertEqual(("## Role playbook" in self.skill("codex")), answer == "yes")
                self.assertEqual(profile.read_bytes(), before)
                self.assertEqual({p: p.read_bytes() for p in stub.rglob("*") if p.is_file()}, project)
        self.assertEqual(json.loads(before), rows)


class WorkerTests(Home):
    def stub(self):
        project = self.home / "project"
        project.mkdir()
        for command in (["git", "init", "-q"], ["git", "config", "remote.origin.url", "https://github.com/sample/repo.git"],
                        ["git", "config", "boat.environment", "team"]):
            subprocess.run(command, cwd=project, env=self.env, check=True)
        boat = self.home / "boat"
        boat.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
home = Path(os.environ['HOME'])
with (home/'calls').open('a') as f: f.write(json.dumps(a)+'\\n')
if a[0] == 'new': print(json.dumps({'event':'ready','id':'bx_1'}))
elif a[0] == 'ssh': sys.exit(9)
elif a[0] != 'delete': sys.exit(10)
""")
        boat.chmod(0o755)
        return project

    def start(self, project, profile, *role):
        (self.home / "calls").unlink(missing_ok=True)
        env = self.env | {"PATH": str(self.home) + ":" + self.env["PATH"], "T3_BOAT_PROFILE": profile}
        result = subprocess.run([ROOT / "bin/boat-worker", "start", *role], cwd=project, env=env,
                                text=True, capture_output=True)
        recorded = self.home / "calls"
        calls = [json.loads(line) for line in recorded.read_text().splitlines()] if recorded.exists() else []
        return result, calls

    def test_generic_start_enables_every_profile_provider_and_opens_with_the_lead_model(self):
        project = self.stub()
        for name in PROFILES:
            with self.subTest(profile=name):
                rows = seeded_roles(self.home, name)
                result, calls = self.start(project, name)
                self.assertNotEqual(result.returncode, 0)  # The fake VM refuses SSH; the worker is deleted.
                self.assertIn("for a generic worker", result.stdout)
                self.assertEqual(calls[0][-2:], ["--environment", "team"])
                self.assertIn("--no-snapshots", calls[0])
                self.assertEqual(calls[-1], ["delete", "--yes", "bx_1"])
                script = next(cmd for cmd in calls if cmd[0] == "ssh")[2]
                enabled = playbook.providers(name)
                self.assertEqual("codex login status" in script, "codex" in enabled)
                self.assertEqual("claude auth status" in script, "claudeAgent" in enabled)
                lead = rows["lead"]
                self.assertIn(json.dumps(lead["model"]), script)
                self.assertIn(f"Started by the T3 team lead".lower(), script.lower())
                self.assertNotIn("agent-playbook", script)

    def test_generic_bootstrap_writes_settings_and_worker_note_for_each_provider(self):
        tools = self.home / "tools"
        tools.mkdir()
        for name, body in {
            "codex": "case \"$1\" in login) echo 'Logged in using ChatGPT' ;; *) exit 1 ;; esac",
            "claude": "case \"$1\" in --version) echo 2.1.330 ;; auth) echo '{\"loggedIn\":true,"
                      "\"apiProvider\":\"firstParty\",\"authMethod\":\"oauth_token\"}' ;; *) exit 1 ;; esac",
            "npm": "mkdir -p \"$3/node_modules/.bin\"; printf '#!/bin/sh\\nexit 0\\n' > \"$3/node_modules/.bin/t3\"; "
                   "chmod +x \"$3/node_modules/.bin/t3\"",
        }.items():
            (tools / name).write_text("#!/bin/sh\n" + body + "\n")
            (tools / name).chmod(0o755)
        rows = seeded_roles(self.home, "codex+claude")
        script = worker.bootstrap(rows["lead"], worker.WORKER_NOTE, playbook.providers("codex+claude"))
        env = self.env | {"PATH": f"{tools}:{self.env['PATH']}"}
        subprocess.run(["sh", "-c", script], env=env, check=True, capture_output=True, text=True)
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertEqual(settings["defaultModelSelection"]["instanceId"], rows["lead"]["provider"])
        self.assertEqual(settings["defaultModelSelection"]["model"], rows["lead"]["model"])
        self.assertEqual({p: v["enabled"] for p, v in settings["providerInstances"].items()},
                         {"codex": True, "claudeAgent": True})
        for provider in TARGETS:
            text = self.note(provider)
            self.assertEqual(text.count("<!-- BEGIN t3-boat -->"), 1)
            self.assertIn(worker.WORKER_NOTE, text)

    def test_role_worker_gets_its_guide_with_the_playbook_and_the_worker_note_without(self):
        project = self.stub()
        for answer in ("no", "yes"):
            with self.subTest(answer=answer):
                self.opt(answer)
                seeded_roles(self.home, "codex-only")
                result, calls = self.start(project, "codex-only", "executor")
                script = next(cmd for cmd in calls if cmd[0] == "ssh")[2]
                self.assertIn("for executor", result.stdout)
                self.assertEqual("ROLE: executor" in script or "agent-playbook" in script, answer == "yes")
                # The playbook's own executor rules forbid delegation; the generic note must not contradict them.
                self.assertEqual("You may split your work further" in script, answer == "no")
                self.assertIn('"enabled": true', script)
                self.assertEqual(calls[-1], ["delete", "--yes", "bx_1"])

    def test_lead_and_unknown_rows_are_refused_before_any_vm(self):
        project = self.stub()
        seeded_roles(self.home, "codex-only")
        for role in ("lead", "nobody"):
            with self.subTest(role=role):
                result, calls = self.start(project, "codex-only", role)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("boat-worker start for a generic worker", result.stderr)
                self.assertEqual(calls, [])

    def test_unbound_stub_refuses_a_generic_worker_before_any_vm(self):
        project = self.home / "start-here"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, env=self.env, check=True)
        self.stub()
        seeded_roles(self.home, "codex-only")
        result, calls = self.start(project, "codex-only")
        self.assertIn("Run t3-boat setup with a GitHub URL", result.stderr)
        self.assertEqual(calls, [])


class StatusTests(Home):
    def test_status_names_the_workflow(self):
        for answer, expected in (("no", "Workflow: generic workers"), ("yes", "Workflow: agent-playbook roles")):
            with self.subTest(answer=answer), patch.dict(os.environ, HOME=str(self.home)), \
                    patch.object(cli, "probe", return_value=None), patch.object(cli, "t3_online", return_value=False), \
                    redirect_stdout(StringIO()) as out:
                self.opt(answer)
                self.assertEqual(cli.status("codex-only", ("codex",)), 1)
                self.assertIn(expected, out.getvalue())


if __name__ == "__main__":
    unittest.main()
