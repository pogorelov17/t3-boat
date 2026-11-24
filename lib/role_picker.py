"""Select and pin the models used by this installation's T3 roles."""
import json
import re
from pathlib import Path

from model_catalog import catalog
from role_profile import PROVIDERS, ROOT, prepare, saved_rows, validate_profile


def stable_version(provider, family, model):
    """Order stable family members by release version, then by snapshot date."""
    date = re.search(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", model)
    release = model[:date.start()] if date else model
    family = re.escape(family)
    if provider == "codex":
        match = re.fullmatch(r"gpt-(\d+(?:\.\d+)*)-" + family, release)
    else:
        version = r"(\d+(?:[.-]\d+)*)"
        match = re.fullmatch("claude-" + family + "-" + version, release)
        if not match:
            match = re.fullmatch("claude-" + version + "-" + family, release)
    if match:
        return tuple(map(int, re.split("[.-]", match[1]))), date[1].replace("-", "") if date else ""
    return None


def resolve_defaults(profile, templates, available):
    required_efforts = {role: {row["effort"]} for role, row in templates.items()}
    if profile == "codex-only" and templates["executor"]["model"] == templates["reviewer2"]["model"] == "sol":
        shared = required_efforts["executor"] | required_efforts["reviewer2"]
        required_efforts["executor"] = required_efforts["reviewer2"] = shared

    rows = {}
    for role, template in templates.items():
        provider, family = template["provider"], template["model"]
        matches = []
        for model, efforts in available.get(provider, {}).items():
            version = stable_version(provider, family, model)
            if version is not None and required_efforts[role].issubset(efforts):
                matches.append((version, model))
        model = max(matches)[1] if matches else f"{family} (no compatible release)"
        rows[role] = dict(template, model=model)
    return rows


def option(value, choices):
    for number, choice in enumerate(choices, start=1):
        if value in (choice, str(number)):
            return choice
    return None


def choose(label, choices, current):
    print(label + " choices")
    for number, choice in enumerate(choices, start=1):
        print(f"  {number}. {choice}")
    default = current if current in choices else None
    prompt = f"{label} [{default}]: " if default else f"{label}: "
    while True:
        raw = input(prompt).strip()
        selected = option(raw, choices) if raw else default
        if selected is not None:
            return selected
        print(f"Select a listed {label.lower()} by number or name.")


def assignment(row):
    return " / ".join(row.get(key, "?") for key in ("provider", "model", "effort"))


def configure_roles(profile):
    if profile not in PROVIDERS:
        raise ValueError(f"Unknown role profile {profile!r}")
    discovered = catalog(profile)
    available = {
        provider: {model: efforts for model, efforts in discovered.get(provider, {}).items() if efforts}
        for provider in PROVIDERS[profile]
    }
    providers = [provider for provider, models in available.items() if models]
    if not providers:
        raise ValueError("No models with supported effort levels were discovered. Check the provider login and rerun setup.")

    saved = Path.home() / "profiles" / (profile + ".json")
    templates = json.loads((ROOT / "profiles" / saved.name).read_text())
    defaults = resolve_defaults(profile, templates, available)
    rows = saved_rows(saved)
    if rows is not None:
        if not isinstance(rows, dict) or any(
            not isinstance(row, dict) or any(not isinstance(row.get(key, ""), str)
                                            for key in ("provider", "model", "effort"))
            for row in rows.values()
        ):
            raise ValueError(f"Cannot read role entries in {saved}. Repair the JSON and rerun setup.")
        print(f"Saved role assignments for {profile}")
    else:
        rows = {role: dict(row) for role, row in defaults.items()}
        print(f"Suggested role assignments for {profile}")

    while True:
        print("   Role             Provider / Model / Effort")
        unresolved = []
        for number, (role, row) in enumerate(rows.items(), start=1):
            model_efforts = available.get(row.get("provider"), {}).get(row.get("model"), [])
            issue = ""
            if not model_efforts:
                issue = "  [select an available model]"
            elif row.get("effort") not in model_efforts:
                issue = "  [select a supported effort]"
            if issue:
                unresolved.append(role)
            print(f"{number:2}. {role:16} {assignment(row)}{issue}")
        action = input("Enter to accept, a role number or name to edit, or 'refresh' for current family defaults: ").strip()
        if action == "refresh":
            for role, default in defaults.items():
                if rows.get(role) != default:
                    print(f"{role}: {assignment(rows.get(role, {}))} -> {assignment(default)}")
                rows[role] = dict(default)
            continue
        if not action:
            if unresolved:
                print("Choose a model and supported effort for " + ", ".join(unresolved) + ".")
                continue
            try:
                validate_profile(profile, rows)
            except ValueError as error:
                print(error)
                continue
            prepare(profile, rows)
            return
        role = option(action, list(rows))
        if role is None:
            print("Select a listed role by number or name.")
            continue
        row = rows[role]
        provider = choose("Provider", providers, row.get("provider"))
        model = choose("Model", list(available[provider]), row.get("model"))
        effort = choose("Effort", available[provider][model], row.get("effort"))
        row.update(provider=provider, model=model, effort=effort)
