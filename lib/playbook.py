"""Read the pinned agent-playbook: its roles, role bundles, model sets and model rules.

The playbook is fetched at a fixed commit by scripts/fetch-playbook and read offline. Every path comes
from its team.json manifest, so the layout of role folders inside the playbook does not matter here.
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Playbook provider names and the T3 provider instance each one runs on, in display order.
PROVIDER_IDS = {"codex": "codex", "claude": "claudeAgent"}
NAME = re.compile(r"[a-z][a-z0-9-]*")


class PlaybookError(ValueError):
    """The playbook in this installation is missing or malformed; the message names the file."""


def location():
    return Path(os.environ.get("T3_BOAT_PLAYBOOK") or ROOT / "playbook")


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlaybookError(f"{path} is missing. Fetch the pinned playbook with scripts/fetch-playbook "
                            f"{location()}") from None
    except (OSError, ValueError) as error:
        raise PlaybookError(f"{path}: {error}") from None


def _inside(rel):
    """Resolve a manifest path inside the playbook; anything else is an error naming the manifest."""
    base = location().resolve()
    path = (base / rel).resolve()
    if not isinstance(rel, str) or not rel or rel.startswith("/") or base not in path.parents:
        raise PlaybookError(f"{location() / 'team.json'}: path {rel!r} is outside the playbook")
    return path


@lru_cache(maxsize=None)
def manifest():
    path = location() / "team.json"
    data = _json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise PlaybookError(f"{path}: expected a schema 1 team description")
    roles, sets = data.get("roles"), data.get("model_sets")
    if not isinstance(roles, dict) or "lead" not in roles or not isinstance(sets, dict) or not sets:
        raise PlaybookError(f"{path}: expected roles including lead, and model_sets")
    for role, spec in roles.items():
        if (not NAME.fullmatch(role) or not isinstance(spec, dict)
                or not isinstance(spec.get("instances"), int) or isinstance(spec.get("instances"), bool)
                or spec["instances"] < 1 or not isinstance(spec.get("bundle"), list) or not spec["bundle"]
                or not all(isinstance(rel, str) for rel in spec["bundle"])):
            raise PlaybookError(f"{path}: role {role!r} needs a name, a positive instances count and a bundle")
    if roles["lead"]["instances"] != 1:
        raise PlaybookError(f"{path}: the lead has exactly one instance")
    for name, rel in sets.items():
        if not NAME.fullmatch(name.replace("+", "-")) or not isinstance(rel, str):
            raise PlaybookError(f"{path}: invalid model set {name!r}")
    rules = data.get("model_rules", [])
    if not isinstance(rules, list) or not all(
            isinstance(rule, dict) and isinstance(rule.get("roles"), list)
            and set(rule["roles"]) | {rule.get("must_differ_from")} <= set(roles) for rule in rules):
        raise PlaybookError(f"{path}: model_rules must name roles of the team")
    return data


def commit():
    try:
        return (location() / ".commit").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def roles():
    return list(manifest()["roles"])


def keys(role):
    """The profile rows for one playbook role: its name, or name1..nameN for several instances."""
    count = manifest()["roles"][role]["instances"]
    return [role] if count == 1 else [f"{role}{number}" for number in range(1, count + 1)]


def role_keys():
    return [key for role in roles() for key in keys(role)]


def role_of(key):
    """Map a profile row to its playbook role, or None for a row the playbook does not define."""
    for role in roles():
        if key in keys(role):
            return role
    return None


def profile_names():
    return list(manifest()["model_sets"])


@lru_cache(maxsize=None)
def model_set(name):
    rel = manifest()["model_sets"].get(name)
    if rel is None:
        raise PlaybookError("T3_BOAT_PROFILE must be one of: " + ", ".join(profile_names()))
    path = _inside(rel)
    data = _json(path)
    assignments = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(assignments, dict):
        raise PlaybookError(f"{path}: expected roles")
    for role in roles():
        entries = assignments.get(role)
        entries = [entries] if isinstance(entries, dict) else entries
        if (not isinstance(entries, list) or len(entries) != manifest()["roles"][role]["instances"]
                or not all(isinstance(e, dict) and e.get("provider") in PROVIDER_IDS
                           and all(isinstance(e.get(k), str) and e[k].strip() for k in ("family", "effort"))
                           for e in entries)):
            raise PlaybookError(f"{path}: role {role} needs one provider, family and effort per instance")
    return data


def _entries(name, role):
    entries = model_set(name)["roles"][role]
    return [entries] if isinstance(entries, dict) else entries


def providers(name):
    """The T3 provider instances a model set uses, in display order."""
    used = {entry["provider"] for role in roles() for entry in _entries(name, role)}
    return tuple(PROVIDER_IDS[p] for p in PROVIDER_IDS if p in used)


def profiles():
    return {name: providers(name) for name in profile_names()}


def templates(name):
    """Default rows for a model set: provider instance, model family and effort for every role instance."""
    rows = {}
    for role in roles():
        for key, entry in zip(keys(role), _entries(name, role)):
            rows[key] = {"provider": PROVIDER_IDS[entry["provider"]], "model": entry["family"].strip(),
                         "effort": entry["effort"].strip()}
    return rows


def rules(name):
    """Yield (row, other row, family allowed to be shared or None) for every model rule of a set."""
    exceptions = {}
    for exception in model_set(name).get("exceptions", []):
        role, number = exception.get("role"), exception.get("instance")
        if role in manifest()["roles"] and isinstance(number, int) and 1 <= number <= len(keys(role)):
            family = _entries(name, role)[number - 1]["family"].strip().casefold()
            exceptions[(keys(role)[number - 1], exception.get("must_differ_from"))] = family
    for rule in manifest().get("model_rules", []):
        other_role = rule["must_differ_from"]
        for role in rule["roles"]:
            for key in keys(role):
                for other in keys(other_role):
                    yield key, other, exceptions.get((key, other_role))


@lru_cache(maxsize=None)
def bundle(role):
    """The role's instruction text, assembled as the playbook's own `check.py --print` does."""
    parts = []
    for rel in manifest()["roles"][role]["bundle"]:
        path = _inside(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise PlaybookError(f"{path}: {error}") from None
        parts.append(f"<!-- file: {rel} -->\n{text if text.endswith(chr(10)) else text + chr(10)}")
    return "\n".join(parts)


def summary(role):
    """The first sentence of the role's `## Who you are` section, for a skill description."""
    first = manifest()["roles"][role]["bundle"][0]  # A bundle always starts with the role's ROLE.md.
    text = _inside(first).read_text(encoding="utf-8")
    match = re.search(r"^## Who you are\n+(.+?)(?:\n\n|\Z)", text, re.MULTILINE | re.DOTALL)
    sentence = re.split(r"(?<=[.!?])\s", " ".join(match[1].split()) if match else "", maxsplit=1)[0]
    return sentence[:300]
