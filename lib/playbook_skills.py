"""Install the agent-playbook role guides as provider skills, when the owner opts in during setup.

Each role's guide is its playbook bundle: the role document, the role's own skills and the shared skills
it needs. On this machine every guide becomes a skill for each enabled provider. A Boat worker receives
the guide of its own role in its provider's instruction file (see boat-worker).
"""
import json
from pathlib import Path

import playbook

STATE = Path("profiles/playbook-skills")  # Relative to the home directory; holds "yes" or "no".
PREFIX = "agent-playbook-"
MARK = "<!-- t3-boat: generated from agent-playbook"


def state_path(home):
    return Path(home) / STATE


def chosen(home):
    """Whether the owner chose to install the playbook skills on this machine."""
    try:
        return state_path(home).read_text().strip() == "yes"
    except OSError:
        return False


def skill_path(base, role):
    return Path(base) / "skills" / (PREFIX + role) / "SKILL.md"


def skill_text(role):
    description = f"agent-playbook role guide for the {role}. {playbook.summary(role)}".strip()
    return (f"---\nname: {PREFIX}{role}\ndescription: {json.dumps(description)}\n---\n\n"
            f"{MARK} {playbook.commit()}. t3-boat replaces this file; delete this line to keep your edits. -->\n\n"
            + playbook.bundle(role))


def generated(path):
    """Whether a file was written by t3-boat; anything else, including a directory, belongs to the owner."""
    try:
        return MARK in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def plan(home, bases, enabled):
    """Return (files to write, generated files to remove, kept custom files).

    `bases` maps each provider instance to its configuration directory; `enabled` lists the active ones.
    """
    install = chosen(home)
    roles = set(playbook.roles()) if install else set()
    writes, removals, kept = {}, [], []
    for provider, base in bases.items():
        wanted = roles if provider in enabled else set()
        for role in sorted(wanted):
            path = skill_path(base, role)
            if (path.exists() and not generated(path)) or (path.parent.exists() and not path.parent.is_dir()):
                kept.append(path)
            else:
                writes[path] = skill_text(role)
        for path in sorted((Path(base) / "skills").glob(PREFIX + "*/SKILL.md")):
            if path.parent.name.removeprefix(PREFIX) not in wanted and generated(path):
                removals.append(path)
    return writes, removals, kept


def remove(paths):
    for path in paths:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()  # Only when nothing else was put in the skill folder.
        except OSError:
            pass


def lead_note(base, installed):
    """The sentence pointing the team lead to its own guide, or an empty string.

    `installed` holds the guide paths this run writes or finds kept by the owner.
    """
    path = skill_path(base, "lead")
    if path not in installed:
        return ""
    return (f" Before you plan or delegate, also load and follow your agent-playbook role guide at {path}; "
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
