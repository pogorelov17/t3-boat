import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
cli = SourceFileLoader("t3_boat_runtime", str(ROOT / "bin/t3-boat")).load_module()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        env = patch.dict(os.environ, HOME=self.home.name, GIT_CONFIG_NOSYSTEM="1")
        env.start()
        self.addCleanup(env.stop)
        self.boat_env = {"id": "abc", "name": "base", "safeForThirdParties": False, "passGithub": True,
                         "passAgentsCredentials": True, "credentialProfileId": None,
                         "selectedRepositories": [{"fullName": "owner/example"}]}
        self.profiles = [{"id": "p", "isDefault": True, "connected": ["chatgpt-subscription"]}]

    def test_https_and_ssh_urls_without_credentials_or_traversal(self):
        for url in ("https://github.com/Owner/example.git/", "git@github.com:Owner/example.git"):
            self.assertEqual(cli.repo_slug(url), "Owner/example")
        for url in ("https://github.com/o/..", "https://github.com/o/.git.git", "https://SECRET@github.com/o/r",
                    "https://github.com/o/r?token=secret", "https://github.com/o/r\n"):
            with self.subTest(url=url), self.assertRaises(cli.SetupError):
                cli.repo_slug(url)

    def test_selected_environment_controls_worker_auth(self):
        cli.check_access(self.boat_env, self.profiles, ("codex",))
        with self.assertRaisesRegex(cli.SetupError, "claude-subscription"):
            cli.check_access(self.boat_env, self.profiles, ("codex", "claude"))
        self.boat_env["safeForThirdParties"] = True
        with self.assertRaises(cli.SetupError):
            cli.check_access(self.boat_env, self.profiles, ("codex",))
        self.boat_env.update(safeForThirdParties=False, credentialProfileId="other")
        with self.assertRaises(cli.SetupError):
            cli.check_access(self.boat_env, self.profiles, ("codex",))
        self.boat_env["credentialProfileId"] = None
        with self.assertRaises(cli.SetupError):
            cli.check_access(self.boat_env, self.profiles * 2, ("codex",))

    def test_project_stub_registration_is_idempotent_and_fetch_is_disabled(self):
        # Real git, with a recorder for the T3 CLI's new/already-added responses.
        t3 = Path(self.home.name, "t3")
        t3.write_text("#!/bin/sh\nif test -f \"$HOME/added\"; then echo 'An active project already exists for ...' >&2; exit 1; fi\ntouch \"$HOME/added\"\n")
        t3.chmod(0o755)
        with patch.dict(os.environ, PATH=self.home.name + os.pathsep + os.environ["PATH"]), redirect_stdout(StringIO()):
            for _ in range(2):
                cli.register("owner/example", "abc")
            repo = cli.projects()[0].parent
            cmd = ["git", "-C", str(repo)]
            self.assertEqual(subprocess.check_output([*cmd, "config", "--get", "boat.environment"], text=True).strip(), "abc")
            self.assertEqual([f.name for f in repo.iterdir()], [".git"])
            fetch = subprocess.run([*cmd, "fetch", "origin"], capture_output=True, text=True)
            self.assertIn("transport 'https' not allowed", fetch.stderr)
            self.assertEqual(fetch.returncode, 128)

    def test_status_verifies_only_enabled_subscriptions_without_vm(self):
        calls = []
        def probe(*args):
            calls.append(args)
            if args[0] == "git":
                return "https://github.com/owner/example.git" if args[-1] == "remote.origin.url" else "abc"
            return ""
        for profile, providers in cli.PROFILES.items():
            with self.subTest(profile=profile):
                subprocess.run(["python3", str(ROOT / "bin/profile.py"), profile], check=True)
                self.profiles[0]["connected"] = [cli.SUBSCRIPTIONS[p] for p in providers]
                calls.clear()
                with patch.object(cli, "boat_records", side_effect=[[self.boat_env], self.profiles]), \
                        patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", side_effect=probe), \
                        patch.object(cli, "logged_in", return_value=True) as auth, \
                        patch.object(cli, "projects", return_value=[Path(self.home.name, "projects/owner/example/.git")]), \
                        redirect_stdout(StringIO()):
                    self.assertEqual(cli.status(profile, providers), 0)
                self.assertEqual([c.args[0] for c in auth.call_args_list], ["claude" if profile == "claude-only" else "codex"])
                self.assertFalse(any(c[:2] == ("boat", "new") for c in calls))

    def test_setup_resumes_project_and_checks_boat_without_local_worker_login(self):
        git_dir = Path(self.home.name, "projects/owner/example/.git")
        def probe(*args):
            if args[-1] == "remote.origin.url":
                return "git@github.com:owner/example.git"
            if args[-1] == "boat.environment":
                return "base"
            return ""
        credentials = [{"isDefault": True, "connected": ["claude-subscription", "chatgpt-subscription"]}]
        empty = dict(self.boat_env, selectedRepositories=[])
        with patch.object(cli, "t3_online", return_value=True), patch.object(cli, "probe", side_effect=probe), \
                patch.object(cli, "parent_role", return_value={"provider": "claudeAgent"}), \
                patch.object(cli, "logged_in", return_value=True) as auth, patch.object(cli, "run") as run, \
                patch.object(cli, "projects", return_value=[git_dir]), patch.object(cli, "register") as register, \
                patch.object(cli, "boat_records", side_effect=[[empty], credentials, [self.boat_env]]), \
                patch("builtins.input", side_effect=["", "y"]), redirect_stdout(StringIO()):
            cli.setup("codex+claude", cli.PROFILES["codex+claude"])
        auth.assert_called_once_with("claude")
        register.assert_called_once_with("owner/example", "base")
        self.assertEqual([call.args for call in run.call_args_list], [
            ("install-claude",), ("boat", "env", "add-repo", "base", "owner/example")])

    def test_local_claude_subscription_auth(self):
        for method, expected in (("claude.ai", True), ("oauth_token", True), ("api_key", False)):
            with self.subTest(method=method), patch.object(cli, "probe", return_value=(
                    '{"loggedIn":true,"apiProvider":"firstParty","authMethod":"' + method + '"}')):
                self.assertEqual(cli.logged_in("claude"), expected)

    def test_empty_status_nonzero_with_next_step(self):
        output = StringIO()
        with patch.object(cli, "probe", return_value=None), patch.object(cli, "t3_online", return_value=False), redirect_stdout(output):
            self.assertEqual(cli.status("codex-only", ("codex",)), 1)
        self.assertIn("t3-boat setup", output.getvalue())
        self.assertNotIn("claude", output.getvalue())


if __name__ == "__main__":
    unittest.main()
