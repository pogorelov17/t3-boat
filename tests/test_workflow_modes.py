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
EFFORTS = ["low", "medium", "high", "xhigh"]
# The models seeded_roles selects, as the provider catalog would list them.
MODELS = {"codex": {"gpt-6.2-sol": EFFORTS, "gpt-6.2-astra": EFFORTS, "gpt-6.2-luna": EFFORTS},
          "claudeAgent": {m: EFFORTS for m in ("claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5",
                                               "claude-haiku-5-5")}}
TARGETS = {"codex": (".codex", "AGENTS.md"), "claudeAgent": (".claude", "CLAUDE.md")}
worker = SourceFileLoader("boat_worker_modes", str(ROOT / "bin/boat-worker")).load_module()
cli = SourceFileLoader("t3_boat_modes", str(ROOT / "bin/t3-boat")).load_module()
import role_picker  # noqa: E402  (the CLI module put lib/ on the path)
import role_profile  # noqa: E402


def lead_only(home, profile):
    """A profile saved by a generic setup: one model, the lead row."""
    rows = {"lead": seeded_roles(home, profile)["lead"]}
    (Path(home) / "profiles" / (profile + ".json")).write_text(json.dumps(rows, indent=2) + "\n")
    return rows


def rows_for(profile, only_lead=False):
    """The rows seeded_roles saves, without saving them."""
    with tempfile.TemporaryDirectory() as scratch:
        rows = seeded_roles(scratch, profile)
    return {"lead": rows["lead"]} if only_lead else rows


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
        # Generated text names the home directory; compare installations made in different ones.
        return {p.relative_to(self.home): p.read_bytes().replace(bytes(self.home), b"~")
                for p in self.home.rglob("*") if p.is_file()}

    def setup_run(self, *answers, catalog=None, profile="codex-only"):
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
                patch.object(cli, "run", side_effect=self.stop_at_boat), \
                patch.object(role_picker, "catalog", return_value=MODELS if catalog is None else catalog), patch.object(cli.sys, "stdin", stdin), \
                redirect_stdout(StringIO()) as out:
            try:
                cli.setup(profile, cli.PROFILES[profile])
            except (Exception, KeyboardInterrupt) as error:  # An interrupt is under test.
                return error, out.getvalue()
        return None, out.getvalue()

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

    @staticmethod
    def stop_at_boat(*argv):
        """The installer and logins succeed; the Boat login, after the models are applied, does not."""
        if argv[0] == "boat":
            raise cli.SetupError("Boat login is incomplete.")

    def startup_settings(self):
        return json.loads((self.home / ".t3/userdata/settings.json").read_text())

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
        # Without the guides (a role workflow kept from an earlier release) the opening does not claim them.
        opening, other = ((role_profile.PLAYBOOK_OPENING, role_profile.ROLES_OPENING) if guides
                          else (role_profile.ROLES_OPENING, role_profile.PLAYBOOK_OPENING))
        self.assertIn(opening, skill)
        self.assertNotIn(other, skill)
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
        result = self.prepare("codex-only")
        self.assertIn(f"T3 Boat: {path}: select a different model for reviewer1", result.stderr)
        self.assertIn("run t3-boat setup with T3_BOAT_PROFILE=codex-only to complete it", result.stderr)
        self.assertNotIn("defaultModelSelection", self.startup_settings())
        self.assertNotIn("BEGIN t3-boat", self.note("codex"))
        self.assertEqual(json.loads(path.read_text()), rows)
        # An installation kept from an earlier release chose nothing, so it is still refused there.
        playbook_skills.workflow_path(self.home).unlink()
        result = self.prepare("codex-only", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(),
                         f"T3 Boat profile: {path}: select a different model for reviewer1; executor uses codex sol")

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




class SetupCommitTests(Home):
    """Setup applies the workflow answer together with the instructions it selects, or not at all."""

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


class Interrupted(KeyboardInterrupt):
    """The process stops at an injected point, as Ctrl-C or a killed container would stop it."""


class CommitRecoveryTests(Home):
    """An accepted setup writes several files. Stopping it at any of them must never leave a state that the next
    container start, setup or worker command rejects: the commit is finished, or nothing changed."""

    def commit_rows(self, name, rows, workflow, stop_at=None):
        """Apply an accepted setup in this process, stopping before write number `stop_at`; return the writes."""
        real, calls = role_profile.write, []

        def write(path, data):
            if len(calls) == stop_at:
                raise Interrupted()
            calls.append(path)
            real(path, data)

        with patch.dict(os.environ, HOME=str(self.home)), patch.object(role_profile, "write", write), \
                redirect_stdout(StringIO()):
            try:
                role_profile.prepare(name, rows, workflow)
            except Interrupted:
                self.assertIsNotNone(stop_at)
        return calls

    def scenario(self, before, name, after, workflow):
        """Snapshots after a restart: without the commit, and with it applied in full."""
        self.reset()
        before(self)
        self.prepare(name)
        old = self.snapshot()
        writes = self.commit_rows(name, after(self, name), workflow)
        self.prepare(name)
        new = self.snapshot()
        self.assertNotEqual(old, new)
        self.assertFalse(role_profile.journal_path(self.home).exists())
        self.assertEqual(playbook_skills.workflow(self.home), workflow)
        return old, new, len(writes)

    SCENARIOS = (
        # A fresh volume choosing generic workers: the lead-only profile and the guide answer come before the
        # workflow, so stopping between them used to leave a lead-only profile in the role workflow.
        ("fresh to generic", lambda self: None, lambda self, n: rows_for(n, only_lead=True), "generic"),
        ("generic to playbook", lambda self: (self.commit("generic"), lead_only(self.home, "codex+claude")),
         lambda self, n: rows_for(n), "playbook"),
        ("playbook to generic", lambda self: (self.commit("playbook"), seeded_roles(self.home, "codex+claude")),
         lambda self, n: {**rows_for(n), "lead": dict(rows_for(n)["scout"])}, "generic"),
        ("earlier release to playbook", lambda self: seeded_roles(self.home, "codex+claude"),
         lambda self, n: rows_for(n), "playbook"),
    )

    def run_scenario(self, label, before, after, workflow, name):
        old, new, total = self.scenario(before, name, after, workflow)
        self.assertGreater(total, 4)
        for stop_at in range(total):
            with self.subTest(scenario=label, profile=name, stop_at=stop_at):
                self.reset()
                before(self)
                self.prepare(name)
                rows = after(self, name)
                self.commit_rows(name, rows, workflow, stop_at)
                # Nothing is written before the record of the accepted choice; after it, a restart finishes it.
                self.assertEqual(role_profile.journal_path(self.home).exists(), stop_at > 0)
                self.prepare(name)
                self.assertEqual(self.snapshot(), new if stop_at else old)

    def test_every_interruption_of_a_commit_is_finished_or_undone_by_a_restart(self):
        for label, before, after, workflow in self.SCENARIOS:
            for name in (PROFILES if label == "fresh to generic" else ("codex+claude",)):
                self.run_scenario(label, before, after, workflow, name)

    def test_staged_switches_each_interrupted_end_in_the_last_choice(self):
        name = "codex+claude"
        stages = (("generic", lambda: rows_for(name, only_lead=True)), ("playbook", lambda: rows_for(name)),
                  ("generic", lambda: {"lead": dict(rows_for(name)["scout"])}))
        # The clean result of the same three choices, for comparison.
        self.prepare(name)
        for workflow, rows in stages:
            self.commit_rows(name, rows(), workflow)
        self.prepare(name)
        expected = self.snapshot()
        for stop_at in range(1, 12):
            with self.subTest(stop_at=stop_at):
                self.reset()
                self.prepare(name)
                # Every choice but the last stops part way; the next one first finishes the one before it.
                for workflow, rows in stages[:-1]:
                    self.commit_rows(name, rows(), workflow, stop_at)
                self.commit_rows(name, stages[-1][1](), stages[-1][0])
                self.prepare(name)
                self.assertEqual(self.snapshot(), expected)

    def test_setup_and_workers_finish_an_interrupted_commit_without_a_restart(self):
        self.prepare("codex-only")
        rows = rows_for("codex-only", only_lead=True)
        writes = [path.relative_to(self.home) for path in self.commit_rows("codex-only", rows, "generic")]
        self.reset()
        project = self.stub()
        self.prepare("codex-only")
        # Stop just before the workflow file, after the lead-only profile and the guide answer are written.
        self.commit_rows("codex-only", rows, "generic", writes.index(playbook_skills.WORKFLOW))
        self.assertEqual(playbook_skills.state_path(self.home).read_text(), "no\n")
        self.assertEqual(json.loads((self.home / "profiles/codex-only.json").read_text()), rows)
        result, calls = self.start(project, "codex-only")
        self.assertIn("for a generic worker", result.stdout)  # Not refused as an incomplete role profile.
        self.assertEqual(calls[-1], ["delete", "--yes", "bx_1"])
        self.assertEqual(playbook_skills.workflow_path(self.home).read_text(), "generic\n")
        self.assertFalse(role_profile.journal_path(self.home).exists())
        self.assertGeneric(self.skill("codex"))

        # The same stop during setup itself: the next setup finishes it and offers it as the current workflow.
        self.reset()
        self.prepare("codex-only")
        real = role_profile.write

        def write(path, data):
            if path == playbook_skills.workflow_path(self.home):
                raise Interrupted()
            real(path, data)

        with patch.object(role_profile, "write", write):
            error, _ = self.setup_run("", "")
        self.assertIsInstance(error, Interrupted)
        self.assertTrue(role_profile.journal_path(self.home).exists())
        error, output = self.setup_run("", "", "")
        self.assertIsInstance(error, cli.SetupError)
        self.assertIn("Finished applying the models and workflow accepted in an interrupted setup.", output)
        self.assertIn("Workflow [generic]", output)
        self.assertIn("your saved workflow", output)
        self.assertEqual(playbook_skills.workflow(self.home), "generic")

    def recovery(self, entry, name, stop_at=None):
        """Finish an interrupted commit in this process through `entry`, as `name`, stopping before write `stop_at`."""
        real, calls = role_profile.write, []

        def write(path, data):
            if len(calls) == stop_at:
                raise Interrupted()
            calls.append(path)
            real(path, data)

        with patch.dict(os.environ, HOME=str(self.home)), patch.object(role_profile, "write", write), \
                redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
            try:
                {"start": role_profile.prepare, "recover": role_profile.recover}[entry](name)
            except Interrupted:
                self.assertIsNotNone(stop_at)
        return calls

    def applied(self, profile):
        """The saved state and the files a start of `profile` generates for its own providers.

        A start of one profile leaves another provider's files as they were, so after a switch of profiles the
        rest of the home directory still depends on what ran before it."""
        snapshot, begin = self.snapshot(), role_profile.BEGIN.encode()
        files = {k: v for k, v in snapshot.items() if k.parts[0] not in (".codex", ".claude")}
        for provider, (folder, filename) in TARGETS.items():
            note = snapshot.get(Path(folder, filename), b"")
            block = note[note.find(begin):] if begin in note else b""
            if provider not in playbook.providers(profile):
                self.assertEqual(block, b"", f"{folder}/{filename} keeps a t3-boat block for a disabled provider")
                continue
            files[Path(folder, filename)] = block
            if block:  # The instructions name the skills, so they are part of what is applied.
                files.update({k: v for k, v in snapshot.items() if k.parts[:2] == (folder, "skills")})
        return files

    def interrupted(self, before, name, after, workflow, recovering, own_rows):
        """A commit of `name` stopped just after its record, with `recovering`'s own rows saved first if asked."""
        self.reset()
        before(self)
        self.recovery("start", name)  # In this process: there are many subtests.
        if own_rows:
            seeded_roles(self.home, recovering)
        self.commit_rows(name, after(self, name), workflow, stop_at=1)

    def test_an_interrupted_recovery_keeps_the_record_until_every_file_is_regenerated(self):
        # A container start finishes the commit in prepare(); setup, status and boat-worker in recover(). Either
        # may run under the profile that committed or under another one sharing the volume, and may itself stop.
        entries = ("start", "recover")
        name = "codex+claude"
        for label, before, after, workflow in self.SCENARIOS:
            for recovering, own_rows in ((name, False), *((p, rows) for p in PROFILES if p != name
                                                          for rows in (False, True))):
                case = (before, name, after, workflow, recovering, own_rows)
                # The clean result: the whole commit, then a start of the recovering profile.
                self.reset()
                before(self)
                self.recovery("start", name)
                if own_rows:
                    seeded_roles(self.home, recovering)
                self.commit_rows(name, after(self, name), workflow)
                self.recovery("start", recovering)
                clean, clean_applied = self.snapshot(), self.applied(recovering)
                # One uninterrupted recovery: its writes, counted, and the state that every retry must reach.
                self.interrupted(*case)
                total = len(self.recovery("recover", recovering))
                recovered = self.snapshot()
                self.assertFalse(role_profile.journal_path(self.home).exists())
                with self.subTest(scenario=label, recovering=recovering, own_rows=own_rows):
                    if recovering == name:
                        self.assertEqual(recovered, clean)
                    else:
                        self.assertEqual(self.applied(recovering), clean_applied)
                finished = 1 + len(playbook_skills.state_files(self.home, workflow))  # Rows and workflow first.
                self.assertGreater(total, finished)
                for first, retry in ((a, b) for a in entries for b in entries):
                    for stop_at in range(total):
                        with self.subTest(scenario=label, recovering=recovering, own_rows=own_rows, first=first,
                                          retry=retry, stop_at=stop_at):
                            self.interrupted(*case)
                            self.recovery(first, recovering, stop_at)
                            # Up to the last regenerated file, the record must survive the stop.
                            self.assertTrue(role_profile.journal_path(self.home).exists())
                            if stop_at >= finished:  # The rows and the workflow are already written.
                                self.assertEqual(playbook_skills.workflow(self.home), workflow)
                            # A second stop in the retry also keeps it; then one full retry finishes everything.
                            self.recovery(retry, recovering, stop_at)
                            self.assertTrue(role_profile.journal_path(self.home).exists())
                            self.recovery(retry, recovering)
                            self.assertFalse(role_profile.journal_path(self.home).exists())
                            snapshot = self.snapshot()
                            if snapshot != recovered:  # Name the files; a full diff of the snapshots is too slow.
                                self.fail("differs from an uninterrupted recovery: " + ", ".join(
                                    sorted(str(k) for k in snapshot.keys() | recovered.keys()
                                           if snapshot.get(k) != recovered.get(k))))

    def test_status_alone_regenerates_after_a_recovery_stopped_before_it_regenerated(self):
        # An earlier release's role installation choosing generic workers with one model.
        name = "codex-only"
        seeded_roles(self.home, name)
        self.prepare(name)
        rows = rows_for(name, only_lead=True)
        self.commit_rows(name, rows, "generic")
        self.prepare(name)
        expected = self.snapshot()
        self.reset()
        seeded_roles(self.home, name)
        self.prepare(name)
        self.commit_rows(name, rows, "generic", stop_at=1)
        # The rows and the workflow are written, nothing generated from them yet.
        self.recovery("recover", name, 1 + len(playbook_skills.state_files(self.home, "generic")))
        self.assertEqual(playbook_skills.workflow(self.home), "generic")
        self.assertEqual(json.loads((self.home / "profiles/codex-only.json").read_text()), rows)
        self.assertIn("## Role playbook", self.skill("codex"))
        self.assertTrue(role_profile.journal_path(self.home).exists())
        with patch.dict(os.environ, HOME=str(self.home)), patch.object(cli, "t3_online", return_value=True), \
                patch.object(cli, "logged_in", return_value=True), patch.object(cli, "probe", return_value=None), \
                redirect_stdout(StringIO()) as out:
            self.assertEqual(cli.status(name, cli.PROFILES[name]), 1)  # No Boat account in this test.
        self.assertIn("Workflow: generic workers", out.getvalue())
        self.assertIn(f"[ok] Global profile {name} applied", out.getvalue())
        self.assertFalse(role_profile.journal_path(self.home).exists())
        self.assertGeneric(self.skill("codex"))
        self.assertEqual(self.snapshot(), expected)

    def test_a_failed_regeneration_keeps_the_record_for_the_next_attempt(self):
        name = "codex-only"
        self.prepare(name)
        self.commit_rows(name, rows_for(name, only_lead=True), "generic", stop_at=1)
        settings = self.home / ".t3/userdata/settings.json"
        good = settings.read_bytes()
        settings.write_text("[")  # Unreadable until the owner repairs it.
        result = self.prepare(name, check=False)
        self.assertIn(f"{settings}:", result.stderr)
        self.assertTrue(role_profile.journal_path(self.home).exists())
        settings.write_bytes(good)
        self.prepare(name)
        self.assertFalse(role_profile.journal_path(self.home).exists())
        self.assertGeneric(self.skill("codex"))

    def test_an_unreadable_commit_record_names_itself(self):
        journal = role_profile.journal_path(self.home)
        journal.parent.mkdir(parents=True)
        journal.write_text("{\"profile\": 1}")
        result = self.prepare("codex-only", check=False)
        self.assertEqual(result.stderr.strip(), f"T3 Boat profile: {journal}: not a record of an interrupted setup; "
                                                "delete it and rerun t3-boat setup")


class ProfileSwitchTests(Home):
    """The workflow is one setting for the machine; each T3_BOAT_PROFILE keeps its own rows."""

    def test_every_profile_left_behind_by_another_profiles_switch_is_recovered(self):
        for stranded in PROFILES:
            for switcher in PROFILES:
                if switcher == stranded:
                    continue
                with self.subTest(stranded=stranded, switcher=switcher):
                    self.reset()
                    path = self.home / "profiles" / (stranded + ".json")
                    # A generic setup under one profile saved only its lead row...
                    self.commit("generic")
                    rows = lead_only(self.home, stranded)
                    saved = path.read_bytes()
                    # ...then setup under another profile chose the role playbook for the whole machine.
                    error, output = self.setup_run("playbook", "", "", profile=switcher)
                    self.assertIsInstance(error, cli.SetupError)
                    self.assertEqual(playbook_skills.workflow(self.home), "playbook")
                    self.assertIn(f"Note: {path} does not fit the playbook workflow yet. Run setup with "
                                  f"T3_BOAT_PROFILE={stranded}", output)
                    self.assertEqual(path.read_bytes(), saved)
                    # Starting the left-behind profile runs T3 without applying it, instead of failing.
                    result = self.prepare(stranded)
                    self.assertIn(f"T3 Boat: {path}: expected roles", result.stderr)
                    self.assertIn(f"run t3-boat setup with T3_BOAT_PROFILE={stranded} to complete it", result.stderr)
                    self.assertNotIn("defaultModelSelection", self.startup_settings())
                    self.assertEqual(path.read_bytes(), saved)
                    with patch.dict(os.environ, HOME=str(self.home)), self.assertRaises(cli.ProfileError) as status:
                        cli.parent_role(stranded)
                    self.assertIn(f"T3_BOAT_PROFILE={stranded}", str(status.exception))
                    # Its setup reaches the picker, keeps the saved lead row and suggests the missing roles.
                    error, output = self.setup_run("", "", "", profile=stranded)
                    self.assertIsInstance(error, cli.SetupError, output)
                    self.assertIn("Setup keeps its saved rows and suggests the missing ones", output)
                    completed = json.loads(path.read_text())
                    self.assertEqual(completed["lead"], rows["lead"])
                    self.assertEqual(set(completed), set(playbook.role_keys()))
                    self.prepare(stranded)
                    self.assertEqual(self.startup_settings()["defaultModelSelection"]["model"], rows["lead"]["model"])
                    for provider in playbook.providers(stranded):
                        self.assertRoles(self.skill(provider), stranded, guides=True)

    def test_a_left_behind_profile_can_also_choose_generic_workers_again(self):
        self.commit("playbook")
        rows = lead_only(self.home, "codex-only")
        error, _ = self.setup_run("generic", "", "")
        self.assertIsInstance(error, cli.SetupError)
        self.assertEqual(json.loads((self.home / "profiles/codex-only.json").read_text()), rows)
        self.assertEqual(playbook_skills.workflow(self.home), "generic")
        self.assertGeneric(self.skill("codex"))

    def test_an_earlier_installation_still_refuses_an_incomplete_profile_before_sign_in(self):
        rows = seeded_roles(self.home, "codex-only")
        del rows["arbiter"]
        path = self.home / "profiles/codex-only.json"
        path.write_text(json.dumps(rows))
        error, output = self.setup_run("", "")
        self.assertIsInstance(error, cli.ProfileError)
        self.assertEqual(str(error), f"{path}: expected roles {', '.join(playbook.role_keys())}; missing arbiter")
        self.assertNotIn("Workflow [", output)


class WorkerTests(Home):
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

    def probes(self, profile):
        project = self.stub(ssh_status=0)
        result, _ = self.run_worker(project, profile, "verify", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.removeprefix("[ok] ") for line in result.stdout.splitlines() if "subscription reply" in line]

    def test_generic_check_probes_the_lead_and_cli_defaults_never_presets(self):
        self.commit("generic")
        for name in PROFILES:
            with self.subTest(profile=name):
                rows = seeded_roles(self.home, name)
                # A preset of the lead's provider with another model than the lead's.
                for key, row in rows.items():
                    if key != "lead" and row["provider"] == rows["lead"]["provider"]:
                        row["model"] = "preset-" + key
                (self.home / "profiles" / (name + ".json")).write_text(json.dumps(rows))
                lead = rows["lead"]
                expected = [f"{p} subscription reply ({lead['model']} / {lead['effort']})" if p == lead["provider"]
                            else f"{p} subscription reply (CLI default)" for p in playbook.providers(name)]
                self.assertEqual(self.probes(name), expected)

    def test_role_workflows_check_the_executor_as_earlier_releases_did(self):
        for workflow in ("playbook", "roles"):
            for name in PROFILES:
                with self.subTest(workflow=workflow, profile=name):
                    self.reset()
                    self.commit(workflow)
                    rows = seeded_roles(self.home, name)
                    expected = []
                    for provider in playbook.providers(name):
                        row = next(r for r in (rows["executor"], *rows.values()) if r["provider"] == provider)
                        expected.append(f"{provider} subscription reply ({row['model']} / {row['effort']})")
                    self.assertEqual(self.probes(name), expected)

    def test_check_receipts_name_what_was_checked(self):
        env, credential = {"id": "env1", "version": 3}, "creds"
        rows = seeded_roles(self.home, "codex-only")
        with patch.dict(os.environ, HOME=str(self.home)):
            legacy = cli.hashlib.sha256(json.dumps([rows, "env1", 3, credential], sort_keys=True).encode()).hexdigest()
            self.assertEqual(cli.check_signature("codex-only", env, credential), legacy)  # Earlier receipts hold.
            self.commit("generic")
            self.assertNotIn(cli.check_signature("codex-only", env, credential), (legacy, None))
            lead_only(self.home, "codex-only")
            self.commit("playbook")
            self.assertIsNone(cli.check_signature("codex-only", env, credential))

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
