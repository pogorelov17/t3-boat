import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import playbook


def seeded_roles(home, profile):
    """Create an existing installation with concrete versions of the playbook's model families selected."""
    rows = playbook.templates(profile)
    for row in rows.values():
        family = row["model"]
        if row["provider"] == "codex":
            row["model"] = "gpt-6.2-" + family
        else:
            version = "5-2" if family == "fable" else "5-5"
            row["model"] = f"claude-{family}-{version}"
    target = Path(home) / "profiles" / (profile + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2) + "\n")
    return rows


FIXTURES = Path(__file__).resolve().parent / "fixtures"
