#!/usr/bin/env python3
"""Apply one Docker installation's editable role table before starting T3."""
import json
import hashlib
import os
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "codex-only": ("codex",),
    "claude-only": ("claudeAgent",),
    "codex+claude": ("codex", "claudeAgent"),
}
REQUIRED = ("orchestrator", "executor", "reviewer1", "reviewer2", "scout", "researcher", "adjudicator")
OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort"}
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


def read_profile(name):
    if name not in PROVIDERS:
        raise ValueError("T3_BOAT_PROFILE must be one of: " + ", ".join(PROVIDERS))
    target = Path.home() / "profiles" / (name + ".json")
    source = target if target.exists() else ROOT / "profiles" / target.name
    rows = json.loads(source.read_text())
    if not isinstance(rows, dict) or not set(REQUIRED).issubset(rows):
        raise ValueError(f"{source}: expected roles " + ", ".join(REQUIRED))
    for role, row in rows.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", role) or not isinstance(row, dict):
            raise ValueError(f"{source}: invalid role {role!r}")
        if row.get("provider") not in PROVIDERS[name]:
            raise ValueError(f"{source}: invalid provider for {role}")
        for key in ("model", "effort"):
            if not isinstance(row.get(key), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*", row[key]):
                raise ValueError(f"{source}: invalid {key} for {role}")
    author = rows["executor"]["model"].rsplit("/", 1)[-1].casefold()
    for role in ("reviewer1", "reviewer2", "adjudicator"):
        if rows[role]["model"].rsplit("/", 1)[-1].casefold() == author:
            raise ValueError(f"{source}: select a different model for {role}; {author} writes the code")
    return rows


def instructions(path, content):
    previous = path.read_text() if path.exists() else ""
    if previous.count(BEGIN) != previous.count(END) or previous.count(BEGIN) > 1:
        raise ValueError(f"Repair the t3-boat marker block in {path}")
    block = f"{BEGIN}\n{content}\n{END}" if content else ""
    if BEGIN in previous:
        start, rest = previous.split(BEGIN, 1)
        _, end = rest.split(END, 1)
        return start + block + end
    if not block:
        return previous
    return previous + ("\n\n" if previous else "") + block + "\n"


def prepare(name):
    # Only migrate the exact stock file installed by 0.1.0. Keep user edits.
    if name == "claude-only":
        saved = Path.home() / "profiles/claude-only.json"
        old_digest = "56398685df58d83b6a053c9213a0468afa56ecea6eb003b215731de95701dffb"
        if saved.is_file() and hashlib.sha256(saved.read_bytes()).hexdigest() == old_digest:
            write(saved, (ROOT / "profiles/claude-only.json").read_text())
    roles = read_profile(name)
    enabled = PROVIDERS[name]
    home = Path.home()
    settings_path = Path(os.environ.get("T3CODE_HOME") or home / ".t3") / "userdata/settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    parent = roles["orchestrator"]
    settings["defaultModelSelection"] = {
        "instanceId": parent["provider"], "model": parent["model"],
        "options": [{"id": OPTION[parent["provider"]], "value": parent["effort"]}],
    }
    settings["linkedEnvironmentsEnabled"] = True
    instances = settings.setdefault("providerInstances", {})
    for provider in OPTION:
        instances.setdefault(provider, {}).update(driver=provider, enabled=provider in enabled)

    policy = (ROOT / "skills/t3-boat/SKILL.md").read_text()
    table = ["## Active roles", "", "| Role | Provider | Model | Option |", "| --- | --- | --- | --- |"]
    for role, row in roles.items():
        table.append(f"| {role} | {row['provider']} | {row['model']} | {OPTION[row['provider']]}={row['effort']} |")
    skill = policy.rstrip() + "\n\n" + "\n".join(table) + "\n"
    targets = [
        ("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "AGENTS.md"),
        ("claudeAgent", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "CLAUDE.md"),
    ]
    updates = {}
    for provider, base, filename in targets:
        path = base / filename
        if provider in enabled:
            override = base / "AGENTS.override.md"
            if provider == "codex" and override.is_file() and override.stat().st_size:
                raise ValueError(f"{override} masks the required global profile")
            skill_path = base / "skills/t3-boat/SKILL.md"
            note = (
                f"The active T3 Boat profile is `{name}`. You are the orchestrator / team lead. "
                f"Respond to the user's opening message in their language. If they ask where to begin, suggest a starting point based on their interests. "
                f"Discuss ideas and plan new projects in this chat without starting a VM. Before any project inspection, tests, research or code changes, "
                f"load and follow {skill_path} automatically. Every project uses this workflow. "
                "Keep source on Boat. Report compact milestones in the parent. Never change models silently."
            )
            updates[skill_path] = skill
            updates[path] = instructions(path, note)
        elif path.exists():
            updates[path] = instructions(path, "")
    role_path = home / "profiles" / (name + ".json")
    if not role_path.exists():
        updates[role_path] = (ROOT / "profiles" / role_path.name).read_text()
    updates[settings_path] = json.dumps(settings, indent=2) + "\n"
    # A provider's child shell may omit custom environment variables.
    updates[home / "profiles/active"] = name + "\n"
    for path, data in updates.items():
        write(path, data)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: profile.py PROFILE")
        prepare(sys.argv[1])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        sys.exit(f"T3 Boat profile: {exc}")
