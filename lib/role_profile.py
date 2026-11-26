#!/usr/bin/env python3
"""Apply one Docker installation's editable role table before starting T3."""
import json
import os
import re
import stat
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import playbook
import playbook_skills

ROOT = Path(__file__).resolve().parents[1]
# Profiles, roles and model rules come from the pinned agent-playbook (lib/playbook.py).
PROVIDERS = playbook.profiles()
PARENT = "lead"
REQUIRED = tuple(playbook.role_keys())
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
MISSING = object()  # A saved profile that does not exist, as opposed to one holding JSON null.
BEGIN = "<!-- BEGIN t3-boat -->"
END = "<!-- END t3-boat -->"


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


def family(provider, model):
    """A model's family: sol for gpt-6.2-sol, opus for claude-opus-4-5-20251101; otherwise the model ID."""
    name = model.rsplit("/", 1)[-1].casefold()
    name = re.sub(r"-(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})$", "", name)
    if provider == "codex":
        match = re.fullmatch(r"gpt-[0-9]+(?:\.[0-9]+)*-([a-z]+)(?:-.*)?", name)
    else:
        match = re.fullmatch(r"claude-(?:([a-z]+)-[0-9].*|[0-9]+(?:[.-][0-9]+)*-([a-z]+)(?:-.*)?)", name)
    return next((group for group in match.groups() if group), name) if match else name


def json_kind(value):
    return {type(None): "null", list: "an array", str: "a string", bool: "a boolean"}.get(type(value), "a number")


def check_roles(rows):
    """Require a JSON object holding every role of this release."""
    if not isinstance(rows, dict):
        raise ProfileError(f"expected a JSON object of roles, found {json_kind(rows)}")
    missing = [role for role in REQUIRED if role not in rows]
    if missing:
        raise ProfileError("expected roles " + ", ".join(REQUIRED) + "; missing " + ", ".join(missing))


def validate_profile(name, rows):
    check_profile_name(name)
    check_roles(rows)
    for role, row in rows.items():
        if not isinstance(role, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", role) or not isinstance(row, dict):
            raise ProfileError(f"invalid role {role!r}")
        if row.get("provider") not in PROVIDERS[name]:
            raise ProfileError(f"invalid provider for {role}")
        for key in ("model", "effort"):
            if not isinstance(row.get(key), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*", row[key]):
                raise ProfileError(f"invalid {key} for {role}")
    # The playbook's model rules: a model is its provider plus family, and effort does not tell two apart.
    for role, other, shared in playbook.rules(name):
        mine, theirs = rows[role], rows[other]
        used = family(theirs["provider"], theirs["model"])
        if mine["provider"] != theirs["provider"] or family(mine["provider"], mine["model"]) != used:
            continue
        if used == shared and mine["effort"] != theirs["effort"]:
            continue  # The model set's declared exception, such as codex-only reviewer2 on the executor's Sol.
        if used == shared:
            raise ProfileError(f"select a different effort for {role}; {name} lets it share {other}'s "
                               f"{used} family only at a different effort")
        raise ProfileError(f"select a different model for {role}; {other} uses {theirs['provider']} {used}")
    return rows


class ProfileError(ValueError):
    """A problem with a file the user can repair; reported as one line."""


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


def saved_rows(target, missing=None):
    """Return a saved profile's parsed JSON, or `missing` when there is none; errors name the file."""
    if not profile_present(target):
        return missing
    try:
        original = target.read_bytes()
    except FileNotFoundError:
        return missing
    except OSError as error:  # For example, a file the container user may not read.
        raise ProfileError(f"{target}: cannot be read: {error.strerror or error}") from None
    try:
        return json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{target}: {exc}") from None


def read_profile(name):
    check_profile_name(name)
    target = Path.home() / "profiles" / (name + ".json")
    rows = saved_rows(target, missing=MISSING)
    if rows is MISSING:
        raise ProfileError("Select role models with t3-boat setup first")
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

    # Without the owner's opt-in to the agent-playbook the lead delegates to generic workers; with it, the
    # playbook's role workflow applies. The shared part (machine boundary, parent updates, cleanup) is in both.
    roles_mode = playbook_skills.chosen(home)
    policy = (ROOT / "skills/t3-boat/SKILL.md").read_text().rstrip() + "\n\n" + (
        ROOT / "skills/t3-boat" / ("playbook.md" if roles_mode else "generic.md")).read_text()
    heading, column = ("## Active roles", "Role") if roles_mode else ("## Model presets", "Preset")
    table = [heading, "", f"| {column} | Provider | Model | Option |", "| --- | --- | --- | --- |"]
    for role, row in (rows or {}).items():
        table.append(f"| {role} | {row['provider']} | {row['model']} | {OPTION[row['provider']]}={row['effort']} |")
    targets = [
        ("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "AGENTS.md"),
        ("claudeAgent", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "CLAUDE.md"),
    ]
    # The owner's setup choice: agent-playbook role guides as skills for every enabled provider.
    skill_files, stale_skills, kept, blocked = playbook_skills.plan(home, {p: base for p, base, _ in targets}, enabled)
    updates = dict(skill_files)
    installed = set(skill_files) | set(kept)  # Only guide files that will exist; a blocked path is never named.
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
                f"Discuss ideas and plan new projects in this chat without starting a VM; targeted web fact checks are fine here. "
                f"Before any project source inspection, edits, project commands, tests, installs, clones or extended research, "
                f"load and follow {skill_path} automatically. Every project uses this workflow. "
                "Keep source on Boat. Post compact, factual updates in the parent. Never change models silently."
            )
            roster = playbook_skills.roster(base, installed)
            sections = [policy.rstrip(), "\n".join(table)]
            if roles_mode:
                sections.append("\n".join(["## Model rules", "", *playbook.rule_lines(name)]))
            if roster:
                sections.append(roster.rstrip())
            updates[skill_path] = "\n\n".join(sections) + "\n"
            updates[path] = instructions(path, note + playbook_skills.lead_note(base, installed))
        elif path.exists():
            updates[path] = instructions(path, "")
    updates[settings_path] = json.dumps(settings, indent=2) + "\n"
    # A provider's child shell may omit custom environment variables.
    updates[home / "profiles/active"] = name + "\n"
    if save_selection:
        updates[role_path] = json.dumps(rows, indent=2) + "\n"
    for path, data in updates.items():
        write(path, data)
    playbook_skills.remove(stale_skills)
    for path in kept:
        print(f"T3 Boat: kept your own {path}; it has no t3-boat marker, so it is used in place of the "
              "agent-playbook guide.", file=sys.stderr)
    for path, obstacle in blocked:
        print(f"T3 Boat: {obstacle} is not a directory or a file t3-boat can replace; the agent-playbook guide "
              f"{path} is not installed.", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: role_profile.py PROFILE")
    try:
        prepare(sys.argv[1])
    except ProfileError as exc:  # Anything else is a defect and keeps its traceback.
        sys.exit(f"T3 Boat profile: {exc}")
