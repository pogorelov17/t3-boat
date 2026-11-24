import json
from pathlib import Path


def seeded_roles(home, profile):
    """Create an existing installation with concrete versions selected."""
    source = Path(__file__).resolve().parents[1] / "profiles" / (profile + ".json")
    rows = json.loads(source.read_text())
    for row in rows.values():
        family = row["model"]
        if row["provider"] == "codex":
            row["model"] = "gpt-6.2-" + family
        else:
            version = "5-2" if family == "fable" else "5-5"
            row["model"] = f"claude-{family}-{version}"
    target = Path(home) / "profiles" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2) + "\n")
    return rows


FIXTURES = Path(__file__).resolve().parent / "fixtures"
