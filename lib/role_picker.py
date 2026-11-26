"""Select and pin the models used by this installation's T3 roles."""
import re
from pathlib import Path

import playbook
import playbook_skills
from model_catalog import catalog
from role_profile import (MISSING, PARENT, PROVIDERS, ProfileError, json_kind, prepare, saved_rows, uses_roles,
                          validate_profile)


def stock_templates(profile):
    """The playbook model set's suggested provider, model family and effort for every role row."""
    return playbook.templates(profile)


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
    # A model set exception lets two rows share a family, for example codex-only reviewer2 and the
    # executor on Sol. Both then resolve to the newest release that supports both efforts.
    for role, other, family in playbook.rules(profile):
        if family and templates[role]["model"].casefold() == templates[other]["model"].casefold() == family:
            shared = required_efforts[role] | required_efforts[other]
            required_efforts[role] = required_efforts[other] = shared

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


def saved_assignments(saved):
    """Return a saved profile's rows, or MISSING; a file the picker cannot show names itself."""
    rows = saved_rows(saved, missing=MISSING)
    if rows is MISSING:
        return rows
    if not isinstance(rows, dict):
        raise ProfileError(f"{saved}: expected a JSON object of roles, found {json_kind(rows)}")
    if any(not isinstance(row, dict) or any(not isinstance(row.get(key, ""), str)
                                            for key in ("provider", "model", "effort"))
           for row in rows.values()):
        raise ProfileError(f"{saved}: each role must be an object with string provider, model and effort")
    return rows


def configure_roles(profile, workflow=None):
    """Pick the models for `workflow` (by default, this machine's) and apply them together with it.

    Generic workers need one model, the `lead` row, which the team lead and every generic worker open with;
    saved rows for other roles stay in the profile unchanged as presets. A workflow with roles shows every
    role row, filling the rows a generic profile lacks from the model set's defaults.
    """
    if profile not in PROVIDERS:
        raise ValueError(f"Unknown role profile {profile!r}")
    workflow = workflow or playbook_skills.workflow(Path.home())
    roles = uses_roles(workflow)
    discovered = catalog(profile)
    available = {
        provider: {model: efforts for model, efforts in discovered.get(provider, {}).items() if efforts}
        for provider in PROVIDERS[profile]
    }
    providers = [provider for provider, models in available.items() if models]
    if not providers:
        raise ValueError("No models with supported effort levels were discovered. Check the provider login and rerun setup.")

    saved = Path.home() / "profiles" / (profile + ".json")
    templates = stock_templates(profile)
    defaults = resolve_defaults(profile, templates, available)
    rows = saved_assignments(saved)
    if rows is not MISSING:
        print(f"Saved {'role assignments' if roles else 'model'} for {profile}")
    else:
        rows = {}
        print(f"Suggested {'role assignments' if roles else 'model'} for {profile}")
    if roles:
        missing = {role: dict(row) for role, row in defaults.items() if role not in rows}
        if missing and rows:
            print("Suggested models for the roles the saved profile lacks: " + ", ".join(missing))
        rows = {**{role: rows.get(role, missing.get(role)) for role in defaults}, **rows}
    else:
        rows = {PARENT: dict(defaults[PARENT]), **rows}
        print("Generic workers need one model: the team lead and every generic worker open with the lead row, "
              "and the lead can pick another listed model for each brief.")
    # Generic workers edit only the lead row; any other saved row is kept as it is, as a preset.
    shown = list(rows) if roles else [PARENT]

    while True:
        print("   Role             Provider / Model / Effort")
        unresolved = []
        for number, role in enumerate(shown, start=1):
            row = rows[role]
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
                if role not in shown:
                    continue
                if rows.get(role) != default:
                    print(f"{role}: {assignment(rows.get(role, {}))} -> {assignment(default)}")
                rows[role] = dict(default)
            continue
        if not action:
            if unresolved:
                print("Choose a model and supported effort for " + ", ".join(unresolved) + ".")
                continue
            try:
                validate_profile(profile, rows, workflow)
            except ValueError as error:
                print(error)
                continue
            prepare(profile, rows, workflow)
            return
        role = option(action, shown)
        if role is None:
            print("Select a listed role by number or name.")
            continue
        row = rows[role]
        provider = choose("Provider", providers, row.get("provider"))
        model = choose("Model", list(available[provider]), row.get("model"))
        effort = choose("Effort", available[provider][model], row.get("effort"))
        row.update(provider=provider, model=model, effort=effort)
