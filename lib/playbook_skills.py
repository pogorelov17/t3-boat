"""Install the agent-playbook role guides as provider skills, when the owner opts in during setup.

Each role's guide is its playbook bundle: the role document, the role's own skills and the shared skills
it needs. On this machine every guide becomes a skill for each enabled provider. A Boat worker receives
the guide of its own role in its provider's instruction file (see boat-worker).
"""
import json
import os
from pathlib import Path

import playbook

# Both paths are relative to the home directory. STATE holds "yes" or "no" for the agent-playbook guides,
# as earlier releases wrote it; WORKFLOW holds the workflow setup committed, one of WORKFLOWS.
STATE = Path("profiles/playbook-skills")
WORKFLOW = Path("profiles/workflow")
PREFIX = "agent-playbook-"
MARK = "<!-- t3-boat: generated from agent-playbook"
# generic: the lead briefs generic workers and the profile needs only the `lead` row.
# playbook: the agent-playbook role workflow, with its role guides installed as skills.
# roles: the role workflow without the guides, kept for installations set up by an earlier release.
WORKFLOWS = ("generic", "playbook", "roles")


def state_path(home):
    return Path(home) / STATE


def workflow_path(home):
    return Path(home) / WORKFLOW


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def workflow(home):
    """The workflow of this machine: the one setup committed, or the one an earlier release used.

    Earlier releases always ran the role workflow and saved only whether the guides were installed. An
    installation they set up (a saved guide answer or a saved role profile) keeps the role workflow until
    the owner chooses another one in setup; only a fresh installation starts with generic workers.
    """
    home = Path(home)
    committed = _read(workflow_path(home))
    if committed in WORKFLOWS:
        return committed
    guides = _read(state_path(home))
    if guides == "yes":
        return "playbook"
    if guides is not None or any((home / "profiles").glob("*.json")):
        return "roles"
    return "generic"


def chosen(home):
    """Whether the agent-playbook guides are installed on this machine (the playbook workflow)."""
    return workflow(home) == "playbook"


def state_files(home, selected):
    """The state files that commit workflow `selected`: write them after the instructions they describe."""
    if selected not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {selected!r}")
    # The guide answer keeps its meaning for an earlier release: only the playbook installs guides.
    return {state_path(home): ("yes" if selected == "playbook" else "no") + "\n",
            workflow_path(home): selected + "\n"}


def skill_path(base, role):
    return Path(base) / "skills" / (PREFIX + role) / "SKILL.md"


def description(role):
    """When a provider should load the guide: the role's rows, its own summary and what the guide holds."""
    rows = " or ".join(f"`{key}`" for key in playbook.keys(role))
    use = ("Load before your first response as the T3 Boat team lead (`lead`), including ordinary discussion, and "
           "follow it for the whole conversation; not when a task assigns you another role."
           if role == "lead" else f"Load when a task assigns you the {role} role (profile row {rows}).")
    return (f"agent-playbook {role} role guide: who the {role} is, what it receives and delivers, its rules and "
            f"report format, with its skills. {use} {playbook.summary(role)}").strip()


def skill_text(role):
    return (f"---\nname: {PREFIX}{role}\ndescription: {json.dumps(description(role))}\n---\n\n"
            f"{MARK} {playbook.commit()}. t3-boat replaces this file; delete this line to keep your edits. -->\n\n"
            + playbook.bundle(role))


def generated(path):
    """Whether a file was written by t3-boat; anything else, including a directory, belongs to the owner."""
    try:
        return MARK in path.read_text(encoding="utf-8", errors="replace")
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


def plan(home, bases, enabled, install=None):
    """Return (files to write, generated files to remove, kept custom files, blocked guides).

    `bases` maps each provider instance to its configuration directory; `enabled` lists the active ones.
    `install` says whether guides are wanted; by default, whether this machine uses the playbook workflow.
    Kept files are regular files the owner made at a guide path. Blocked guides are (guide path, obstacle)
    pairs where something else occupies the path; they are neither written nor named to the team lead.
    """
    install = chosen(home) if install is None else install
    roles = set(playbook.roles()) if install else set()
    writes, removals, kept, blocked = {}, [], [], []
    for provider, base in bases.items():
        wanted = roles if provider in enabled else set()
        for role in sorted(wanted):
            path = skill_path(base, role)
            if (found := obstacle(base, path)) is not None:
                blocked.append((path, found))
            elif path.is_file() and not generated(path):
                kept.append(path)
            else:
                writes[path] = skill_text(role)
        for path in sorted((Path(base) / "skills").glob(PREFIX + "*/SKILL.md")):
            if path.parent.name.removeprefix(PREFIX) not in wanted and generated(path):
                removals.append(path)
    return writes, removals, kept, blocked


def remove(paths):
    for path in paths:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()  # Only when nothing else was put in the skill folder.
        except OSError:
            pass


def lead_note(base, installed):
    """The sentences telling the team lead to follow its own guide from its first response, or an empty string.

    `installed` holds the guide files this run writes or keeps for the owner; a blocked path is never in it,
    and without the owner's opt-in nothing is, so the lead is never sent to a guide that is not there.
    """
    path = skill_path(base, "lead")
    if path not in installed:
        return ""
    return (f" As the team lead, load and follow your agent-playbook role guide at {path} before your first "
            "response, including ordinary discussion and questions, and keep following it for the whole "
            "conversation. When your task assigns you another role, follow that role's guide instead of this one; "
            "every worker started with boat-worker receives its own role guide.")


def roster(base, installed):
    """The skill section listing the guides installed for one provider, or an empty string."""
    rows = []
    for role in playbook.roles():
        path = skill_path(base, role)
        if path in installed:
            rows.append(f"| {role} | {', '.join(playbook.keys(role))} | {path} |")
    if not rows:
        return ""
    return "\n".join([
        "## Agent playbook", "",
        f"Role guides from agent-playbook {playbook.commit()} are installed on this machine. "
        "Each `boat-worker start ROLE` also writes that role's guide into the worker's own instruction file, "
        "so brief every worker with the inputs listed under `What you receive` in its guide.", "",
        "| Playbook role | Profile rows | Guide |", "| --- | --- | --- |", *rows, "",
    ])


def worker_guide(home, key):
    """The instruction text for a worker in profile row `key`, or None without a guide."""
    role = playbook.role_of(key)
    if not chosen(home) or role is None or role == "lead":
        return None
    row = "" if key == role else f" (profile row `{key}`)"
    return (f"You are the {role}{row} on this team. Your role guide from agent-playbook {playbook.commit()} "
            f"follows. Follow it for this whole task.\n\n{playbook.bundle(role)}")
