"""Read an external playbook package that the owner attached in setup.

A playbook is a directory with a `team.json` manifest (schema 1, the format of agent-playbook): its roles, the
ordered instruction files ("bundle") of each role, how many instances of each it runs, and optional model sets.
T3 Boat reads the package as data and never runs code from it. Role names, counts, model assignments, model
rules and every instruction belong to the package; nothing here knows a role by name.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The pinned agent-playbook copy built into the image (scripts/fetch-playbook); setup offers it as "bundled".
BUNDLED = ROOT / "playbook"
# Package provider names and the T3 provider instance each one runs on, in display order.
PROVIDER_IDS = {"codex": "codex", "claude": "claudeAgent"}
# Role and package names: anything a shell argument and a file name can carry unquoted. Skill folders use slug().
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_/+-]*")
# agent-playbook's schema 1 calls the role that talks to the owner `lead`; setup suggests it for the T3 team lead.
# The manifest itself does not mark that role, so setup asks which role the lead follows, if any.
OWNER_ROLE = "lead"


def slug(name):
    """A skill-safe form of a name: lowercase letters, digits and hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


class PlaybookError(ValueError):
    """The attached package is missing or malformed; the message names the file."""


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlaybookError(f"{path} is missing") from None
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise PlaybookError(f"{path}: {error}") from None


def _entries(value):
    return [value] if isinstance(value, dict) else value


class Package:
    """One validated package directory. Every path a manifest names must stay inside it."""

    def __init__(self, path):
        self.root = Path(path)
        if not self.root.is_absolute():
            raise PlaybookError(f"{path}: enter an absolute directory path")
        if not self.root.is_dir():
            raise PlaybookError(f"{path} is not a directory")
        self.base = self.root.resolve()
        manifest_path = self.root / "team.json"
        data = _json(manifest_path)
        self.manifest_path = manifest_path
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise PlaybookError(f"{manifest_path}: expected a schema 1 team description")
        roles = data.get("roles")
        if not isinstance(roles, dict) or not roles:
            raise PlaybookError(f"{manifest_path}: expected a non-empty roles object")
        self.roles = {}
        for role, spec in roles.items():
            count = spec.get("instances") if isinstance(spec, dict) else None
            bundle = spec.get("bundle") if isinstance(spec, dict) else None
            if (not isinstance(role, str) or not NAME.fullmatch(role) or not isinstance(count, int)
                    or isinstance(count, bool) or count < 1 or not isinstance(bundle, list) or not bundle
                    or not all(isinstance(rel, str) for rel in bundle)):
                raise PlaybookError(f"{manifest_path}: role {role!r} needs a name of letters, digits, '.', '_' or "
                                    "'-', a positive instances count and a non-empty bundle of file paths")
            for rel in bundle:
                self._file(rel)
            self.roles[role] = {"instances": count, "bundle": list(bundle)}
        slugs = {}
        for role in self.roles:
            if slug(role) in slugs:
                raise PlaybookError(f"{manifest_path}: roles {slugs[slug(role)]!r} and {role!r} share the skill "
                                    f"name {slug(role)!r}; rename one")
            slugs[slug(role)] = role
        name = data.get("name")
        self.name = name if isinstance(name, str) and NAME.fullmatch(name) else "playbook"
        version = data.get("version")
        self.version = version if isinstance(version, str) and TEXT.fullmatch(version) else None
        try:
            commit = (self.root / ".commit").read_text().strip()
        except OSError:
            commit = ""
        self.commit = commit if re.fullmatch(r"[0-9a-f]{7,64}", commit) else None
        rules = data.get("model_rules", [])
        if not isinstance(rules, list):
            raise PlaybookError(f"{manifest_path}: model_rules must be a list")
        self.model_rules = rules
        sets = data.get("model_sets", {})
        if not isinstance(sets, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in sets.items()):
            raise PlaybookError(f"{manifest_path}: model_sets must map names to file paths")
        self.model_set_paths = dict(sets)
        self._sets = {}
        for set_name in self.model_set_paths:  # Name a malformed set when the package is attached, not later.
            self.model_set(set_name)

    def _file(self, rel):
        """Resolve a manifest path to a readable file inside the package; anything else names the manifest."""
        path = (self.base / rel).resolve() if isinstance(rel, str) and rel and not rel.startswith("/") else None
        if path is None or self.base not in path.parents:
            raise PlaybookError(f"{self.manifest_path}: path {rel!r} is outside the package")
        if not path.is_file():
            raise PlaybookError(f"{self.manifest_path}: {rel} is not a file in the package")
        return path

    def label(self, where=True):
        """How setup, status and the skill name this package; `where` adds its directory on this server."""
        detail = ", ".join(part for part in (self.version and "version " + self.version,
                                              self.commit and "commit " + self.commit[:12]) if part)
        return f"{self.name}" + (f" ({detail})" if detail else "") + (f" at {self.root}" if where else "")

    def bundle(self, role):
        """A role's instruction text: its bundle files in order, each after a comment naming it, as the
        agent-playbook checker's `--print ROLE` assembles them."""
        parts = []
        for rel in self.roles[role]["bundle"]:
            path = self._file(rel)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise PlaybookError(f"{path}: {error}") from None
            parts.append(f"<!-- file: {rel} -->\n{text if text.endswith(chr(10)) else text + chr(10)}")
        return "\n".join(parts)

    def summary(self, role):
        """The first sentence under a `## Who you are` heading of the role's first file, when it has one."""
        try:
            text = self._file(self.roles[role]["bundle"][0]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, PlaybookError):
            return ""
        match = re.search(r"^## Who you are\n+(.+?)(?:\n\n|\Z)", text, re.MULTILINE | re.DOTALL)
        sentence = re.split(r"(?<=[.!?])\s", " ".join(match[1].split()) if match else "", maxsplit=1)[0]
        return sentence[:300]

    def model_set(self, name):
        """A model set's role assignments as {role: [entry, ...]}, each entry with provider, family and effort.

        Only the shape T3 Boat displays is checked; whether the set covers every role, and every rule between
        roles, is the package's own business.
        """
        if name in self._sets:
            return self._sets[name]
        if name not in self.model_set_paths:
            raise PlaybookError(f"{self.manifest_path}: no model set {name!r}")
        path = self._file(self.model_set_paths[name])
        data = _json(path)
        assignments = data.get("roles") if isinstance(data, dict) else None
        if not isinstance(assignments, dict) or not assignments:
            raise PlaybookError(f"{path}: expected roles")
        result = {}
        for role, value in assignments.items():
            entries = _entries(value)
            if (not isinstance(entries, list) or not entries or not all(
                    isinstance(e, dict) and e.get("provider") in PROVIDER_IDS
                    and all(isinstance(e.get(k), str) and TEXT.fullmatch(e[k].strip()) for k in ("family", "effort"))
                    for e in entries)):
                raise PlaybookError(f"{path}: role {role!r} needs a provider ({', '.join(PROVIDER_IDS)}), "
                                    "family and effort for each entry")
            result[role] = [{"provider": PROVIDER_IDS[e["provider"]], "family": e["family"].strip(),
                             "effort": e["effort"].strip()} for e in entries]
        exceptions = data.get("exceptions", [])
        self._sets[name] = {"roles": result, "exceptions": exceptions if isinstance(exceptions, list) else [],
                            "path": path}
        return self._sets[name]

    def set_providers(self, name):
        used = {e["provider"] for entries in self.model_set(name)["roles"].values() for e in entries}
        return tuple(p for p in PROVIDER_IDS.values() if p in used)

    def fitting_sets(self, enabled):
        """The model sets whose every provider is enabled in this T3 Boat profile."""
        return [name for name in self.model_set_paths if set(self.set_providers(name)) <= set(enabled)]

    def entry(self, name, role):
        """A model set's first entry for `role`, if the set assigns one."""
        entries = self.model_set(name)["roles"].get(role) if name and role else None
        return entries[0] if entries else None


def load(path):
    return Package(path)
