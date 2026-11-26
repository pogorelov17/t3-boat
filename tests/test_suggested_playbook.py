"""The suggested external playbook: offered in setup, downloaded at its pinned commit only when selected."""
import hashlib
import json
import os
import re
import shutil
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
import playbook_pin
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


def pin_of(repository, commit):
    """The pin of a commit in a local repository, read from git's objects as the hard-coded pin was."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True,
                              env=dict(os.environ, **GIT)).stdout
    files = {}
    for entry in filter(None, git("ls-tree", "-r", "-z", commit).split(b"\0")):
        meta, _, name = entry.partition(b"\t")
        mode, _, blob = meta.decode().split()
        if mode in ("100644", "100755"):
            files[name.decode()] = (mode, blob, hashlib.sha256(git("cat-file", "blob", blob)).hexdigest())
    return playbook_pin.Pin(commit, git("rev-parse", commit + "^{tree}").decode().strip(), files)


def snapshot(folder):
    """Every entry under a folder, with link targets and file bytes, to show it was left as it was."""
    result = {}
    for path in sorted(Path(folder).rglob("*")):
        rel = str(path.relative_to(folder))
        result[rel] = (("link", os.readlink(path)) if path.is_symlink() else ("file", path.read_bytes())
                       if path.is_file() else ("folder", None))
    return result


class Suggested:
    """Point the suggested playbook at a local repository for one test."""

    def use_repository(self, link=False):
        self.repository = self.home / "remote"
        self.commit = commit_package(self.repository, link=link)
        self.pin = pin_of(self.repository, self.commit)
        for owner, name, value in ((playbook_fetch, "REPOSITORY", "file://" + str(self.repository)),
                                   (playbook_pin, "PIN", self.pin)):
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.home / "teams" / f"agent-playbook-{self.commit[:12]}"

    def offline(self):
        """Fail the test if anything runs git, and so if anything reaches the network."""
        patcher = patch.object(playbook_fetch, "_git", side_effect=AssertionError("git ran"))
        patcher.start()
        self.addCleanup(patcher.stop)

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
                      f"{playbook_pin.COMMIT[:12]}. Enter 'agent-playbook' to download", output)
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

    def test_reuse_is_offline_and_a_changed_folder_is_refused_and_left_as_it_is(self):
        self.use_repository()
        playbook_fetch.fetch(self.home / "teams")
        self.offline()
        result, output = self.choose("codex-only", None, "agent-playbook", "helm")
        self.assertEqual(result["path"], str(self.target))
        self.assertIn(f"Checking {self.target} against the pinned commit {self.commit} (offline)", output)
        self.assertIn(f"Attaching suggested-crew (version 7.1.0, commit {self.commit[:12]})", output)
        pristine = snapshot(self.target)
        guide = self.target / "crew/helm/ROLE.md"
        outside = self.home / "copy.md"
        damages = {
            "changed": (lambda: guide.write_text("EDITED\n"), "crew/helm/ROLE.md differs from the pinned commit"),
            "added": (lambda: (self.target / "crew/helm/extra.md").write_text("x\n"), "crew/helm/extra.md was added"),
            "added folder": (lambda: (self.target / "notes").mkdir(), "notes/ was added"),
            "missing": (lambda: (self.target / "common/report.md").unlink(), "common/report.md is missing"),
            # A link to a byte-identical copy is still refused: only plain files are read.
            "symlinked file": (lambda: (outside.write_bytes(guide.read_bytes()), guide.unlink(),
                                        guide.symlink_to(outside)), "crew/helm/ROLE.md is a link"),
            "symlinked folder": (lambda: (shutil.copytree(self.target / "crew/deck", self.home / "deck"),
                                          shutil.rmtree(self.target / "crew/deck"),
                                          (self.target / "crew/deck").symlink_to(self.home / "deck")),
                                 "crew/deck is a link"),
            "other commit": (lambda: (self.target / ".commit").write_text("0" * 40 + "\n"),
                             f".commit does not name commit {self.commit[:12]}"),
        }
        for label, (damage, reason) in damages.items():
            with self.subTest(label):
                shutil.rmtree(self.target)
                for rel, (kind, data) in pristine.items():
                    if kind == "folder":
                        (self.target / rel).mkdir(parents=True, exist_ok=True)
                    else:
                        (self.target / rel).parent.mkdir(parents=True, exist_ok=True)
                        (self.target / rel).write_bytes(data)
                damage()
                before = snapshot(self.target)
                result, output = self.choose("codex-only", None, "agent-playbook", "")
                self.assertIsNone(result)  # Enter then attaches nothing.
                self.assertIn(f"agent-playbook was not attached: {self.target} does not hold the pinned commit "
                              f"{self.commit[:12]}: ", output)
                self.assertIn(reason, output)
                self.assertIn("Enter its path to attach it as your own package (shown as unverified)", output)
                self.assertNotIn(f"commit {self.commit[:12]}) at {self.target}", output)
                self.assertEqual(snapshot(self.target), before)  # Left exactly as the owner had it.
                if label in ("changed", "added", "other commit"):
                    # Its path attaches it as the owner's own package, labelled as unverified.
                    result, output = self.choose("codex-only", None, str(self.target), "helm")
                    self.assertEqual(result["path"], str(self.target))
                    self.assertNotIn(f" commit {self.commit[:12]}) at", output.replace("unverified commit", ""))
                    self.assertEqual(snapshot(self.target), before)

    def test_a_folder_changed_after_its_check_is_not_attached(self):
        self.use_repository()
        fetch = playbook_fetch.fetch

        def tamper(*args, **kwargs):
            target = fetch(*args, **kwargs)
            (target / "crew/helm/ROLE.md").write_text("# helm\n\nSWAPPED\n")
            return target
        with patch.object(playbook_fetch, "fetch", side_effect=tamper):
            result, output = self.choose("codex-only", None, "agent-playbook", "")
        self.assertIsNone(result)
        self.assertIn("crew/helm/ROLE.md differs from the pinned commit", output)
        self.assertNotIn("Attaching", output)

    def test_a_commit_file_alone_is_shown_as_unverified(self):
        self.use_repository()
        (self.custom / ".commit").write_text(self.commit + "\n")  # The owner's package claims the pin.
        result, output = self.choose("codex-only", None, str(self.custom), "rigger", "none")
        self.assertEqual(result["path"], str(self.custom))
        self.assertIn(f"Attaching harbor-crew (version 7.1.0, unverified commit {self.commit[:12]}) at", output)
        self.assertIsNone(playbook.load(str(self.custom)).pin)


class FetchTests(Suggested, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_the_pin_matches_the_bundled_build(self):
        script = (ROOT / "scripts/fetch-playbook").read_text()
        self.assertEqual(re.search(r"^commit=([0-9a-f]{40})$", script, re.MULTILINE)[1], playbook_pin.COMMIT)
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
        target = playbook_fetch.fetch(self.home / "teams", pin=pin_of(self.repository, commit))
        self.assertFalse(marker.exists())
        self.assertEqual(target.name, f"agent-playbook-{commit[:12]}")
        self.assertEqual(playbook.load(str(target)).commit, commit)
        self.assertFalse(os.access(target / "scripts/check.py", os.X_OK))

    def test_links_and_wrong_commits_are_refused_and_nothing_is_left(self):
        self.use_repository(link=True)
        with self.assertRaisesRegex(playbook_fetch.FetchError, "shortcut.md in the pinned commit is not a regular file"):
            playbook_fetch.fetch(self.home / "teams")
        with self.assertRaisesRegex(playbook_fetch.FetchError, "git fetch failed"):
            playbook_fetch.fetch(self.home / "teams", pin=playbook_pin.Pin("0" * 40, "0" * 40, {}))
        with self.assertRaisesRegex(playbook_fetch.FetchError, "not a full commit ID"):
            playbook_fetch.fetch(self.home / "teams", pin=playbook_pin.Pin("main", "0" * 40, {}))
        self.assertEqual(list((self.home / "teams").iterdir()), [])

    def test_an_invalid_package_at_the_pin_is_refused(self):
        self.use_repository()
        (self.repository / "team.json").write_text("{}")
        env = dict(os.environ, **GIT)
        subprocess.run(["git", "-C", str(self.repository), "commit", "--quiet", "-am", "break"], check=True, env=env)
        commit = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], check=True, env=env,
                                capture_output=True, text=True).stdout.strip()
        with self.assertRaisesRegex(playbook_fetch.FetchError, "not a valid playbook: .*schema 1"):
            playbook_fetch.fetch(self.home / "teams", pin=pin_of(self.repository, commit))
        self.assertEqual(list((self.home / "teams").iterdir()), [])

    def test_an_owner_folder_with_the_download_name_is_never_replaced(self):
        self.use_repository()
        self.target.mkdir(parents=True)
        (self.target / "mine.md").write_text("mine\n")
        self.offline()
        with self.assertRaisesRegex(playbook_fetch.Mismatch, "does not hold the pinned commit .*mine.md was added"):
            playbook_fetch.fetch(self.home / "teams")
        self.assertEqual([p.name for p in self.target.iterdir()], ["mine.md"])


class PinTests(unittest.TestCase):
    """The hard-coded pin is bound to its commit, and checked against the real release."""

    def test_the_file_table_hashes_to_the_pinned_tree(self):
        self.assertEqual(playbook_pin.tree_id(playbook_pin.FILES), playbook_pin.TREE)
        files = dict(playbook_pin.FILES)
        mode, blob, _ = files["team.json"]
        files["team.json"] = (mode, blob, "0" * 64)  # A SHA-256 alone cannot be swapped: the blob ID is checked too.
        broken = dict(playbook_pin.FILES, **{"team.json": (mode, "0" * 40, "0" * 64)})
        differences, _ = playbook_pin.check(ROOT, playbook_pin.Pin(playbook_pin.COMMIT, playbook_pin.TREE, broken))
        self.assertEqual(differences, [f"the file table of commit {playbook_pin.COMMIT[:12]} does not hash to its "
                                       f"tree {playbook_pin.TREE[:12]}"])
        self.assertEqual(playbook_fetch.destination("/data/teams").name, "agent-playbook-a27b16a7d696")

    @unittest.skipUnless((ROOT / "playbook/.commit").is_file(), "no copy from sh scripts/fetch-playbook playbook")
    def test_the_bundled_build_holds_the_pinned_release(self):
        package = playbook.load(str(ROOT / "playbook"))
        self.assertEqual(package.pin, playbook_pin.COMMIT, package.differences)
        self.assertIn(f"commit {playbook_pin.COMMIT[:12]})", package.label())

    def test_real_download_tamper_and_offline_reuse(self):
        """Against GitHub: the pinned commit downloads, matches the table and loads as verified; reuse is offline;
        a tampered copy is refused and left as it is. Skipped only when github.com cannot be reached."""
        if os.environ.get("T3_BOAT_OFFLINE") == "1":
            self.skipTest("T3_BOAT_OFFLINE=1")
        try:
            subprocess.run(["git", "ls-remote", playbook_fetch.REPOSITORY, "HEAD"], check=True, capture_output=True,
                           timeout=30, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", **GIT))
        except (subprocess.SubprocessError, OSError):
            self.skipTest(f"{playbook_fetch.REPOSITORY} is unreachable from here")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        teams = Path(temporary.name)
        package = playbook_fetch.load(teams)
        target = playbook_fetch.destination(teams)
        self.assertEqual(package.root, target)
        self.assertEqual(package.pin, playbook_pin.COMMIT)
        self.assertEqual(sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()),
                         sorted([*playbook_pin.FILES, ".commit"]))
        self.assertIn("agent-playbook (version 2.0.0, commit a27b16a7d696)", package.label())
        with patch.object(playbook_fetch, "_git", side_effect=AssertionError("git ran")):
            self.assertEqual(playbook_fetch.load(teams).pin, playbook_pin.COMMIT)  # Offline reuse.
            (target / "roles/lead/ROLE.md").write_text("TAMPERED\n")
            before = snapshot(target)
            with self.assertRaisesRegex(playbook_fetch.Mismatch, "roles/lead/ROLE.md differs from the pinned commit"):
                playbook_fetch.load(teams)
            self.assertEqual(snapshot(target), before)
        tampered = playbook.load(str(target))
        self.assertIsNone(tampered.pin)
        self.assertIn("unverified commit a27b16a7d696", tampered.label())


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
