"""Generic workers for a new installation, the agent-playbook role workflow when the owner chooses it, and the
role workflow that an installation set up by an earlier release keeps."""
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
from unittest.mock import MagicMock, patch

from profile_fixtures import seeded_roles
import playbook
import playbook_skills

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("codex-only", "codex+claude", "claude-only")
TARGETS = {"codex": (".codex", "AGENTS.md"), "claudeAgent": (".claude", "CLAUDE.md")}
worker = SourceFileLoader("boat_worker_modes", str(ROOT / "bin/boat-worker")).load_module()
cli = SourceFileLoader("t3_boat_modes", str(ROOT / "bin/t3-boat")).load_module()
import role_picker  # noqa: E402  (the CLI module put lib/ on the path)


def lead_only(home, profile):
    """A profile saved by a generic setup: one model, the lead row."""
    rows = {"lead": seeded_roles(home, profile)["lead"]}
    (Path(home) / "profiles" / (profile + ".json")).write_text(json.dumps(rows, indent=2) + "\n")
    return rows


class Home(unittest.TestCase):
    def setUp(self):
        self.reset()

    def reset(self):
        """Start again from an empty home directory."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_CONFIG_NOSYSTEM": "1"}

    def opt(self, answer):
        """The guide answer as an earlier release saved it."""
        path = playbook_skills.state_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(answer + "\n")

    def commit(self, workflow):
        """The workflow as this release's setup commits it."""
        for path, data in playbook_skills.state_files(self.home, workflow).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)

    def prepare(self, profile, check=True):
        result = subprocess.run([sys.executable, str(ROOT / "lib/role_profile.py"), profile], env=self.env,
                                capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def skill(self, provider):
        return (self.home / TARGETS[provider][0] / "skills/t3-boat/SKILL.md").read_text()

    def note(self, provider):
        folder, filename = TARGETS[provider]
        return (self.home / folder / filename).read_text()

    def snapshot(self):
        return {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}

    def assertGeneric(self, skill):
        policy = skill.split("## Models", 1)[0]
        self.assertIn("## Generic workers", policy)
        self.assertIn("No roles, reviews or fixed sequence are required", policy)
        self.assertIn("Start each worker with `boat-worker start`, without a role.", policy)
        for role_step in ("## Role playbook", "reviewer1", "arbiter", "executor"):
            self.assertNotIn(role_step, policy)
        self.assertNotIn("## Model rules", skill)
        self.assertNotIn("## Agent playbook", skill)
        # The generic brief does not ban a worker from splitting its work further.
        self.assertIn("A worker may split its own work further when the brief allows it", policy)
        self.assertIn("| Row | Provider | Model | Option |", skill)
        self.assertTrue(skill.endswith("|\n"), skill[-80:])

    def assertRoles(self, skill, name, guides):
        self.assertIn("## Role playbook", skill)
        self.assertIn("Start reviewer1 and reviewer2 on separate fresh VMs.", skill)
        self.assertIn("Start the arbiter on a fresh VM", skill)
        self.assertIn("`boat-worker start` without a role is refused", skill)
        self.assertNotIn("## Generic workers", skill)
        self.assertIn("## Active roles", skill)
        self.assertIn("## Model rules", skill)
        for line in playbook.rule_lines(name):
            self.assertIn(line, skill)
        self.assertEqual("## Agent playbook" in skill, guides)


class WorkflowStateTests(Home):
    def test_only_a_fresh_installation_starts_with_generic_workers(self):
        self.assertEqual(playbook_skills.workflow(self.home), "generic")
        # An earlier release that never installed the guides still ran the role workflow.
        seeded_roles(self.home, "codex-only")
        self.assertEqual(playbook_skills.workflow(self.home), "roles")
        self.opt("no")
        self.assertEqual(playbook_skills.workflow(self.home), "roles")
        self.opt("yes")
        self.assertEqual(playbook_skills.workflow(self.home), "playbook")
        # Only setup's explicit choice moves an installation to another workflow.
        for workflow in playbook_skills.WORKFLOWS:
            self.commit(workflow)
            self.assertEqual(playbook_skills.workflow(self.home), workflow)
        self.assertEqual(playbook_skills.state_path(self.home).read_text(), "no\n")

    def test_a_guide_answer_without_a_profile_is_an_earlier_installation(self):
        self.opt("no")
        self.assertEqual(playbook_skills.workflow(self.home), "roles")


class InstalledWorkflowTests(Home):
    def test_generic_setup_needs_only_the_lead_row(self):
        for name in PROFILES:
            with self.subTest(profile=name):
                self.commit("generic")
                rows = lead_only(self.home, name)
                self.prepare(name)
                for provider in playbook.providers(name):
                    skill = self.skill(provider)
                    self.assertGeneric(skill)
                    self.assertIn(f"| lead | {rows['lead']['provider']} | {rows['lead']['model']} |", skill)
                    self.assertNotIn("agent-playbook", self.note(provider))

    def test_generic_presets_are_kept_without_the_model_rules(self):
        self.commit("generic")
        rows = seeded_roles(self.home, "codex-only")
        rows["reviewer1"] = dict(rows["executor"])  # Breaks the role workflow's rules.
        path = self.home / "profiles/codex-only.json"
        path.write_text(json.dumps(rows))
        self.prepare("codex-only")
        self.assertIn("| reviewer1 |", self.skill("codex"))
        self.assertEqual(json.loads(path.read_text()), rows)
        self.commit("playbook")
        result = self.prepare("codex-only", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewer1", result.stderr)

    def test_generic_profile_without_a_lead_row_is_refused(self):
        self.commit("generic")
        path = self.home / "profiles/codex-only.json"
        rows = seeded_roles(self.home, "codex-only")
        del rows["lead"]
        path.write_text(json.dumps(rows))
        result = self.prepare("codex-only", check=False)
        self.assertEqual(result.stderr.strip(), f"T3 Boat profile: {path}: expected roles lead; missing lead")

    def test_upgraded_installation_keeps_the_role_workflow_it_had(self):
        for answer in (None, "no"):
            for name in PROFILES:
                with self.subTest(answer=answer, profile=name):
                    self.reset()
                    if answer:
                        self.opt(answer)
                    seeded_roles(self.home, name)
                    self.prepare(name)
                    self.assertFalse(playbook_skills.workflow_path(self.home).exists())  # Nothing is migrated.
                    for provider in playbook.providers(name):
                        self.assertRoles(self.skill(provider), name, guides=False)
                        self.assertNotIn("agent-playbook-lead", self.note(provider))

    def test_upgraded_profile_missing_roles_is_still_refused(self):
        rows = seeded_roles(self.home, "codex-only")
        del rows["arbiter"]
        path = self.home / "profiles/codex-only.json"
        path.write_text(json.dumps(rows))
        result = self.prepare("codex-only", check=False)
        self.assertIn("missing arbiter", result.stderr)

    def test_explicit_playbook_keeps_the_role_workflow_and_model_rules(self):
        for state in ("guide answer", "committed"):
            for name in PROFILES:
                with self.subTest(state=state, profile=name):
                    self.reset()
                    self.opt("yes") if state == "guide answer" else self.commit("playbook")
                    rows = seeded_roles(self.home, name)
                    self.prepare(name)
                    for provider in playbook.providers(name):
                        skill = self.skill(provider)
                        self.assertRoles(skill, name, guides=True)
                        for role, row in rows.items():
                            self.assertIn(f"| {role} | {row['provider']} | {row['model']} |", skill)

    def test_shared_boundary_and_parent_updates_apply_in_every_workflow(self):
        for workflow in playbook_skills.WORKFLOWS:
            with self.subTest(workflow=workflow):
                self.reset()
                self.commit(workflow)
                (lead_only if workflow == "generic" else seeded_roles)(self.home, "codex+claude")
                self.prepare("codex+claude")
                for provider in TARGETS:
                    skill, note = self.skill(provider), self.note(provider)
                    for text in ("## Machine boundary", "Targeted web fact checks are fine here",
                                 "Delegate extended research to a Boat worker",
                                 "all source inspection, edits, project commands, tests, dependency installs, "
                                 "builds and clones", "Nothing from a repository is cloned, fetched, installed or "
                                 "run on this server", "**Control plane, on this T3 server:**",
                                 "## Parent updates", "**Who → whom:**", "**Asked:**", "**Depends on:**",
                                 "**Result:**", "**Decision:**", "State only observed facts.",
                                 "instead of pasting shell output, logs or transcripts",
                                 "It does not stream a child's progress here",
                                 "work a child hands on to other agents is not visible here", "## Cleanup"):
                        self.assertIn(text, skill)
                    self.assertEqual(skill.count("## Parent updates"), 1)
                    self.assertIn("targeted web fact checks are fine here", note)
                    self.assertIn("project source inspection, edits, project commands, tests, installs, clones "
                                  "or extended research", note)
                    self.assertIn("Keep source on Boat. Post compact, factual updates in the parent.", note)

    def test_switching_workflows_rewrites_only_generated_files_and_keeps_projects(self):
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
        for workflow in ("generic", "playbook", "roles", "generic", "playbook", "roles"):
            with self.subTest(workflow=workflow):
                self.commit(workflow)
                self.prepare("codex+claude")
                current = {p: v for p, v in self.snapshot().items() if stub not in p.parents}
                snapshots.setdefault(workflow, current)
                self.assertEqual(snapshots[workflow], current)
                self.assertEqual("## Role playbook" in self.skill("codex"), workflow != "generic")
                self.assertEqual("## Agent playbook" in self.skill("codex"), workflow == "playbook")
                self.assertEqual(profile.read_bytes(), before)
                self.assertEqual({p: p.read_bytes() for p in stub.rglob("*") if p.is_file()}, project)
        self.assertEqual(json.loads(before), rows)


EFFORTS = ["low", "medium", "high", "xhigh"]
MODELS = {"codex": {"gpt-6.2-sol": EFFORTS, "gpt-6.2-astra": EFFORTS, "gpt-6.2-luna": EFFORTS}}


class SetupCommitTests(Home):
    """Setup applies the workflow answer together with the instructions it selects, or not at all."""

    def setup_run(self, *answers, catalog=MODELS):
        replies = iter(answers)

        def answer(prompt):
            print(prompt)
            try:
                reply = next(replies)
            except StopIteration:
                raise EOFError() from None
            if isinstance(reply, BaseException):
                raise reply
            return reply

        stdin = MagicMock()
        stdin.isatty.return_value = True
        # Setup stops at the Boat login, after the workflow and models are applied.
        with patch.dict(os.environ, HOME=str(self.home)), patch("builtins.input", answer), \
                patch.object(cli, "t3_online", return_value=True), patch.object(cli, "logged_in", return_value=True), \
                patch.object(cli, "probe", return_value=None), \
                patch.object(cli, "run", side_effect=cli.SetupError("Boat login is incomplete.")), \
                patch.object(role_picker, "catalog", return_value=catalog), patch.object(cli.sys, "stdin", stdin), \
                redirect_stdout(StringIO()) as out:
            try:
                cli.setup("codex-only", ("codex",))
            except (Exception, KeyboardInterrupt) as error:  # An interrupt is under test.
                return error, out.getvalue()
        return None, out.getvalue()

    def upgraded(self):
        """An installation that an earlier release set up with the role workflow and no guides."""
        seeded_roles(self.home, "codex-only")
        self.prepare("codex-only")
        return self.snapshot()

    def test_interrupted_model_choice_keeps_the_earlier_workflow_and_its_files(self):
        for interrupt in (KeyboardInterrupt(), EOFError()):
            with self.subTest(interrupt=type(interrupt).__name__):
                self.reset()
                before = self.upgraded()
                error, output = self.setup_run("playbook", interrupt)
                self.assertIs(type(error), type(interrupt))
                self.assertIn("It applies once you accept the models below", output)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(playbook_skills.workflow(self.home), "roles")
                self.assertFalse(playbook_skills.skill_path(self.home / ".codex", "lead").exists())

    def test_failed_model_discovery_keeps_the_earlier_workflow_and_its_files(self):
        self.commit("generic")
        lead_only(self.home, "codex-only")
        self.prepare("codex-only")
        before = self.snapshot()
        error, _ = self.setup_run("playbook", catalog={})
        self.assertIsInstance(error, ValueError)
        self.assertIn("No models with supported effort levels", str(error))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(playbook_skills.workflow(self.home), "generic")
        self.assertGeneric(self.skill("codex"))

    def test_fresh_interrupted_setup_saves_no_workflow(self):
        self.prepare("codex-only")
        before = self.snapshot()
        error, _ = self.setup_run("", KeyboardInterrupt())
        self.assertIsInstance(error, KeyboardInterrupt)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(playbook_skills.workflow_path(self.home).exists())

    def test_accepted_models_commit_the_workflow_with_its_instructions(self):
        for upgraded, choice, expected in ((True, "", "roles"), (True, "1", "generic"), (False, "playbook", "playbook"),
                                           (False, "2", "playbook"), (False, "", "generic")):
            with self.subTest(upgraded=upgraded, choice=choice):
                self.reset()
                if upgraded:
                    self.upgraded()
                error, output = self.setup_run(choice, "", "")
                self.assertIsInstance(error, cli.SetupError)
                self.assertEqual(playbook_skills.workflow_path(self.home).read_text(), expected + "\n")
                skill = self.skill("codex")
                rows = json.loads((self.home / "profiles/codex-only.json").read_text())
                if expected == "generic":
                    self.assertGeneric(skill)
                    # A fresh generic setup asks for one model; an upgraded one keeps its rows as presets.
                    self.assertEqual(set(rows), set(playbook.role_keys()) if upgraded else {"lead"})
                    self.assertNotIn("reviewer1 ", output if not upgraded else "")
                else:
                    self.assertRoles(skill, "codex-only", guides=expected == "playbook")
                    self.assertEqual(set(rows), set(playbook.role_keys()))
                self.assertEqual(playbook_skills.skill_path(self.home / ".codex", "lead").exists(),
                                 expected == "playbook")
                # Only an earlier installation is offered the workflow it keeps.
                self.assertEqual("3. roles: keep the role workflow" in output, upgraded)
                self.assertIn(f"Workflow [{'roles' if upgraded else 'generic'}]", output)

    def test_setup_without_a_terminal_keeps_the_workflow(self):
        self.upgraded()
        with patch.dict(os.environ, HOME=str(self.home)), patch.object(cli.sys, "stdin") as stdin, \
                redirect_stdout(StringIO()) as out:
            stdin.isatty.return_value = False
            self.assertEqual(cli.choose_workflow(("codex",)), "roles")
        self.assertIn("asks nothing and keeps it", out.getvalue())


class WorkerTests(Home):
    def stub(self, ssh_status=9):
        project = self.home / "project"
        project.mkdir(exist_ok=True)
        for command in (["git", "init", "-q"], ["git", "config", "remote.origin.url", "https://github.com/sample/repo.git"],
                        ["git", "config", "boat.environment", "team"]):
            subprocess.run(command, cwd=project, env=self.env, check=True)
        boat = self.home / "boat"
        boat.write_text(f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
home = Path(os.environ['HOME'])
with (home/'calls').open('a') as f: f.write(json.dumps(a)+'\\n')
if a[0] == 'new': print(json.dumps({{'event':'ready','id':'bx_1'}}))
elif a[0] == 'ssh': sys.exit({ssh_status})
elif a[0] == 'delete' and '--json' in a:
    print(json.dumps({{'operation':{{'id':'bdop_1','targetId':'bx_1','kind':'sandbox','status':'completed'}}}}))
elif a[0] != 'delete': sys.exit(10)
""")
        boat.chmod(0o755)
        return project

    def run_worker(self, project, profile, *args):
        (self.home / "calls").unlink(missing_ok=True)
        env = self.env | {"PATH": str(self.home) + ":" + self.env["PATH"], "T3_BOAT_PROFILE": profile}
        result = subprocess.run([ROOT / "bin/boat-worker", *args], cwd=project, env=env,
                                text=True, capture_output=True)
        recorded = self.home / "calls"
        calls = [json.loads(line) for line in recorded.read_text().splitlines()] if recorded.exists() else []
        return result, calls

    def start(self, project, profile, *role):
        return self.run_worker(project, profile, "start", *role)

    def tools(self):
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

    def bootstrap(self, role, note, providers):
        env = self.env | {"PATH": f"{self.home / 'tools'}:{self.env['PATH']}"}
        return subprocess.run(["sh", "-c", worker.bootstrap(role, note, providers)], env=env, check=True,
                              capture_output=True, text=True)

    def test_generic_start_needs_one_model_and_enables_every_profile_provider(self):
        project = self.stub()
        self.commit("generic")
        for name in PROFILES:
            with self.subTest(profile=name):
                rows = lead_only(self.home, name)
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
                self.assertIn(json.dumps(rows["lead"]["model"]), script)
                self.assertIn("started by the T3 team lead", script)
                self.assertNotIn("agent-playbook", script)

    def test_generic_start_ignores_presets_that_break_the_role_rules(self):
        project = self.stub()
        self.commit("generic")
        rows = seeded_roles(self.home, "codex-only")
        rows["reviewer1"] = dict(rows["executor"])
        (self.home / "profiles/codex-only.json").write_text(json.dumps(rows))
        for role in ((), ("reviewer1",)):
            with self.subTest(role=role):
                result, calls = self.start(project, "codex-only", *role)
                script = next(cmd for cmd in calls if cmd[0] == "ssh")[2]
                self.assertIn(worker.WORKER_NOTE.splitlines()[0], script)
                self.assertIn(json.dumps(rows[role[0] if role else "lead"]["model"]), script)

    def test_generic_bootstrap_writes_settings_and_worker_note_for_each_provider(self):
        self.tools()
        rows = seeded_roles(self.home, "codex+claude")
        self.bootstrap(rows["lead"], worker.WORKER_NOTE, playbook.providers("codex+claude"))
        settings = json.loads((self.home / ".t3/userdata/settings.json").read_text())
        self.assertEqual(settings["defaultModelSelection"]["instanceId"], rows["lead"]["provider"])
        self.assertEqual(settings["defaultModelSelection"]["model"], rows["lead"]["model"])
        self.assertEqual({p: v["enabled"] for p, v in settings["providerInstances"].items()},
                         {"codex": True, "claudeAgent": True})
        for provider in TARGETS:
            text = self.note(provider)
            self.assertEqual(text.count("<!-- BEGIN t3-boat -->"), 1)
            self.assertIn(worker.WORKER_NOTE, text)

    def test_codex_override_on_the_vm_receives_the_note_after_the_owners_text(self):
        self.tools()
        override = self.home / ".codex/AGENTS.override.md"
        override.parent.mkdir()
        override.write_text("Owner's override instructions\n")
        rows = seeded_roles(self.home, "codex+claude")
        result = self.bootstrap(rows["lead"], worker.WORKER_NOTE, playbook.providers("codex+claude"))
        text = override.read_text()
        self.assertTrue(text.startswith("Owner's override instructions\n\n"))
        self.assertIn(worker.WORKER_NOTE, text)
        self.assertTrue(text.endswith("<!-- END t3-boat -->\n"))
        self.assertFalse((self.home / ".codex/AGENTS.md").exists())
        self.assertIn("AGENTS.override.md", result.stderr)
        # The Claude side of the same worker is unaffected.
        self.assertIn(worker.WORKER_NOTE, self.note("claudeAgent"))

    def test_empty_codex_override_leaves_the_note_in_agents_md(self):
        self.tools()
        override = self.home / ".codex/AGENTS.override.md"
        override.parent.mkdir()
        override.touch()
        rows = seeded_roles(self.home, "codex-only")
        self.bootstrap(rows["lead"], worker.WORKER_NOTE, ("codex",))
        self.assertEqual(override.read_text(), "")
        self.assertIn(worker.WORKER_NOTE, self.note("codex"))

    def test_role_workers_get_the_instructions_of_their_workflow(self):
        project = self.stub()
        for workflow in playbook_skills.WORKFLOWS:
            with self.subTest(workflow=workflow):
                self.commit(workflow)
                seeded_roles(self.home, "codex-only")
                result, calls = self.start(project, "codex-only", "executor")
                script = next(cmd for cmd in calls if cmd[0] == "ssh")[2]
                self.assertIn("for executor", result.stdout)
                self.assertEqual("ROLE: executor" in script or "agent-playbook" in script, workflow == "playbook")
                # The playbook's own executor rules forbid delegation; the generic note must not contradict them,
                # and an installation kept from an earlier release gets no instructions, as it did then.
                self.assertEqual("You may split your work further" in script, workflow == "generic")
                self.assertEqual("BEGIN t3-boat" in script, workflow != "roles")
                self.assertIn('"enabled": true', script)
                self.assertEqual(calls[-1], ["delete", "--yes", "bx_1"])

    def test_roleless_start_is_refused_in_a_role_workflow_before_any_vm(self):
        project = self.stub()
        seeded_roles(self.home, "codex-only")
        for workflow in ("playbook", "roles"):
            with self.subTest(workflow=workflow):
                self.commit(workflow)
                result, calls = self.start(project, "codex-only")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("This machine uses the role workflow, so every worker has a role", result.stderr)
                self.assertIn("executor, reviewer1, reviewer2", result.stderr)
                self.assertEqual(calls, [])

    def test_lead_and_unknown_rows_are_refused_before_any_vm(self):
        project = self.stub()
        for workflow, rows, expected in (("generic", lead_only, "Run boat-worker start for a generic worker"),
                                         ("playbook", seeded_roles, "Select a child role from the active profile")):
            self.commit(workflow)
            rows(self.home, "codex-only")
            for role in ("lead", "nobody"):
                with self.subTest(workflow=workflow, role=role):
                    result, calls = self.start(project, "codex-only", role)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertEqual(calls, [])

    def test_unbound_stub_refuses_a_generic_worker_before_any_vm(self):
        project = self.home / "start-here"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, env=self.env, check=True)
        self.stub()
        self.commit("generic")
        lead_only(self.home, "codex-only")
        result, calls = self.start(project, "codex-only")
        self.assertIn("Run t3-boat setup with a GitHub URL", result.stderr)
        self.assertEqual(calls, [])

    def test_setup_check_uses_the_lead_model_of_a_generic_profile(self):
        project = self.stub(ssh_status=0)
        self.commit("generic")
        rows = lead_only(self.home, "codex+claude")
        result, calls = self.run_worker(project, "codex+claude", "verify", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [cmd[2] for cmd in calls if cmd[0] == "ssh" and "BOAT_OK" in cmd[2]]
        self.assertEqual(len(replies), 2)
        self.assertIn(f"codex subscription reply ({rows['lead']['model']} / {rows['lead']['effort']})", result.stdout)
        self.assertIn("claudeAgent subscription reply (CLI default)", result.stdout)  # No row selects a Claude model.


class StatusTests(Home):
    def test_status_names_the_workflow(self):
        for workflow, expected in (("generic", "Workflow: generic workers"),
                                   ("playbook", "Workflow: agent-playbook roles"),
                                   ("roles", "Workflow: role workflow without agent-playbook guides")):
            with self.subTest(workflow=workflow), patch.dict(os.environ, HOME=str(self.home)), \
                    patch.object(cli, "probe", return_value=None), patch.object(cli, "t3_online", return_value=False), \
                    redirect_stdout(StringIO()) as out:
                self.commit(workflow)
                self.assertEqual(cli.status("codex-only", ("codex",)), 1)
                self.assertIn(expected, out.getvalue())

    def test_status_accepts_a_generic_single_model_profile(self):
        self.commit("generic")
        rows = lead_only(self.home, "codex-only")
        with patch.dict(os.environ, HOME=str(self.home)):
            self.assertEqual(cli.parent_role("codex-only"), rows["lead"])
            self.commit("playbook")
            with self.assertRaises(cli.ProfileError):
                cli.parent_role("codex-only")


if __name__ == "__main__":
    unittest.main()
