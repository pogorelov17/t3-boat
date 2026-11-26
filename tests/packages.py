"""Build playbook packages for tests: the agent-playbook format with any roles, counts and model sets."""
import json
from pathlib import Path

# A team that shares no role name, count or model with agent-playbook: T3 Boat must treat it like any other.
CUSTOM_ROLES = {
    "harbor-master": 1,
    "rigger": 3,
    "cartographer": 2,
    "qa_bot": 1,
}
CUSTOM_SETS = {
    "both-providers": {
        "harbor-master": {"provider": "claude", "family": "opus", "effort": "high"},
        "rigger": [{"provider": "codex", "family": "luna", "effort": "low"},
                   {"provider": "codex", "family": "sol", "effort": "medium"},
                   {"provider": "claude", "family": "haiku", "effort": "medium"}],
        "cartographer": [{"provider": "claude", "family": "sonnet", "effort": "high"},
                         {"provider": "codex", "family": "astra", "effort": "high"}],
    },
    "codex-crew": {
        "harbor-master": {"provider": "codex", "family": "astra", "effort": "high"},
        "rigger": {"provider": "codex", "family": "luna", "effort": "low"},
    },
}


def write_package(root, roles=None, sets=None, name="harbor-crew", rules=None, version="7.1.0", lead_role=None):
    """Write a package to `root` and return its path. `roles` maps role names to instance counts."""
    root = Path(root)
    roles = CUSTOM_ROLES if roles is None else roles
    sets = CUSTOM_SETS if sets is None else sets
    manifest = {"schema": 1, "name": name, "version": version, "roles": {}, "model_sets": {}}
    for role, count in roles.items():
        folder = root / "crew" / role
        (folder / "how").mkdir(parents=True, exist_ok=True)
        (folder / "ROLE.md").write_text(f"# {role}\n\n## Who you are\n\nYou are the {role}. Second sentence.\n\n"
                                        f"## Rules\n\nROLE-RULES-{role}\n")
        (folder / "how" / "steps.md").write_text(f"STEPS-{role}\n")
        (root / "common").mkdir(exist_ok=True)
        (root / "common" / "report.md").write_text("COMMON-REPORT\n")
        manifest["roles"][role] = {"instances": count,
                                   "bundle": [f"crew/{role}/ROLE.md", f"crew/{role}/how/steps.md", "common/report.md"]}
    for set_name, assignments in sets.items():
        path = root / "assignments" / f"{set_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 1, "name": set_name, "roles": assignments,
                                    "exceptions": [{"role": "rigger", "instance": 2, "reason": "test"}]}))
        manifest["model_sets"][set_name] = f"assignments/{set_name}.json"
    if rules is not None:
        manifest["model_rules"] = rules
    (root / "team.json").write_text(json.dumps(manifest, indent=2))
    return root
