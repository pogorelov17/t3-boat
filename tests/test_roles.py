import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}

    def prepare(self, profile, check=True):
        return subprocess.run([sys.executable, str(ROOT / "bin/profile.py"), profile], env=self.env,
                              capture_output=True, text=True, check=check)

    def test_all_profiles_switch_idempotently_and_preserve_user_settings(self):
        settings = self.home / ".t3/userdata/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"existingSetting": 5, "providerInstances": {"codex": {"name": "My Codex"}}}))
        instructions = self.home / ".codex/AGENTS.md"
        instructions.parent.mkdir()
        instructions.write_text("Personal instructions\n")
        for name in ("codex-only", "codex+claude", "claude-only"):
            with self.subTest(name=name):
                self.prepare(name)
                snapshot = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
                self.prepare(name)
                self.assertEqual(snapshot, {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()})
                cfg = json.loads(settings.read_text())
                self.assertEqual(cfg["existingSetting"], 5)
                self.assertEqual(cfg["providerInstances"]["codex"]["name"], "My Codex")
                self.assertEqual(cfg["providerInstances"]["codex"]["enabled"], name != "claude-only")
                self.assertEqual(cfg["providerInstances"]["claudeAgent"]["enabled"], name != "codex-only")
                self.assertEqual(cfg["defaultModelSelection"]["instanceId"], "claudeAgent" if name == "claude-only" else "codex")
                self.assertTrue(instructions.read_text().startswith("Personal instructions\n"))
                for provider_dir, filename, selected in [
                    (".codex", "AGENTS.md", name != "claude-only"),
                    (".claude", "CLAUDE.md", name != "codex-only"),
                ]:
                    path = self.home / provider_dir / filename
                    self.assertEqual(path.exists() and "<!-- BEGIN t3-boat -->" in path.read_text(), selected)
                    if selected:
                        skill_path = path.parent / "skills/t3-boat/SKILL.md"
                        self.assertIn(str(skill_path), path.read_text())
                        installed = skill_path.read_text()
                        shared = (ROOT / "skills/t3-boat/SKILL.md").read_text().rstrip()
                        self.assertTrue(installed.startswith(shared + "\n"))
                        roles = json.loads((self.home / "profiles" / (name + ".json")).read_text())
                        for role, row in roles.items():
                            self.assertIn(f"| {role} | {row['provider']} | {row['model']} |", installed)

    def test_executor_cannot_review_even_with_a_different_effort(self):
        self.prepare("codex-only")
        path = self.home / "profiles/codex-only.json"
        rows = json.loads(path.read_text())
        for role in ("reviewer1", "reviewer2", "adjudicator"):
            with self.subTest(role=role):
                edited = dict(rows)
                edited[role] = dict(rows["executor"], effort="low")
                path.write_text(json.dumps(edited))
                result = self.prepare("codex-only", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(role, result.stderr)
                self.assertEqual(json.loads(path.read_text()), edited)

    def test_stock_claude_volume_upgrades_to_independent_review(self):
        path = self.home / "profiles/claude-only.json"
        path.parent.mkdir()
        old = json.loads((ROOT / "profiles/claude-only.json").read_text())
        for role in ("reviewer2", "adjudicator"):
            old[role] = dict(old["executor"])
        path.write_text(json.dumps(old, indent=2) + "\n")
        self.prepare("claude-only")
        upgraded = json.loads(path.read_text())
        for role in ("reviewer1", "reviewer2", "adjudicator"):
            self.assertNotEqual(upgraded[role]["model"], upgraded["executor"]["model"])

    def test_profile_edits_take_effect_and_invalid_provider_fails_before_changes(self):
        self.prepare("claude-only")
        path = self.home / "profiles/claude-only.json"
        rows = json.loads(path.read_text())
        rows["orchestrator"].update(model="custom-opus", effort="medium")
        path.write_text(json.dumps(rows))
        self.prepare("claude-only")
        settings = self.home / ".t3/userdata/settings.json"
        selection = json.loads(settings.read_text())["defaultModelSelection"]
        self.assertEqual(selection["model"], "custom-opus")
        self.assertEqual(selection["options"], [{"id": "effort", "value": "medium"}])
        before = settings.read_bytes()
        rows["executor"]["provider"] = "codex"
        path.write_text(json.dumps(rows))
        self.assertNotEqual(self.prepare("claude-only", check=False).returncode, 0)
        self.assertEqual(settings.read_bytes(), before)

    def test_worker_uses_bound_environment_and_only_selected_provider_and_deletes_on_failure(self):
        project = self.home / "project"
        project.mkdir()
        for command in (["git","init","-q"], ["git","config","remote.origin.url","https://github.com/sample/repo.git"],
                        ["git","config","boat.environment","team"]):
            subprocess.run(command, cwd=project, env=self.env, check=True)
        boat = self.home / "boat"
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
        for name, role, expected, excluded in [
            ("codex-only", "reviewer1", "codex login status", "claude auth status"),
            ("claude-only", "reviewer2", "claude auth status", "codex login status"),
            ("codex+claude", "executor", "claude auth status", "codex login status"),
        ]:
            with self.subTest(profile=name):
                calls = self.home / "calls"
                calls.unlink(missing_ok=True)
                env = self.env | {"PATH": str(self.home) + ":" + self.env["PATH"], "T3_BOAT_PROFILE": name}
                result = subprocess.run([ROOT / "bin/boat-worker", "start", role], cwd=project, env=env,
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                invoked = [json.loads(line) for line in calls.read_text().splitlines()]
                self.assertIn("--no-snapshots", invoked[0])
                self.assertEqual(invoked[0][-2:], ["--environment", "team"])
                self.assertEqual(invoked[-1], ["delete", "--yes", "bx_1"])
                self.assertEqual(any(cmd[0] == "list" for cmd in invoked), name == "claude-only")
                script = next(cmd for cmd in invoked if cmd[0] == "ssh")[2]
                self.assertIn(expected, script)
                self.assertNotIn(excluded, script)
                self.assertNotIn("git clone", script)


if __name__ == "__main__":
    unittest.main()
