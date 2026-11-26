"""The suggested external playbook: offered in setup, downloaded at its pinned commit only when selected."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import playbook
import playbook_fetch
import playbook_skills
from packages import write_package
from test_profile import save
import test_setup_playbook

GIT = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def commit_package(repository, link=False):
    """A local repository whose one commit holds a package; returns its commit ID. It stands in for the suggested
    repository, so these tests never use the network."""
    write_package(repository, name="suggested-crew", roles={"helm": 1, "deck": 2}, sets={})
    if link:
        (repository / "shortcut.md").symlink_to("team.json")
    env = dict(os.environ, **GIT)
    for command in (["init", "--quiet"], ["add", "-A"], ["commit", "--quiet", "-m", "package"]):
        subprocess.run(["git", "-C", str(repository), *command], check=True, env=env)
    return subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


class Suggested:
    """Point the suggested playbook at a local repository for one test."""

    def use_repository(self, link=False):
        self.repository = self.home / "remote"
        self.commit = commit_package(self.repository, link=link)
        for name, value in (("REPOSITORY", "file://" + str(self.repository)), ("COMMIT", self.commit)):
            patcher = patch.object(playbook_fetch, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.home / "teams" / f"agent-playbook-{self.commit[:12]}"

    def break_network(self):
        patcher = patch.object(playbook_fetch, "REPOSITORY", "file://" + str(self.home / "unreachable"))
        patcher.start()
        self.addCleanup(patcher.stop)


class ChooseSuggestedTests(Suggested, unittest.TestCase):
    setUp = test_setup_playbook.ChoosePlaybookTests.setUp
    choose = test_setup_playbook.ChoosePlaybookTests.choose

    def test_fresh_enter_attaches_nothing_and_downloads_nothing(self):
        with patch.object(playbook_fetch, "fetch", side_effect=AssertionError("downloaded")):
            result, output = self.choose("codex-only", None, "")
        self.assertIsNone(result)
        self.assertIn(f"Suggested: agent-playbook, https://github.com/pogorelov17/agent-playbook at commit "
                      f"{playbook_fetch.COMMIT[:12]}. Enter 'agent-playbook' to download", output)
        self.assertIn("Enter for none, 'agent-playbook' for the suggested playbook", output)
        self.assertEqual(sorted(p.name for p in (self.home / "teams").iterdir()), ["harbor"])

    def test_selecting_it_downloads_the_pinned_commit_once_and_attaches_it(self):
        self.use_repository()
        result, output = self.choose("codex-only", None, "agent-playbook", "helm")
        self.assertEqual(result, {"path": str(self.target), "model_set": None, "lead_role": "helm"})
        self.assertIn(f"Attaching suggested-crew (version 7.1.0, commit {self.commit[:12]}) at {self.target}", output)
        self.assertEqual((self.target / ".commit").read_text(), self.commit + "\n")
        self.assertEqual(sorted(p.name for p in (self.home / "teams").iterdir()),
                         sorted(["harbor", self.target.name]))  # No temporary download is left.
        self.break_network()  # The downloaded release is read from the volume again, offline.
        current = result
        result, output = self.choose("codex-only", current, "agent-playbook", "", "")
        self.assertEqual(result, current)
        self.assertNotIn("could not be downloaded", output)

    def test_a_failed_download_keeps_the_current_choice_and_offers_a_retry(self):
        self.use_repository()
        self.break_network()
        current = {"path": str(self.custom), "model_set": "codex-crew", "lead_role": "qa_bot"}
        result, output = self.choose("codex-only", current, "agent-playbook", "", "", "")
        self.assertEqual(result, current)
        self.assertIn("agent-playbook could not be downloaded: git fetch failed", output)
        self.assertIn("Nothing was changed. Check this server's network and enter 'agent-playbook' to retry, "
                      "Enter to keep the current playbook, 'none' to detach it, or the path", output)
        self.assertFalse(self.target.exists())
        self.assertEqual(sorted(p.name for p in (self.home / "teams").iterdir()), ["harbor"])
        result, output = self.choose("codex-only", None, "agent-playbook", "")
        self.assertIsNone(result)
        self.assertIn("retry, Enter for none, or the path", output)
        result, _ = self.choose("codex-only", None, "agent-playbook", str(self.custom), "rigger", "none")
        self.assertEqual(result, {"path": str(self.custom), "model_set": None, "lead_role": "rigger"})

    def test_retry_after_the_network_returns(self):
        self.use_repository()
        repository = playbook_fetch.REPOSITORY
        self.break_network()

        def restore():
            playbook_fetch.REPOSITORY = repository
            return "agent-playbook"
        result, output = self.choose("claude-only", None, "agent-playbook", restore, "none")
        self.assertEqual(result, {"path": str(self.target), "model_set": None, "lead_role": None})
        self.assertIn("could not be downloaded", output)


class FetchTests(Suggested, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_the_pin_matches_the_bundled_build(self):
        script = (ROOT / "scripts/fetch-playbook").read_text()
        self.assertEqual(re.search(r"^commit=([0-9a-f]{40})$", script, re.MULTILINE)[1], playbook_fetch.COMMIT)
        self.assertEqual(playbook_fetch.REPOSITORY, "https://github.com/pogorelov17/agent-playbook.git")

    def test_the_download_is_written_as_data_and_never_runs_package_code(self):
        self.use_repository()
        marker = self.home / "ran"
        script = self.repository / "scripts/check.py"
        script.parent.mkdir()
        script.write_text(f"open({str(marker)!r}, 'w').close()\n")
        (self.repository / "scripts/check.py").chmod(0o755)
        env = dict(os.environ, **GIT)
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.repository), "commit", "--quiet", "-m", "script"], check=True, env=env)
        commit = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], check=True, env=env,
                                capture_output=True, text=True).stdout.strip()
        target = playbook_fetch.fetch(self.home / "teams", commit=commit)
        self.assertFalse(marker.exists())
        self.assertEqual(target.name, f"agent-playbook-{commit[:12]}")
        self.assertEqual(playbook.load(str(target)).commit, commit)
        self.assertFalse(os.access(target / "scripts/check.py", os.X_OK))

    def test_links_and_wrong_commits_are_refused_and_nothing_is_left(self):
        self.use_repository(link=True)
        with self.assertRaisesRegex(playbook_fetch.FetchError, "shortcut.md in the pinned commit is not a regular file"):
            playbook_fetch.fetch(self.home / "teams")
        with self.assertRaisesRegex(playbook_fetch.FetchError, "git fetch failed"):
            playbook_fetch.fetch(self.home / "teams", commit="0" * 40)
        with self.assertRaisesRegex(playbook_fetch.FetchError, "not a full commit ID"):
            playbook_fetch.fetch(self.home / "teams", commit="main")
        self.assertEqual(list((self.home / "teams").iterdir()), [])

    def test_an_invalid_package_at_the_pin_is_refused(self):
        self.use_repository()
        (self.repository / "team.json").write_text("{}")
        env = dict(os.environ, **GIT)
        subprocess.run(["git", "-C", str(self.repository), "commit", "--quiet", "-am", "break"], check=True, env=env)
        commit = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], check=True, env=env,
                                capture_output=True, text=True).stdout.strip()
        with self.assertRaisesRegex(playbook_fetch.FetchError, "not a valid playbook: .*schema 1"):
            playbook_fetch.fetch(self.home / "teams", commit=commit)
        self.assertEqual(list((self.home / "teams").iterdir()), [])

    def test_an_owner_folder_with_the_download_name_is_never_replaced(self):
        self.use_repository()
        self.target.mkdir(parents=True)
        (self.target / "mine.md").write_text("mine\n")
        with self.assertRaisesRegex(playbook_fetch.FetchError, "already exists and is not the downloaded commit"):
            playbook_fetch.fetch(self.home / "teams")
        self.assertEqual([p.name for p in self.target.iterdir()], ["mine.md"])


class SetupSuggestedTests(Suggested, unittest.TestCase):
    setUp = test_setup_playbook.SetupWithPlaybookTests.setUp
    run_setup = test_setup_playbook.SetupWithPlaybookTests.run_setup

    def test_setup_downloads_applies_and_reports_the_suggested_playbook(self):
        self.use_repository()
        code, output = self.run_setup("codex-only", "agent-playbook", "helm", "gpt-6-luna", "", "", "")
        self.assertEqual(code, 0, output)
        state = json.loads((self.home / "profiles/codex-only.json").read_text())
        self.assertEqual(state["playbook"], {"path": str(self.target), "model_set": None, "lead_role": "helm"})
        self.assertIn(f"Playbook: suggested-crew (version 7.1.0, commit {self.commit[:12]}) at {self.target}", output)
        package = playbook.load(str(self.target))
        for role in package.roles:
            self.assertTrue(playbook_skills.generated(playbook_skills.skill_path(self.home / ".codex", package, role)))
        self.assertIn("helm guide at", (self.home / ".codex/AGENTS.md").read_text())

    def test_a_failed_download_in_setup_keeps_the_saved_profile(self):
        self.use_repository()
        self.break_network()
        saved = save(self.home, "codex-only", {"path": str(self.custom), "model_set": None, "lead_role": "rigger"})
        before = saved.read_text()
        code, output = self.run_setup("codex-only", "agent-playbook", "", "", "none", "", "", "")
        self.assertEqual(code, 0, output)
        self.assertIn("could not be downloaded", output)
        self.assertEqual(json.loads(saved.read_text())["playbook"], json.loads(before)["playbook"])
        self.assertFalse(self.target.exists())

    def test_setup_without_a_playbook_stays_generic_and_offline(self):
        with patch.object(playbook_fetch, "fetch", side_effect=AssertionError("downloaded")):
            code, output = self.run_setup("codex-only", "", "gpt-6-luna", "", "", "")
        self.assertEqual(code, 0, output)
        self.assertIsNone(json.loads((self.home / "profiles/codex-only.json").read_text())["playbook"])
        self.assertIn("## Workers and briefs", (self.home / ".codex/skills/t3-boat/SKILL.md").read_text())
        self.assertFalse(list((self.home / ".codex/skills").glob("playbook-*")))


if __name__ == "__main__":
    unittest.main()
