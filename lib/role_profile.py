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
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import playbook
import playbook_skills

ROOT = Path(__file__).resolve().parents[1]
# Profiles, roles and model rules come from the pinned agent-playbook (lib/playbook.py).
PROVIDERS = playbook.profiles()
PARENT = "lead"
LEGACY = {"orchestrator": PARENT, "adjudicator": "arbiter"}  # 0.1.x role names and their playbook names
REQUIRED = tuple(playbook.role_keys())
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
STOCK_0_1_0 = "56398685df58d83b6a053c9213a0468afa56ecea6eb003b215731de95701dffb"  # claude-only.json
MISSING = object()  # A saved profile that does not exist, as opposed to one holding JSON null.
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


def in_family(provider, family, model):
    """Whether a model ID is a release of a family, for example gpt-6.2-sol or claude-opus-5-5."""
    date = re.search(r"-(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})$", model)
    release = (model[:date.start()] if date else model).casefold()
    family = re.escape(family.casefold())
    if provider == "codex":
        return re.fullmatch(r"gpt-[0-9]+(?:\.[0-9]+)*-" + family, release) is not None
    version = r"[0-9]+(?:[.-][0-9]+)*"
    return re.fullmatch(f"claude-(?:{family}-{version}|{version}-{family})", release) is not None


def json_kind(value):
    return {type(None): "null", list: "an array", str: "a string", bool: "a boolean"}.get(type(value), "a number")


def validate_profile(name, rows):
    check_profile_name(name)
    if not isinstance(rows, dict):
        raise ProfileError(f"expected a JSON object of roles, found {json_kind(rows)}")
    if not set(REQUIRED).issubset(rows):
        raise ProfileError("expected roles " + ", ".join(REQUIRED))
    for role, row in rows.items():
        if not isinstance(role, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", role) or not isinstance(row, dict):
            raise ProfileError(f"invalid role {role!r}")
        if row.get("provider") not in PROVIDERS[name]:
            raise ProfileError(f"invalid provider for {role}")
        for key in ("model", "effort"):
            if not isinstance(row.get(key), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*", row[key]):
                raise ProfileError(f"invalid {key} for {role}")
    # The playbook's model rules, for example reviewers and the arbiter on a model other than the executor's.
    for role, other, shared_family in playbook.rules(name):
        model, theirs = rows[role]["model"], rows[other]["model"]
        if shared_family and model == theirs and in_family(rows[role]["provider"], shared_family, model):
            continue  # A declared exception of the model set, such as codex-only reviewer2 on the executor's Sol.
        author = theirs.rsplit("/", 1)[-1].casefold()
        if model.rsplit("/", 1)[-1].casefold() == author:
            raise ProfileError(f"select a different model for {role}; {other} uses {author}")
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
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass  # Best effort, for example in a read-only directory.
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


def profile_present(target):
    """Return whether a saved profile exists; anything there that cannot be read as a file names itself."""
    try:
        mode = os.stat(target).st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProfileError(f"{target}: {error.strerror or error}") from None
    if not stat.S_ISREG(mode):
        kind = "a directory" if stat.S_ISDIR(mode) else "not a regular file"
        raise ProfileError(f"{target}: is {kind}; expected a JSON file of roles")
    return True


def parse_profile(target, original):
    """Decode a saved profile's bytes strictly as UTF-8 and parse the text; errors name the file."""
    try:
        return json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{target}: {exc}") from None


def migrated_rows(name, rows, original):
    """Return the fully migrated rows for a saved 0.1.x profile, or None when it needs no migration."""
    if not isinstance(rows, dict) or not LEGACY.keys() & rows.keys():
        return None
    for old, new in LEGACY.items():
        if old in rows and new in rows:
            raise ProfileError(f"both `{old}` and `{new}` roles are present; keep only `{new}`")
    if name == "claude-only.json" and hashlib.sha256(original).hexdigest() == STOCK_0_1_0:
        # The untouched 0.1.0 table: give the reviewers models distinct from the executor.
        rows["reviewer2"]["model"] = "claude-sonnet-5-5"
        rows["adjudicator"]["model"] = rows["reviewer1"]["model"]
    return {LEGACY.get(role, role): row for role, row in rows.items()}


def migrate_locked(target, real, unlocked):
    """Migrate the profile while holding its lock; returns its rows after migration and whether it changed."""
    try:
        original = real.read_bytes()
    except FileNotFoundError:
        return MISSING, False
    except OSError as error:  # For example, a file the container user may not read.
        raise ProfileError(f"{target}: cannot be read: {error.strerror or error}") from None
    rows = parse_profile(target, original)
    try:
        migrated = migrated_rows(target.name, rows, original)
    except ProfileError as exc:
        raise ProfileError(f"{target}: {exc}") from None
    if migrated is None:
        return rows, False
    try:  # Never back up or rewrite a file whose migrated rows would not load.
        validate_profile(target.name.removesuffix(".json"), migrated)
    except ProfileError as exc:
        raise ProfileError(f"{target}: needs migration to 0.2 but is invalid: {exc}") from None
    if unlocked:
        raise ProfileError(f"{target}: needs migration to 0.2 but cannot be locked: {unlocked}")
    data = (json.dumps(migrated, indent=2) + "\n").encode()
    temporary = None
    try:
        keep_backup(real, original)
        temporary = durable_temporary(real, data)
        os.replace(temporary, real)
    except OSError as error:
        if error.errno not in NO_LOCK:
            raise
        raise ProfileError(f"{target}: needs migration to 0.2 but cannot be written: {error.strerror}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return migrated, True


def migrate_profile(target):
    """Apply every 0.1.x migration to a saved profile in one atomic replacement, keeping a backup."""
    if not profile_present(target):
        return False  # Missing files are reported by the reader.
    with profile_lock(target) as (real, unlocked):
        return migrate_locked(target, real, unlocked)[1]


def saved_rows(target, missing=None):
    """Return a saved profile's rows after every 0.1.x migration, or `missing` when there is none."""
    if not profile_present(target):
        return missing
    with profile_lock(target) as (real, unlocked):
        rows = migrate_locked(target, real, unlocked)[0]
    return missing if rows is MISSING else rows


def save_profile(target, rows):
    with profile_lock(target) as (real, unlocked):
        data = (json.dumps(rows, indent=2) + "\n").encode()
        if real.exists() and real.read_bytes() == data:
            return
        if unlocked:
            raise ProfileError(f"{target}: cannot be saved without its lock: {unlocked}")
        temporary = durable_temporary(real, data)
        try:
            os.replace(temporary, real)
        finally:
            temporary.unlink(missing_ok=True)


def read_profile(name):
    check_profile_name(name)
    target = Path.home() / "profiles" / (name + ".json")
    if not profile_present(target):
        raise ProfileError("Select role models with t3-boat setup first")
    with profile_lock(target) as (real, unlocked):
        rows = migrate_locked(target, real, unlocked)[0]
    if rows is MISSING:
        raise ProfileError(f"{target}: the file disappeared while it was read")
    try:
        return validate_profile(name, rows)
    except ProfileError as exc:  # Anything else is a defect and keeps its traceback.
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
    if rows is None and profile_present(role_path):
        rows = read_profile(name)
    enabled = PROVIDERS[name]
    settings_path = Path(os.environ.get("T3CODE_HOME") or home / ".t3") / "userdata/settings.json"
    raw = settings_path.read_bytes() if settings_path.exists() else b"{}"
    try:
        settings = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
    targets = [
        ("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "AGENTS.md"),
        ("claudeAgent", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "CLAUDE.md"),
    ]
    # The owner's setup choice: agent-playbook role guides as skills for every enabled provider.
    skill_files, stale_skills, kept = playbook_skills.plan(home, {p: base for p, base, _ in targets}, enabled)
    updates = dict(skill_files)
    installed = set(skill_files) | set(kept)
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
            roster = playbook_skills.roster(base, installed)
            updates[skill_path] = policy.rstrip() + "\n\n" + "\n".join(table) + "\n" + ("\n" + roster if roster else "")
            updates[path] = instructions(path, note + playbook_skills.lead_note(base, installed))
        elif path.exists():
            updates[path] = instructions(path, "")
    updates[settings_path] = json.dumps(settings, indent=2) + "\n"
    # A provider's child shell may omit custom environment variables.
    updates[home / "profiles/active"] = name + "\n"
    for path, data in updates.items():
        write(path, data)
    playbook_skills.remove(stale_skills)
    for path in kept:
        print(f"T3 Boat: kept your own {path}; the agent-playbook guide is not installed there.", file=sys.stderr)
    if save_selection:
        save_profile(role_path, rows)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: role_profile.py PROFILE")
    try:
        prepare(sys.argv[1])
    except ProfileError as exc:  # Anything else is a defect and keeps its traceback.
        sys.exit(f"T3 Boat profile: {exc}")
