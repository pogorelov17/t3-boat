"""Install an attached playbook's role guides as provider skills on this T3 server, and describe it to the lead.

Each role's guide is its package bundle. On this machine every guide becomes a skill for each enabled provider,
so the team lead can read what each role receives and delivers. A Boat worker started for a role receives that
role's guide in its provider's instruction file on its VM (see boat-worker).
"""
import json
import os
from pathlib import Path

from playbook import read_regular, skill_name

MARK = "<!-- t3-boat: generated from an attached playbook"


def skill_path(base, package, role):
    return Path(base) / "skills" / skill_name(package.name, role) / "SKILL.md"


def description(package, role, lead_role):
    count = package.roles[role]["instances"]
    use = ("Load before your first response as the T3 Boat team lead, including ordinary discussion, and follow it "
           "for the whole conversation." if role == lead_role else
           f"Read it to brief a Boat worker for the {role} role (`boat-worker start {role}`).")
    return (f"{package.name} {role} role guide ({count} instance{'s' if count != 1 else ''} declared). "
            f"{use} {package.summary(role)}").strip()


def skill_text(package, role, lead_role):
    return (f"---\nname: {skill_name(package.name, role)}\ndescription: {json.dumps(description(package, role, lead_role))}\n---\n\n"
            f"{MARK} {package.label()}. t3-boat replaces this file; delete this line to keep your edits. -->\n\n"
            + package.bundle(role))


def generated(path):
    """Whether a file was written by t3-boat; anything else, including a directory, belongs to the owner."""
    try:
        return MARK in read_regular(path).decode("utf-8", errors="replace")
    except OSError:
        return False


def obstacle(base, path):
    """The path that keeps a guide from being written or read: a non-directory where a folder goes, or
    something other than a readable file at the guide's own path. None when nothing is in the way."""
    for folder in (Path(base) / "skills", path.parent):
        if (folder.exists() or folder.is_symlink()) and not folder.is_dir():
            return folder
    if path.exists() and (not path.is_file() or not os.access(path, os.R_OK)):
        return path
    return None


def plan(bases, enabled, package, lead_role=None):
    """Return (files to write, generated files to remove, kept custom files, blocked guides).

    `bases` maps each provider instance to its configuration directory; `enabled` lists the active ones. Without
    a package nothing is wanted, so every guide t3-boat generated earlier is removed. Kept files are regular files
    the owner made at a guide path. Blocked guides are (guide path, obstacle) pairs where something else occupies
    the path; they are neither written nor named to the team lead.
    """
    writes, removals, kept, blocked = {}, [], [], []
    for provider, base in bases.items():
        wanted = set()
        if package is not None and provider in enabled:
            for role in package.roles:
                path = skill_path(base, package, role)
                wanted.add(path)
                if (found := obstacle(base, path)) is not None:
                    blocked.append((path, found))
                elif path.is_file() and not generated(path):
                    kept.append(path)
                else:
                    writes[path] = skill_text(package, role, lead_role)
        for path in sorted((Path(base) / "skills").glob("*/SKILL.md")):
            if path not in wanted and generated(path):
                removals.append(path)
    return writes, removals, kept, blocked


def remove(paths):
    for path in paths:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()  # Only when nothing else was put in the skill folder.
        except OSError:
            pass


def lead_note(base, package, lead_role, installed):
    """The sentence sending the team lead to the guide of the role setup chose for it, or an empty string.

    `installed` holds the guide files this run writes or keeps for the owner; a blocked path is never in it.
    """
    if package is None or lead_role is None:
        return ""
    path = skill_path(base, package, lead_role)
    if path not in installed:
        return ""
    return (f" The owner attached the playbook {package.name}: load and follow its {lead_role} guide at {path} "
            "before your first response, including ordinary discussion and questions, and for the whole conversation.")


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def section(base, package, model_set, lead_role, installed):
    """The skill section that describes the attached package to the lead: data only, quoted from the package."""
    lines = [
        "## Attached playbook", "",
        f"The owner attached the playbook {package.label()}. Its guides decide the workflow: which roles take part, "
        "what each one receives and delivers, how many instances run, in what order and on which models. This skill "
        "only says how to run them on Boat. Where the playbook and this skill differ, follow the playbook, except for "
        "the machine boundary, the control plane, the parent updates and the cleanup above, which hold in every "
        "mode.", "",
        (f"You, the T3 team lead, follow the `{lead_role}` guide." if lead_role else
         "Setup assigned you none of these roles: you are the team lead and brief each role's worker yourself."), "",
        "Start a worker for a role with `boat-worker start ROLE`: its VM receives that role's guide in its provider's "
        "instruction file before the worker does anything. `boat-worker start` without a role starts a worker that "
        "has only your brief. The table lists the roles and instance counts that the package's `team.json` declares.",
        "", "| Role | Instances | Guide on this server |", "| --- | --- | --- |",
    ]
    for role, spec in package.roles.items():
        path = skill_path(base, package, role)
        lines.append(f"| {role} | {spec['instances']} | {path if path in installed else 'not installed'} |")
    if model_set is None:
        lines += ["", "No model set of the package is selected for this profile, so the package assigns no models "
                      "here. Choose each worker's model yourself and name it in the parent update."]
    else:
        data = package.model_set(model_set)
        lines += ["", f"### Model assignment: `{model_set}`", "",
                  f"Selected in setup from {data['path']}. Resolve each family to an exact model that "
                  "`orchestrator_capabilities` lists before you delegate; if none fits, tell the owner before "
                  "starting the worker.", "",
                  "| Role | Instance | T3 provider | Family | Effort |", "| --- | --- | --- | --- | --- |"]
        for role, entries in data["roles"].items():
            for number, entry in enumerate(entries, start=1):
                lines.append(f"| {_cell(role)} | {number} | {entry['provider']} | {_cell(entry['family'])} | "
                             f"{_cell(entry['effort'])} |")
        if data["exceptions"]:
            lines += ["", "Exceptions declared by this model set, as written in it:", "", "```json",
                      json.dumps(data["exceptions"], indent=2), "```"]
    if package.model_rules:
        lines += ["", "Model rules declared by the package's `team.json`, as written in it:", "", "```json",
                  json.dumps(package.model_rules, indent=2), "```"]
    return "\n".join(lines) + "\n"


# Every worker reports the same way, with or without a playbook role, and whatever its guide says.
REPORTING = ("Your result for the lead states only what you observed: what you did, the commands you ran with pass "
             "or fail, the identifiers of anything you produced or published, open questions and what is "
             "unverified. Keep credentials, tokens and account details out of it.")


def worker_guide(package, role):
    """The instruction text a worker VM receives for one of the package's roles."""
    return (f"You are a worker on a disposable Boat VM. The T3 team lead started you for the `{role}` role of the "
            f"playbook {package.label(where=False)}. Your role guide follows; follow it and the lead's brief for "
            "this whole task. The brief and the guide decide what you deliver and how. Do all project work here, "
            f"on this VM. {REPORTING}\n\n{package.bundle(role)}")
