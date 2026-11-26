"""Download the suggested external playbook at one pinned commit, only when the owner selects it in setup.

Nothing else downloads it: the default image, its build and a container start never fetch a playbook. The download
is data only. Git fetches the pinned commit, Python writes each file of its tree as a plain file, the result is
compared with the pinned file table (playbook_pin), and playbook.load checks it like any other package. No code
from the package runs on this server, and no hook, filter, link or submodule from the repository is followed. The
release lands in its own folder under /data/teams, named after the commit, so a later setup or container start
reads it from the volume without the network. Reusing that folder checks every file against the pinned table
first, offline; a folder that differs is refused and left as it is, since the owner may have edited it on purpose.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import playbook
import playbook_pin

URL = playbook_pin.URL
REPOSITORY = URL + ".git"
MAX_TREE = 64 << 20  # Bytes of the whole downloaded tree; the package's own limits are checked when it loads.
TIMEOUT = 300


class FetchError(Exception):
    """The suggested playbook could not be downloaded or is not a valid package; the message says why."""


class Mismatch(FetchError):
    """The download folder exists but does not hold the pinned release. It is left as it is; nothing is fetched."""


def destination(teams, pin=None):
    return Path(teams) / f"agent-playbook-{(pin or playbook_pin.PIN).commit[:12]}"


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


def fetch(teams, repository=None, pin=None):
    """Return the folder holding the pinned release under `teams`, downloading it first if it is not there.

    An existing folder of that name is used only if it holds exactly the pinned files (playbook_pin.check), without
    the network; otherwise Mismatch names the differences and the folder is never changed or replaced: it may be
    the owner's. Raises FetchError naming the reason; nothing is left behind by a failed download.
    """
    repository = repository or REPOSITORY
    pin = pin or playbook_pin.PIN
    commit = pin.commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise FetchError(f"{commit!r} is not a full commit ID")
    teams = Path(teams)
    target = destination(teams, pin)
    if target.exists() or target.is_symlink():
        differences, _ = playbook_pin.check(target, pin)
        if differences:
            raise Mismatch(f"{target} does not hold the pinned commit {commit[:12]}: "
                           + playbook_pin.summary(differences))
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
        tree_id = _git(work, "rev-parse", commit + "^{tree}").decode().strip()
        if tree_id != pin.tree:
            raise FetchError(f"commit {commit[:12]} holds tree {tree_id[:12]}, not the pinned {pin.tree[:12]}")
        tree = work / "tree"
        tree.mkdir()
        _write_tree(work, tree, commit)
        (tree / ".commit").write_text(commit + "\n")
        if differences := playbook_pin.check(tree, pin)[0]:
            raise FetchError(f"the download differs from the pinned file table: {playbook_pin.summary(differences)}")
        try:
            playbook.load(str(tree), pin)
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


def load(teams, repository=None, pin=None):
    """The pinned release as a loaded package: downloaded if needed, and verified in the very read that is applied.
    Raises Mismatch if the folder differs from the pin, also when it changes after fetch checked it."""
    pin = pin or playbook_pin.PIN
    target = fetch(teams, repository, pin)
    try:
        package = playbook.load(str(target), pin)
    except playbook.PlaybookError as error:
        raise Mismatch(f"{target} does not hold the pinned commit {pin.commit[:12]}: {error}") from None
    if package.pin != pin.commit:
        raise Mismatch(f"{target} does not hold the pinned commit {pin.commit[:12]}: "
                       + playbook_pin.summary(package.differences or [".commit does not name it"]))
    return package
