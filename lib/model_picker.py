"""Select the lead model: the model of the T3 chats and the model every Boat worker opens with."""
import re

from boat_profile import PARENT, PROVIDERS, check_attachment, prepare, read_state
from model_catalog import catalog


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


def resolve(entry, available):
    """The newest stable release of a package entry's family that supports its effort, or None."""
    matches = []
    for model, efforts in available.get(entry["provider"], {}).items():
        version = stable_version(entry["provider"], entry["family"], model)
        if version is not None and entry["effort"] in efforts:
            matches.append((version, model))
    if not matches:
        return None
    return {"provider": entry["provider"], "model": max(matches)[1], "effort": entry["effort"]}


def suggestion(package, model_set, lead_role, available):
    """The model the attached package's model set assigns to the lead's role, resolved to a listed release."""
    entry = package.entry(model_set, lead_role) if package else None
    if entry is None:
        return None, None
    source = (f"{package.name} model set {model_set} for {lead_role}: "
              f"{entry['provider']} {entry['family']} {entry['effort']}")
    return resolve(entry, available), source


def option(value, choices):
    for number, choice in enumerate(choices, start=1):
        if value in (choice, str(number)):
            return choice
    return None


def choose(label, choices, current):
    if len(choices) == 1:
        print(f"{label}: {choices[0]}")
        return choices[0]
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


def usable(row, available):
    return bool(row) and row.get("effort") in available.get(row.get("provider"), {}).get(row.get("model"), [])


def configure(profile, attachment, package=None):
    """Pick the lead model for `profile` and save it together with the playbook `attachment` (or None).

    `package` is the attachment's package as setup read it and showed it to the owner. The suggestion below and
    every file generated from this choice come from that one read, even when the configured path names another
    release by now; the path itself is saved, so a later container start or setup reads whatever it names then.
    Without `package` the attachment is read here, once.
    """
    if profile not in PROVIDERS:
        raise ValueError(f"Unknown profile {profile!r}")
    loaded = check_attachment(profile, attachment, package)
    package, model_set, lead_role = loaded
    discovered = catalog(profile)
    available = {
        provider: {model: efforts for model, efforts in discovered.get(provider, {}).items() if efforts}
        for provider in PROVIDERS[profile]
    }
    providers = [provider for provider, models in available.items() if models]
    if not providers:
        raise ValueError("No models with supported effort levels were discovered. Check the provider login and rerun setup.")
    state = read_state(profile, missing=None)
    row = dict(state[PARENT]) if state else None
    suggested, source = suggestion(package, model_set, lead_role, available)
    if source:
        print(f"Suggested by {source}: " + (assignment(suggested) if suggested else "no listed release supports it"))
    if row is None and suggested:
        row = dict(suggested)
    print("The lead model is the model of your T3 chats and the model every Boat worker opens with. "
          "The lead can pick another listed model for each brief.")
    editing = not usable(row, available)
    while True:
        if editing:
            if row and not usable(row, available):
                print(f"lead: {assignment(row)} is not available; choose a model and supported effort.")
            current = row or {}
            provider = choose("Provider", providers, current.get("provider"))
            model = choose("Model", list(available[provider]), current.get("model"))
            effort = choose("Effort", available[provider][model], current.get("effort"))
            row, editing = {"provider": provider, "model": model, "effort": effort}, False
        print(f"lead: {assignment(row)}")
        hint = ", or 'refresh' for the playbook's suggestion" if suggested and suggested != row else ""
        action = input(f"Enter to accept, 'edit' to change it{hint}: ").strip().lower()
        if not action:
            prepare(profile, {PARENT: row, "playbook": attachment}, loaded=loaded)
            return row
        if action == "refresh" and hint:
            row = dict(suggested)
        elif action in ("edit", "1", PARENT):
            editing = True
        else:
            print("Press Enter, or type edit" + (" or refresh." if hint else "."))
