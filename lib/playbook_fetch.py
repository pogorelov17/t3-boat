"""Download the suggested external playbook at one pinned commit, only when the owner selects it in setup.

Nothing else downloads it: the default image, its build and a container start never fetch a playbook. The download
is data only. Git fetches the pinned commit, Python writes each file of its tree as a plain file, and
playbook.load checks the result like any other package. No code from the package runs on this server, and no
hook, filter, link or submodule from the repository is followed. The release lands in its own folder under
/data/teams, named after the commit, so a later setup or container start reads it from the volume without the
network.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import playbook

URL = "https://github.com/pogorelov17/agent-playbook"
# The same commit an image built with BUNDLE_PLAYBOOK=1 contains (scripts/fetch-playbook).
COMMIT = "a27b16a7d6962df7b26fc3233b8503f973f8d715"
REPOSITORY = URL + ".git"
MAX_TREE = 64 << 20  # Bytes of the whole downloaded tree; the package's own limits are checked when it loads.
TIMEOUT = 300


class FetchError(Exception):
    """The suggested playbook could not be downloaded or is not a valid package; the message says why."""


def destination(teams, commit=None):
    return Path(teams) / f"agent-playbook-{(commit or COMMIT)[:12]}"


def _git(work, *args, repository=None, commit=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    command = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "protocol.file.allow=always", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
               "-C", str(work / "repo"), *args]
    try:
        result = subprocess.run(command, capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise FetchError(f"git {args[0]} did not finish within {TIMEOUT} seconds") from None
    except OSError as error:
        raise FetchError(f"git cannot run: {error.strerror or error}") from None
    if result.returncode:
        detail = " ".join(result.stderr.decode("utf-8", errors="replace").split())[-300:]
        raise FetchError(f"git {args[0]} failed" + (f": {detail}" if detail else ""))
    return result.stdout


def _write_tree(work, tree, commit):
    """Write the commit's files under `tree` from git's object data: regular files only, nothing executable."""
    listing = _git(work, "ls-tree", "-r", "-z", "--full-tree", commit)
    total = 0
    for entry in filter(None, listing.split(b"\0")):
        meta, _, name = entry.partition(b"\t")
        mode, kind, blob = meta.decode("ascii").split()
        path = name.decode("utf-8", errors="replace")
        parts = path.split("/")
        if mode not in ("100644", "100755") or kind != "blob":
            raise FetchError(f"{path} in the pinned commit is not a regular file (mode {mode})")
        if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise FetchError(f"{path!r} in the pinned commit is not a plain relative path")
        data = _git(work, "cat-file", "blob", blob)
        total += len(data)
        if total > MAX_TREE:
            raise FetchError(f"the pinned commit holds more than {MAX_TREE} bytes")
        target = tree.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(data)


def fetch(teams, repository=None, commit=None):
    """Return the folder holding the pinned release under `teams`, downloading it first if it is not there.

    A folder of that name that does not hold the pinned release is never replaced: it may be the owner's.
    Raises FetchError naming the reason; nothing is left behind by a failed download.
    """
    repository = repository or REPOSITORY
    commit = commit or COMMIT
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise FetchError(f"{commit!r} is not a full commit ID")
    teams = Path(teams)
    target = destination(teams, commit)
    if target.exists() or target.is_symlink():
        try:
            recorded = playbook.read_regular(target / ".commit", 128).decode("ascii", errors="replace").strip()
        except OSError:
            recorded = ""
        if target.is_symlink() or not target.is_dir() or recorded != commit:
            raise FetchError(f"{target} already exists and is not the downloaded commit {commit[:12]}; move it "
                             "away to download again, or enter its path to attach it as your own package")
        return target
    try:
        teams.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".download-", dir=teams))
    except OSError as error:
        raise FetchError(f"{teams}: cannot be written: {error.strerror or error}") from None
    try:
        (work / "repo").mkdir()
        _git(work, "init", "--quiet")
        _git(work, "fetch", "--quiet", "--depth", "1", "--no-tags", repository, commit)
        fetched = _git(work, "rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        if fetched != commit:  # A commit ID names its content, so this pins every file.
            raise FetchError(f"{repository} returned commit {fetched[:12]}, not the pinned {commit[:12]}")
        tree = work / "tree"
        tree.mkdir()
        _write_tree(work, tree, commit)
        (tree / ".commit").write_text(commit + "\n")
        try:
            playbook.load(str(tree))
        except playbook.PlaybookError as error:
            raise FetchError(f"the pinned commit is not a valid playbook: {error}") from None
        try:
            os.rename(tree, target)
        except OSError as error:
            raise FetchError(f"{target}: cannot be created: {error.strerror or error}") from None
    except OSError as error:
        raise FetchError(f"{work}: {error.strerror or error}") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target
