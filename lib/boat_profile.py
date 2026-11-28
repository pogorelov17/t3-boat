#!/usr/bin/env python3
"""Apply one Docker installation's saved choices before starting T3.

A profile file, `profiles/PROFILE.json`, holds everything setup chose for that provider profile:

    {"lead": {"provider": ..., "model": ..., "effort": ...},
     "playbook": null | {"path": "/absolute/package/dir", "model_set": null | "NAME", "lead_role": null | "ROLE",
                          "role_overrides": {"ROLE": {"provider": ..., "family" | "model": ..., "effort": ...}}}}

`lead` is the model of the T3 chats (the team lead) and the model every Boat worker opens with. `playbook` is
the optional external package attached in setup: its directory, the model set chosen for this profile, and the
package role whose guide the T3 team lead follows, plus optional owner-approved model overrides for future role
workers. Setup writes the file in one atomic step, and every generated file (T3 settings, instructions, skills) is
derived from it, so a container start can always regenerate them.

The package is read once per run, in full, and everything is generated from that one read. The last file written,
`profiles/PROFILE.applied.json`, records the saved choice it was generated from and each role's worker guide, so
`boat-worker` starts workers from exactly what the team lead's skills describe, even after the owner edits the
package directory. A profile that cannot be applied, because setup has not run or the package cannot be read,
leaves T3 starting with a short note in place of the instructions, and no Boat skill, guides or applied record.
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
import role_models

ROOT = Path(__file__).resolve().parents[1]
# The provider subscriptions of each profile, as T3 provider instances. These are T3 Boat's own choices; an
# attached package's model sets do not define them.
PROVIDERS = {"codex-only": ("codex",), "claude-only": ("claudeAgent",), "codex+claude": ("codex", "claudeAgent")}
PARENT = "lead"
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
MISSING = object()  # A saved profile that does not exist, as opposed to one holding JSON null.
BEGIN = "<!-- BEGIN t3-boat -->"
END = "<!-- END t3-boat -->"
# The first line after the front matter of the generated Boat skill. Only a regular file that starts with the Boat
# skill's front matter followed by this line is generated (see generated_skill); t3-boat never replaces or removes
# anything else at the skill's path.
SKILL_MARK = "<!-- t3-boat: generated from the T3 Boat profile. t3-boat replaces and removes this file. -->"
SKILL_HEADER = re.compile(r"---\nname: t3-boat\ndescription: [^\n]*\n---\n\n" + re.escape(SKILL_MARK) + r"\n")
VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*")


class ProfileError(ValueError):
    """A problem with a file the user can repair; reported as one line."""


def write(target, content):
    """Replace `target` with `content` in one step; a generated file that cannot be read is simply replaced."""
    data = content.encode()
    try:
        if playbook.read_regular(target) == data:
            return
    except OSError:
        pass
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(data)
        try:
            os.replace(temporary.name, target)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
    except OSError as error:
        raise ProfileError(f"{target}: cannot be written: {error.strerror or error}") from None


def read_owned(path, default=""):
    """The text of a file the owner may also edit, or `default` when there is none; errors name the file."""
    try:
        return playbook.read_regular(path).decode("utf-8")
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileError(f"{path}: cannot be read: {getattr(error, 'strerror', None) or error}") from None


def check_profile_name(name):
    if name not in PROVIDERS:
        raise ProfileError("profile must be one of: " + ", ".join(PROVIDERS))


def profile_path(name, home=None):
    return Path(home or Path.home()) / "profiles" / (name + ".json")


def selection_path(home=None):
    return Path(home or Path.home()) / "profiles/selection.json"


def read_selection(missing=MISSING, home=None):
    """The provider profile explicitly selected in setup, or `missing` before that choice."""
    target = selection_path(home)
    selection = saved(target, missing=MISSING)
    if selection is MISSING:
        return missing
    if not isinstance(selection, dict) or set(selection) != {"profile"}:
        raise ProfileError(f"{target}: expected an object with only the profile key")
    try:
        check_profile_name(selection["profile"])
    except ProfileError as error:
        raise ProfileError(f"{target}: {error}") from None
    return selection["profile"]


def select_profile(name, home=None):
    """Persist the owner's explicit setup choice atomically."""
    check_profile_name(name)
    home = Path(home or Path.home())
    with state_lock(home):
        write(selection_path(home), json.dumps({"profile": name}, indent=2) + "\n")


def runtime_profile(home=None):
    """The confirmed selection, or the generated active pointer used by provider child processes."""
    home = Path(home or Path.home())
    selected = read_selection(missing=None, home=home)
    if selected is not None:
        return selected
    active = home / "profiles/active"
    try:
        name = playbook.read_regular(active).decode("utf-8").strip()
    except FileNotFoundError:
        raise ProfileError("Select provider subscriptions with t3-boat setup first") from None
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileError(f"{active}: cannot be read: {getattr(error, 'strerror', None) or error}") from None
    try:
        check_profile_name(name)
    except ProfileError as error:
        raise ProfileError(f"{active}: {error}") from None
    return name


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


def applied_path(name, home=None):
    return Path(home or Path.home()) / "profiles" / (name + ".applied.json")


def check_attachment(name, attachment, package=None):
    """Load the attachment of a profile that passed check_state; return (package, model set, lead role).

    Without an attachment all three are None. A package that is missing or malformed, or no longer has the chosen
    model set or role, raises playbook.PlaybookError, naming the package file. `package` is the copy of the
    attachment's directory that setup already read and showed the owner: the choice is checked against it instead
    of reading the directory again, which may by now name another release (a link the owner switched).
    """
    if attachment is None:
        return None, None, None
    if package is None:
        package = playbook.load(attachment["path"])
    elif str(package.root) != attachment["path"]:
        raise ValueError(f"the loaded package {package.root} is not the attached {attachment['path']}")
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
    try:
        role_models.check_overrides(package, attachment.get("role_overrides", {}), PROVIDERS[name], lead_role)
    except ValueError as error:
        raise playbook.PlaybookError(f"{package.manifest_path}: {error}") from None
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
    required = {"path", "model_set", "lead_role"}
    if attachment is not None and (not isinstance(attachment, dict)
                                   or not required <= set(attachment)
                                   or set(attachment) - required - {"role_overrides"}
                                   or not isinstance(attachment["path"], str)
                                   or not all(isinstance(attachment[k], (str, type(None)))
                                              for k in ("model_set", "lead_role"))
                                   or not isinstance(attachment.get("role_overrides", {}), dict)):
        raise ProfileError('playbook must be null or {"path": DIRECTORY, "model_set": NAME or null, '
                           '"lead_role": ROLE or null, optional "role_overrides": object}')
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
        original = playbook.read_regular(target)  # Checked again on the open file: a FIFO never blocks here.
    except playbook.NotRegularFile:
        raise ProfileError(f"{target}: is not a regular file; expected a JSON profile") from None
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


def read_applied(name):
    """The saved profile and what the last complete run generated from it, without reading the package again.

    Returns (state, playbook), where playbook is None or {"name", "label", "roles": {role: worker guide}}. Call
    it under state_lock. A profile whose generated files are missing or from another choice is refused.
    """
    state = read_state(name)
    if state is MISSING:
        raise ProfileError("Select a model with t3-boat setup first")
    record = saved(applied_path(name), missing=None)
    if not isinstance(record, dict) or record.get("profile") != state or "playbook" not in record:
        raise ProfileError(f"the {name} profile is not applied, so no worker can start. The container log and "
                           "t3-boat status name the reason; fix it, then restart the container or rerun t3-boat setup")
    return state, record["playbook"]


def instructions(path, content):
    previous = read_owned(path)
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


def boat_skill_path(base):
    return base / "skills/t3-boat/SKILL.md"


def generated_skill(path):
    """Whether `path` holds a Boat skill t3-boat generated: a regular file, reached through no link at its own
    folder or name, that starts with the generated header. A file that only quotes the marker belongs to the owner."""
    try:
        if path.parent.is_symlink() or path.is_symlink():
            return False
        return SKILL_HEADER.match(playbook.read_regular(path).decode("utf-8", errors="replace")) is not None
    except OSError:
        return False


def skill_occupant(base, path):
    """What keeps t3-boat from writing the Boat skill at `path`, or None: a link, a non-directory where a folder
    goes, anything that is not a readable regular file, or a file the owner wrote."""
    found = playbook_skills.obstacle(base, path)
    if found is None and (path.exists() or path.is_symlink()) and not generated_skill(path):
        found = path
    return found


def reconcile(writes, removals):
    """Plan each directory entry once (playbook_skills.entry). Providers whose configuration folders are aliased
    (`~/.claude/skills` linked to `~/.codex/skills`, or both bind mounts of one host folder) reach one entry through
    two paths: the first provider that writes it decides its content, and nothing that any provider writes is
    removed through another provider's path."""
    unique, written = {}, set()
    for path, data in writes.items():
        if (key := playbook_skills.entry(path)) not in written:
            written.add(key)
            unique[path] = data
    stale, seen = [], set(written)
    for path in removals:
        if (key := playbook_skills.entry(path)) not in seen:
            seen.add(key)
            stale.append(path)
    return unique, stale


def disabled_note(name, reason):
    """The instructions of a profile that cannot be applied: no workflow, only what to tell the owner."""
    return (f"T3 Boat is not ready: the `{name}` profile cannot be applied because {reason}. No T3 Boat workflow, "
            "skill or playbook is active. Do not inspect, edit, build, test, install or run any project on this "
            "server, and do not start Boat workers. Tell the owner this note, and that "
            "`docker exec -it t3-boat t3-boat setup` (or restoring the named file and restarting the container) "
            "fixes it. Planning conversation is fine.")


def plan(name=None, state=None, warn=None, loaded=None):
    """Compute every file generated for profile `name` from `state` (by default, the saved one).

    Returns (writes, removals, notices). Nothing is written. `loaded` is the (package, model set, lead role) that
    check_attachment already returned for `state`, so the package is read only once. A saved package that cannot be
    read is reported through `warn` and leaves the profile unapplied, so T3 still starts and setup can attach it
    again.
    """
    if name is not None:
        check_profile_name(name)
    home = Path.home()
    notices = []
    package = model_set = lead_role = None
    overrides = {}
    reason = "setup has not saved it yet"
    if state is None and name is not None:
        state = read_state(name, missing=None)
    if state is not None:
        check_state(name, state)
        try:
            package, model_set, lead_role = loaded or check_attachment(name, state.get("playbook"))
            overrides = state.get("playbook", {}).get("role_overrides", {}) if package else {}
        except playbook.PlaybookError as error:
            if warn is None:
                raise
            warn(f"{error}. T3 starts without the {name} profile until the playbook is restored or setup "
                 "attaches another one")
            # The diagnostic goes to the log and t3-boat status only: package text, such as a role name, could
            # otherwise put a marker line inside the managed block and break the next start.
            state, reason = None, ("its attached playbook cannot be read; the container log and "
                                   "`docker exec t3-boat t3-boat status` name the file")
    enabled = PROVIDERS[name] if name is not None else ()
    if state is not None:
        # The Boat skill holds the workflow the lead is told to follow; a file the owner put at its path is kept,
        # and the profile stays unapplied until it is moved, so the lead is never sent to text t3-boat did not write.
        for provider, base, _ in targets(home):
            path = boat_skill_path(base)
            if provider in enabled and (found := skill_occupant(base, path)) is not None:
                message = (f"{found} is not a Boat skill t3-boat generated, so it is kept and the {name} profile is "
                           f"not applied; move it away from {path}, then restart the container or rerun setup")
                if warn is None:
                    raise ProfileError(message)
                warn(message)
                state, reason = None, ("a file t3-boat did not generate occupies the path of its Boat skill; the "
                                       "container log and `docker exec t3-boat t3-boat status` name the file")
                break
    path = settings_path(home)
    try:
        settings = json.loads(read_owned(path, "{}"))
    except (json.JSONDecodeError, RecursionError) as exc:
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
    front, body = (ROOT / "skills/t3-boat/SKILL.md").read_text().rstrip().split("\n---\n", 1)
    base_skill = f"{front}\n---\n\n{SKILL_MARK}\n{body}"
    generic = (ROOT / "skills/t3-boat/generic.md").read_text().rstrip()
    # Enabled providers first: where two providers' files are one file through a link, the enabled one's text wins.
    for provider, base, filename in sorted(targets(home), key=lambda row: row[0] not in enabled):
        target = base / filename
        skill_path = boat_skill_path(base)
        if (state is None or provider not in enabled) and generated_skill(skill_path):
            removals.append(skill_path)
        if state is None and provider in enabled:
            writes[target] = instructions(target, disabled_note(name, reason))
        elif state is not None and provider in enabled:
            override = base / "AGENTS.override.md"
            if provider == "codex" and override.is_file() and override.stat().st_size:
                raise ProfileError(f"{override} masks the required global profile")
            lead = state[PARENT]
            sections = [base_skill,
                        playbook_skills.section(base, package, model_set, lead_role, installed, overrides).rstrip()
                        if package else generic,
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
    active = home / "profiles/active"
    record = applied_path(name, home) if name is not None else None
    if name is None:
        if active.exists() or active.is_symlink():
            removals.insert(0, active)
    else:
        writes[active] = name + "\n"
    if state is None and record is not None:
        if record.exists() or record.is_symlink():
            removals.insert(0, record)  # Removed first, so no worker starts from the earlier choice.
    elif state is not None:
        roles = {role: playbook_skills.worker_guide(package, role) for role in package.roles} if package else None
        writes[record] = json.dumps({"profile": state, "playbook": package and {
            "name": package.name, "label": package.label(where=False), "roles": roles,
            "assignments": role_models.effective(package, model_set, overrides)}}, indent=2) + "\n"
    writes, removals = reconcile(writes, removals)
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


def prepare(name=None, state=None, loaded=None):
    """Write the saved choices (when setup passes `state`) and regenerate every file derived from them.

    The applied record is removed before anything changes. The profile file is then written in one atomic step:
    it is the whole choice. The generated files follow, and a new applied record is written only once all of them
    are in place. If that is interrupted, even when the saved choice did not change, no record is left, so
    boat-worker refuses to start a worker and t3-boat status reports the profile as not applied until the next
    container start or setup regenerates every file.
    `loaded` is what check_attachment returned for `state`, when the caller already read the package.
    """
    if name is not None:
        check_profile_name(name)
    elif state is not None:
        raise ProfileError("an unconfigured installation cannot have profile state")
    home = Path.home()
    with state_lock(home):
        if state is not None:
            check_state(name, state)
            loaded = loaded or check_attachment(name, state.get("playbook"))
        writes, removals, notices = plan(name, state, loaded=loaded, warn=None if state else
                                         lambda message: print(f"T3 Boat: {message}.", file=sys.stderr))
        record = applied_path(name, home) if name is not None else None
        if record is not None and record in removals:
            removals.remove(record)
        last = writes.pop(record, None) if record is not None else None
        # Invalidate the record before the first change: the saved choice can be unchanged while the package's
        # contents changed, so an interrupted run must not leave the earlier record describing new lead files.
        if record is not None:
            try:
                record.unlink(missing_ok=True)
            except OSError as error:
                raise ProfileError(f"{record}: cannot be removed: {error.strerror or error}") from None
        if state is not None:
            write(profile_path(name, home), json.dumps(state, indent=2) + "\n")
        for path, data in writes.items():
            write(path, data)
        playbook_skills.remove(removals)
        if last is not None:
            # Published only after every other file is in place: checked again after the removals, so a removal
            # that reached a written file through a path the plan did not recognize never leaves a record behind.
            for path, data in writes.items():
                try:
                    intact = playbook.read_regular(path) == data.encode()
                except OSError:
                    intact = False
                if not intact:
                    raise ProfileError(f"{path} does not hold what this run wrote, so the {name} profile is not "
                                       "applied; check whether another folder is mounted or linked over it, then "
                                       "restart the container or rerun setup")
            write(record, last)
    for notice in notices:
        print(f"T3 Boat: {notice}.", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) not in (1, 2):
        sys.exit("usage: boat_profile.py [PROFILE]")
    try:
        prepare(sys.argv[1] if len(sys.argv) == 2 else None)
    except (ProfileError, playbook.PlaybookError) as exc:  # Anything else is a defect and keeps its traceback.
        sys.exit(f"T3 Boat profile: {exc}")
