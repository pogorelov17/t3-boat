#!/usr/bin/env python3
"""Apply one Docker installation's editable role table before starting T3."""
import ctypes
import errno
import fcntl
import glob
import json
import hashlib
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "codex-only": ("codex",),
    "claude-only": ("claudeAgent",),
    "codex+claude": ("codex", "claudeAgent"),
}
PARENT = "lead"
LEGACY_PARENT = "orchestrator"  # 0.1.x name of the parent role
REQUIRED = (PARENT, "executor", "reviewer1", "reviewer2", "scout", "researcher", "adjudicator")
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
CODEX_SOL = re.compile(r"gpt-[0-9]+(?:\.[0-9]+)*-sol(?:-(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}))?")
STOCK_0_1_0 = "56398685df58d83b6a053c9213a0468afa56ecea6eb003b215731de95701dffb"  # claude-only.json
BEGIN = "<!-- BEGIN t3-boat -->"
END = "<!-- END t3-boat -->"


def write(target, content):
    data = content if isinstance(content, bytes) else content.encode()
    if target.exists() and target.read_bytes() == data:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(data)
    try:
        os.replace(temporary.name, target)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def check_profile_name(name):
    if name not in PROVIDERS:
        raise ProfileError("T3_BOAT_PROFILE must be one of: " + ", ".join(PROVIDERS))


def validate_profile(name, rows):
    check_profile_name(name)
    if not isinstance(rows, dict) or not set(REQUIRED).issubset(rows):
        raise ProfileError("expected roles " + ", ".join(REQUIRED))
    for role, row in rows.items():
        if not isinstance(role, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", role) or not isinstance(row, dict):
            raise ProfileError(f"invalid role {role!r}")
        if row.get("provider") not in PROVIDERS[name]:
            raise ProfileError(f"invalid provider for {role}")
        for key in ("model", "effort"):
            if not isinstance(row.get(key), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*", row[key]):
                raise ProfileError(f"invalid {key} for {role}")
    executor_model = rows["executor"]["model"]
    author = executor_model.rsplit("/", 1)[-1].casefold()
    for role in ("reviewer1", "reviewer2", "adjudicator"):
        model = rows[role]["model"]
        if (name == "codex-only" and role == "reviewer2" and model == executor_model
                and CODEX_SOL.fullmatch(model)):
            continue
        if model.rsplit("/", 1)[-1].casefold() == author:
            raise ProfileError(f"select a different model for {role}; {author} writes the code")
    return rows


class ProfileError(ValueError):
    """A problem with a file the user can repair; reported as one line."""


TEMPORARY = ".t3-boat-tmp-"  # Marks temporary files beside a profile, so stale ones can be removed.
# Errors meaning the environment cannot provide a lock, as opposed to a lock held by another process.
NO_LOCK = {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS}


def durable_temporary(real, data):
    with NamedTemporaryFile(dir=real.parent, prefix="." + real.name + TEMPORARY, delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
    return Path(temporary.name)


@contextmanager
def profile_lock(target):
    """Serialise reads that migrate and saves of one profile.

    Yields the file behind any symlink and the reason no lock could be taken, or None when it is held.
    """
    real = Path(os.path.realpath(target))
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        lock = (real.parent / ("." + real.name + ".lock")).open("a")
    except OSError as error:
        if error.errno not in NO_LOCK:
            raise
        yield real, error.strerror
        return
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError as error:
            if error.errno not in NO_LOCK:
                raise
            yield real, error.strerror
            return
        for stale in real.parent.glob(glob.escape("." + real.name + TEMPORARY) + "*"):
            stale.unlink(missing_ok=True)
        yield real, None


def no_replace(source, destination):
    """Rename source to destination, failing with FileExistsError if destination exists."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    at_cwd, no_replace_flag = -100, 1  # AT_FDCWD, RENAME_NOREPLACE
    if renameat2(at_cwd, os.fsencode(source), at_cwd, os.fsencode(destination), no_replace_flag):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def keep_backup(real, data):
    """Create the backup only if none exists; an existing non-empty backup is never replaced."""
    backup = real.with_name(real.name + ".pre-0.2")
    try:
        if backup.stat().st_size == 0:
            backup.unlink()  # Left by an interrupted run; it holds none of the original bytes.
    except FileNotFoundError:
        pass
    temporary = durable_temporary(real, data)
    try:
        os.link(temporary, backup)
    except FileExistsError:
        pass
    except OSError:  # A file system without hard links: a rename that never replaces.
        try:
            no_replace(temporary, backup)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def migrated_rows(name, original):
    """Return the fully migrated rows for a saved 0.1.x profile, or None when it needs no migration."""
    try:
        rows = json.loads(original)
    except ValueError:
        return None  # Unreadable files are reported by the reader.
    if not isinstance(rows, dict) or LEGACY_PARENT not in rows:
        return None
    if PARENT in rows:
        raise ProfileError(f"both `{LEGACY_PARENT}` and `{PARENT}` roles are present; keep only `{PARENT}`")
    if name == "claude-only.json" and hashlib.sha256(original).hexdigest() == STOCK_0_1_0:
        # The untouched 0.1.0 table: give the reviewers models distinct from the executor.
        rows["reviewer2"]["model"] = "claude-sonnet-5-5"
        rows["adjudicator"]["model"] = rows["reviewer1"]["model"]
    return {(PARENT if role == LEGACY_PARENT else role): row for role, row in rows.items()}


def migrate_locked(target, real, unlocked):
    """Migrate the profile while holding its lock; returns its bytes after migration and whether it changed."""
    try:
        original = real.read_bytes()
    except FileNotFoundError:
        return None, False
    try:
        rows = migrated_rows(target.name, original)
    except ProfileError as exc:
        raise ProfileError(f"{target}: {exc}") from None
    if rows is None:
        return original, False
    if unlocked:
        raise ProfileError(f"{target}: needs migration to 0.2 but cannot be locked: {unlocked}")
    data = (json.dumps(rows, indent=2) + "\n").encode()
    try:
        keep_backup(real, original)
        temporary = durable_temporary(real, data)
    except OSError as error:
        if error.errno not in NO_LOCK:
            raise
        raise ProfileError(f"{target}: needs migration to 0.2 but cannot be written: {error.strerror}") from None
    try:
        os.replace(temporary, real)
    finally:
        temporary.unlink(missing_ok=True)
    return data, True


def migrate_profile(target):
    """Apply every 0.1.x migration to a saved profile in one atomic replacement, keeping a backup."""
    if not target.is_file():
        return False  # Missing files are reported by the reader.
    with profile_lock(target) as (real, unlocked):
        return migrate_locked(target, real, unlocked)[1]


def save_profile(target, rows):
    with profile_lock(target) as (real, unlocked):
        data = (json.dumps(rows, indent=2) + "\n").encode()
        if real.exists() and real.read_bytes() == data:
            return
        temporary = durable_temporary(real, data)
        try:
            os.replace(temporary, real)
        finally:
            temporary.unlink(missing_ok=True)


def read_profile(name):
    check_profile_name(name)
    target = Path.home() / "profiles" / (name + ".json")
    if not target.is_file():
        raise ProfileError("Select role models with t3-boat setup first")
    with profile_lock(target) as (real, unlocked):
        data = migrate_locked(target, real, unlocked)[0]
    try:
        if data is None:
            raise ProfileError("the file disappeared while it was read")
        return validate_profile(name, json.loads(data.decode()))
    except ValueError as exc:
        raise ProfileError(f"{target}: {exc}") from exc


def instructions(path, content):
    previous = path.read_text() if path.exists() else ""
    if previous.count(BEGIN) != previous.count(END) or previous.count(BEGIN) > 1:
        raise ProfileError(f"Repair the t3-boat marker block in {path}")
    block = f"{BEGIN}\n{content}\n{END}" if content else ""
    if BEGIN in previous:
        start, rest = previous.split(BEGIN, 1)
        _, end = rest.split(END, 1)
        return start + block + end
    if not block:
        return previous
    return previous + ("\n\n" if previous else "") + block + "\n"


def prepare(name, rows=None):
    check_profile_name(name)
    home = Path.home()
    role_path = home / "profiles" / (name + ".json")
    save_selection = rows is not None
    if rows is None and role_path.is_file():
        rows = read_profile(name)
    enabled = PROVIDERS[name]
    settings_path = Path(os.environ.get("T3CODE_HOME") or home / ".t3") / "userdata/settings.json"
    try:
        settings = json.loads(settings_path.read_bytes().decode()) if settings_path.exists() else {}
    except ValueError as exc:
        raise ProfileError(f"{settings_path}: {exc}") from None
    if not isinstance(settings, dict):
        raise ProfileError(f"{settings_path}: expected a JSON object")
    instances = settings.setdefault("providerInstances", {})
    if not isinstance(instances, dict) or not all(isinstance(instances.get(p, {}), dict) for p in OPTION):
        raise ProfileError(f"{settings_path}: providerInstances must be an object of objects")
    if rows is None:
        settings.pop("defaultModelSelection", None)
    else:
        validate_profile(name, rows)
        parent = rows[PARENT]
        settings["defaultModelSelection"] = {
            "instanceId": parent["provider"], "model": parent["model"],
            "options": [{"id": OPTION[parent["provider"]], "value": parent["effort"]}],
        }
    settings["linkedEnvironmentsEnabled"] = True
    for provider in OPTION:
        instances.setdefault(provider, {}).update(driver=provider, enabled=provider in enabled)

    policy = (ROOT / "skills/t3-boat/SKILL.md").read_text()
    table = ["## Active roles", "", "| Role | Provider | Model | Option |", "| --- | --- | --- | --- |"]
    for role, row in (rows or {}).items():
        table.append(f"| {role} | {row['provider']} | {row['model']} | {OPTION[row['provider']]}={row['effort']} |")
    skill = policy.rstrip() + "\n\n" + "\n".join(table) + "\n"
    targets = [
        ("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "AGENTS.md"),
        ("claudeAgent", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "CLAUDE.md"),
    ]
    updates = {}
    for provider, base, filename in targets:
        path = base / filename
        if rows is not None and provider in enabled:
            override = base / "AGENTS.override.md"
            if provider == "codex" and override.is_file() and override.stat().st_size:
                raise ProfileError(f"{override} masks the required global profile")
            skill_path = base / "skills/t3-boat/SKILL.md"
            note = (
                f"The active T3 Boat profile is `{name}`. You are the team lead, unless your task assigns you another role. "
                f"Respond to the user's opening message in their language. If they ask where to begin, suggest a starting point based on their interests. "
                f"Discuss ideas and plan new projects in this chat without starting a VM. Before any project inspection, tests, research or code changes, "
                f"load and follow {skill_path} automatically. Every project uses this workflow. "
                "Keep source on Boat. Report compact milestones in the parent. Never change models silently."
            )
            updates[skill_path] = skill
            updates[path] = instructions(path, note)
        elif path.exists():
            updates[path] = instructions(path, "")
    updates[settings_path] = json.dumps(settings, indent=2) + "\n"
    # A provider's child shell may omit custom environment variables.
    updates[home / "profiles/active"] = name + "\n"
    for path, data in updates.items():
        write(path, data)
    if save_selection:
        save_profile(role_path, rows)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: role_profile.py PROFILE")
    try:
        prepare(sys.argv[1])
    except ProfileError as exc:  # Anything else is a defect and keeps its traceback.
        sys.exit(f"T3 Boat profile: {exc}")
