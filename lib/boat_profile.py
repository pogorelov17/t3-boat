#!/usr/bin/env python3
"""Apply one Docker installation's saved choices before starting T3.

A profile file, `profiles/PROFILE.json`, holds everything setup chose for that T3_BOAT_PROFILE:

    {"lead": {"provider": ..., "model": ..., "effort": ...},
     "playbook": null | {"path": "/absolute/package/dir", "model_set": null | "NAME", "lead_role": null | "ROLE"}}

`lead` is the model of the T3 chats (the team lead) and the model every Boat worker opens with. `playbook` is
the optional external package attached in setup: its directory, the model set chosen for this profile, and the
package role whose guide the T3 team lead follows. Setup writes the file in one atomic step, and every generated
file (T3 settings, instructions, skills) is derived from it, so a container start can always regenerate them.
"""
import fcntl
import json
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
# The provider subscriptions of each profile, as T3 provider instances. These are T3 Boat's own choices; an
# attached package's model sets do not define them.
PROVIDERS = {"codex-only": ("codex",), "claude-only": ("claudeAgent",), "codex+claude": ("codex", "claudeAgent")}
PARENT = "lead"
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
MISSING = object()  # A saved profile that does not exist, as opposed to one holding JSON null.
BEGIN = "<!-- BEGIN t3-boat -->"
END = "<!-- END t3-boat -->"
VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*")


class ProfileError(ValueError):
    """A problem with a file the user can repair; reported as one line."""


def write(target, content):
    data = content.encode()
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


def profile_path(name, home=None):
    return Path(home or Path.home()) / "profiles" / (name + ".json")


def json_kind(value):
    return {type(None): "null", list: "an array", str: "a string", bool: "a boolean"}.get(type(value), "a number")


def check_row(name, row):
    if not isinstance(row, dict) or set(row) != {"provider", "model", "effort"}:
        raise ProfileError("lead must be an object with provider, model and effort")
    if row["provider"] not in PROVIDERS[name]:
        raise ProfileError(f"lead provider must be one of {', '.join(PROVIDERS[name])} for {name}")
    for key in ("model", "effort"):
        if not isinstance(row[key], str) or not VALUE.fullmatch(row[key]):
            raise ProfileError(f"invalid lead {key}")
    return row


def check_attachment(name, attachment):
    """Load the attachment of a profile that passed check_state; return (package, model set, lead role).

    Without an attachment all three are None. A package that is missing or malformed, or no longer has the chosen
    model set or role, raises playbook.PlaybookError, naming the package file.
    """
    if attachment is None:
        return None, None, None
    package = playbook.load(attachment["path"])
    lead_role = attachment["lead_role"]
    if lead_role is not None and lead_role not in package.roles:
        raise playbook.PlaybookError(f"{package.manifest_path}: no role {lead_role!r} for the team lead")
    chosen = attachment["model_set"]
    if chosen is not None:
        if chosen not in package.model_set_paths:
            raise playbook.PlaybookError(f"{package.manifest_path}: no model set {chosen!r}")
        if chosen not in package.fitting_sets(PROVIDERS[name]):
            raise playbook.PlaybookError(f"{package.manifest_path}: model set {chosen!r} uses a provider that "
                                         f"{name} does not enable")
    return package, chosen, lead_role


def check_state(name, state):
    """Check the shape of a saved profile; the attached package itself is read by check_attachment."""
    check_profile_name(name)
    if not isinstance(state, dict):
        raise ProfileError(f"expected a JSON object, found {json_kind(state)}")
    unknown = sorted(set(state) - {PARENT, "playbook"})
    if unknown or PARENT not in state:
        raise ProfileError("expected the keys lead and playbook" + (f"; unknown {', '.join(unknown)}" if unknown else ""))
    check_row(name, state[PARENT])
    attachment = state.get("playbook")
    if attachment is not None and (not isinstance(attachment, dict) or set(attachment) != {"path", "model_set", "lead_role"}
                                   or not isinstance(attachment["path"], str)
                                   or not all(isinstance(attachment[k], (str, type(None)))
                                              for k in ("model_set", "lead_role"))):
        raise ProfileError('playbook must be null or {"path": DIRECTORY, "model_set": NAME or null, '
                           '"lead_role": ROLE or null}')
    return state


def saved(target, missing=None):
    """Return a saved profile's parsed JSON, or `missing` when there is none; errors name the file."""
    try:
        mode = os.stat(target).st_mode
    except FileNotFoundError:
        return missing
    except OSError as error:
        raise ProfileError(f"{target}: {error.strerror or error}") from None
    if not stat.S_ISREG(mode):
        kind = "a directory" if stat.S_ISDIR(mode) else "not a regular file"
        raise ProfileError(f"{target}: is {kind}; expected a JSON profile")
    try:
        original = target.read_bytes()
    except OSError as error:  # For example, a file the container user may not read.
        raise ProfileError(f"{target}: cannot be read: {error.strerror or error}") from None
    try:
        return json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{target}: {exc}") from None


def read_state(name, missing=MISSING):
    """The saved profile, checked; `missing` when setup has not saved one. Errors name the file."""
    check_profile_name(name)
    target = profile_path(name)
    state = saved(target, missing=MISSING)
    if state is MISSING:
        return missing
    try:
        return check_state(name, state)
    except ProfileError as exc:
        raise ProfileError(f"{target}: {exc}") from None


def read_profile(name):
    """The saved profile with its attached package loaded: (state, package, model set, lead role)."""
    state = read_state(name)
    if state is MISSING:
        raise ProfileError("Select a model with t3-boat setup first")
    return (state, *check_attachment(name, state.get("playbook")))


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


@contextmanager
def state_lock(home=None):
    """Serialize every change to the saved state and the files generated from it."""
    with (Path(home or Path.home()) / ".t3-boat-state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def targets(home):
    return [
        ("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "AGENTS.md"),
        ("claudeAgent", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "CLAUDE.md"),
    ]


def settings_path(home):
    return Path(os.environ.get("T3CODE_HOME") or home / ".t3") / "userdata/settings.json"


def plan(name, state=None, warn=None):
    """Compute every file generated for profile `name` from `state` (by default, the saved one).

    Returns (writes, removals, notices). Nothing is written. A saved package that cannot be read is reported
    through `warn` and leaves the profile unapplied, so T3 still starts and setup can attach it again.
    """
    check_profile_name(name)
    home = Path.home()
    notices = []
    package = model_set = lead_role = None
    if state is None:
        state = read_state(name, missing=None)
    if state is not None:
        check_state(name, state)
        try:
            package, model_set, lead_role = check_attachment(name, state.get("playbook"))
        except playbook.PlaybookError as error:
            if warn is None:
                raise
            warn(f"{error}. T3 starts without the {name} profile until the playbook is restored or setup "
                 "attaches another one")
            state = None
    enabled = PROVIDERS[name]
    path = settings_path(home)
    raw = path.read_bytes() if path.exists() else b"{}"
    try:
        settings = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{path}: {exc}") from None
    if not isinstance(settings, dict):
        raise ProfileError(f"{path}: expected a JSON object")
    instances = settings.setdefault("providerInstances", {})
    if not isinstance(instances, dict) or not all(isinstance(instances.get(p, {}), dict) for p in OPTION):
        raise ProfileError(f"{path}: providerInstances must be an object of objects")
    if state is None:
        settings.pop("defaultModelSelection", None)
    else:
        lead = state[PARENT]
        settings["defaultModelSelection"] = {
            "instanceId": lead["provider"], "model": lead["model"],
            "options": [{"id": OPTION[lead["provider"]], "value": lead["effort"]}],
        }
    settings["linkedEnvironmentsEnabled"] = True
    for provider in OPTION:
        instances.setdefault(provider, {}).update(driver=provider, enabled=provider in enabled)

    skill_files, removals, kept, blocked = playbook_skills.plan(
        {p: base for p, base, _ in targets(home)}, enabled, package, lead_role)
    writes = dict(skill_files)
    installed = set(skill_files) | set(kept)  # Only guide files that will exist; a blocked path is never named.
    base_skill = (ROOT / "skills/t3-boat/SKILL.md").read_text().rstrip()
    generic = (ROOT / "skills/t3-boat/generic.md").read_text().rstrip()
    for provider, base, filename in targets(home):
        target = base / filename
        if state is not None and provider in enabled:
            override = base / "AGENTS.override.md"
            if provider == "codex" and override.is_file() and override.stat().st_size:
                raise ProfileError(f"{override} masks the required global profile")
            skill_path = base / "skills/t3-boat/SKILL.md"
            lead = state[PARENT]
            sections = [base_skill,
                        playbook_skills.section(base, package, model_set, lead_role, installed).rstrip() if package else generic,
                        "\n".join(["## Lead model", "",
                                   "The model of this chat and the model every Boat worker opens with:", "",
                                   "| Row | Provider | Model | Option |", "| --- | --- | --- | --- |",
                                   f"| lead | {lead['provider']} | {lead['model']} | "
                                   f"{OPTION[lead['provider']]}={lead['effort']} |"])]
            writes[skill_path] = "\n\n".join(sections) + "\n"
            note = (
                f"The active T3 Boat profile is `{name}`. You are the T3 team lead. "
                "Respond to the user's opening message in their language. If they ask where to begin, suggest a "
                "starting point based on their interests. Discuss ideas and plan new projects in this chat without "
                "starting a VM; targeted web fact checks are fine here. Before any project source inspection, edits, "
                "project commands, tests, installs, clones or extended research, load and follow "
                f"{skill_path} automatically. Keep source on Boat. Post compact, factual updates in the parent. "
                "Never change models silently."
            )
            writes[target] = instructions(target, note + playbook_skills.lead_note(base, package, lead_role, installed))
        elif target.exists():
            writes[target] = instructions(target, "")
    writes[path] = json.dumps(settings, indent=2) + "\n"
    # A provider's child shell may omit custom environment variables.
    writes[home / "profiles/active"] = name + "\n"
    for found in kept:
        notices.append(f"kept your own {found}; it has no t3-boat marker, so it is used in place of the "
                       "playbook's guide")
    for found, obstacle in blocked:
        notices.append(f"{obstacle} is not a directory or a file t3-boat can replace; the playbook guide "
                       f"{found} is not installed")
    return writes, removals, notices


def applied(name):
    """Whether every generated file matches the saved profile (for t3-boat status)."""
    writes, removals, _ = plan(name)
    return not removals and all(p.is_file() and p.read_bytes() == data.encode() for p, data in writes.items())


def prepare(name, state=None):
    """Write the saved choices (when setup passes `state`) and regenerate every file derived from them.

    The profile file is written first, in one atomic step: it is the whole choice. The generated files follow;
    if that is interrupted, the next container start or setup regenerates them, and t3-boat status reports the
    profile as not applied until then.
    """
    check_profile_name(name)
    home = Path.home()
    with state_lock(home):
        if state is not None:
            check_state(name, state)
            check_attachment(name, state.get("playbook"))
        writes, removals, notices = plan(name, state, warn=None if state else
                                         lambda message: print(f"T3 Boat: {message}.", file=sys.stderr))
        if state is not None:
            write(profile_path(name, home), json.dumps(state, indent=2) + "\n")
        for path, data in writes.items():
            write(path, data)
        playbook_skills.remove(removals)
    for notice in notices:
        print(f"T3 Boat: {notice}.", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: boat_profile.py PROFILE")
    try:
        prepare(sys.argv[1])
    except (ProfileError, playbook.PlaybookError) as exc:  # Anything else is a defect and keeps its traceback.
        sys.exit(f"T3 Boat profile: {exc}")
