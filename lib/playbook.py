"""Read an external playbook package that the owner attached in setup.

A playbook is a directory with a `team.json` manifest (schema 1, the format of agent-playbook): its roles, the
ordered instruction files ("bundle") of each role, how many instances of each it runs, and optional model sets.
T3 Boat reads the package as data and never runs code from it. Role names, counts, model assignments, model
rules and every instruction belong to the package; nothing here knows a role by name.

Loading reads and checks every file the manifest names, in full, before anything is generated from it, and keeps
the contents in memory: every guide, skill and worker instruction made from one loaded package comes from the
same read, even while the owner edits the directory.
"""
import hashlib
import json
import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A copy of agent-playbook that an image built with BUNDLE_PLAYBOOK=1 contains (scripts/fetch-playbook); setup
# offers it as "bundled" only when it is there. The default image has none and never fetches one.
BUNDLED = ROOT / "playbook"
# Package provider names and the T3 provider instance each one runs on, in display order.
PROVIDER_IDS = {"codex": "codex", "claude": "claudeAgent"}
# Role and package names: anything a shell argument and a file name can carry unquoted. Skill folders use slug().
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_/+-]*")
# Size limits, in bytes, checked when the package is read and so before any file is generated or any VM created.
MAX_FILE = 1 << 20      # One file of the package.
MAX_GUIDE = 1 << 20     # One role's assembled guide, which a worker VM receives.
MAX_PACKAGE = 8 << 20   # Every file the manifest names, together.
READS = 3               # Attempts at reading an unchanged package while it is being edited.


def slug(name):
    """A skill-safe form of a name: lowercase letters, digits and hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


def skill_name(package_name, role):
    """A role guide's skill name: `playbook-PACKAGE-ROLE-HASH`. Generated guides share the `playbook-` prefix, so no
    package or role name can produce the Boat skill's own name (`t3-boat`). The readable parts are shortened slugs;
    the hash of the exact names keeps them distinct, so `qa_bot` and `QA-bot` get separate skills. The result has
    at most 64 lowercase letters, digits and single hyphens."""
    digest = hashlib.sha256(json.dumps([package_name, role]).encode()).hexdigest()[:8]
    return "-".join(("playbook", slug(package_name)[:16].strip("-"), slug(role)[:28].strip("-"), digest))


class PlaybookError(ValueError):
    """The attached package is missing or malformed; the message names the file."""


class NotRegularFile(OSError):
    """A path that is a FIFO, device, socket or directory where a regular file is expected."""


def read_regular(path, limit=-1):
    """Read up to `limit` bytes of a regular file; anything else raises NotRegularFile without blocking.

    The file is opened non-blocking and checked on the open descriptor, so a FIFO put at the path, even after an
    earlier check, is refused at once instead of waiting for a writer.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except BaseException:
        os.close(descriptor)
        raise
    if not regular:
        os.close(descriptor)
        raise NotRegularFile(f"{path} is not a regular file")
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit)


def _entries(value):
    return [value] if isinstance(value, dict) else value


def _text_field(entry, key):
    value = entry.get(key)
    return isinstance(value, str) and bool(TEXT.fullmatch(value.strip()))


class Package:
    """One validated package directory, read in full. Every path a manifest names must stay inside it."""

    def __init__(self, path):
        if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
            raise PlaybookError(f"{path}: enter an absolute directory path")
        self.root = Path(path)
        try:
            directory = self.root.is_dir()
            self.base = self.root.resolve() if directory else None
        except (OSError, ValueError, RuntimeError) as error:
            raise PlaybookError(f"{path}: {error}") from None
        if not directory:
            raise PlaybookError(f"{path} is not a directory")
        self.manifest_path = self.root / "team.json"
        for _ in range(READS):
            self._read = {}
            self._parse()
            if all(self._bytes(path, again=True) == data for path, data in self._read.items()):
                break
        else:
            raise PlaybookError(f"{self.manifest_path}: the package kept changing while it was read; "
                                "finish editing it and try again")
        del self._read

    # Reading. Every file is read once per attempt, then read again to confirm nothing changed in between.

    def _bytes(self, path, again=False):
        if not again and path in self._read:
            return self._read[path]
        try:
            data = read_regular(path, MAX_FILE + 1)
        except FileNotFoundError:
            if again:
                return None
            raise PlaybookError(f"{path} is missing") from None
        except NotRegularFile:
            if again:
                return None
            raise PlaybookError(f"{path} is not a regular file") from None
        except (OSError, ValueError) as error:
            if again:
                return None
            raise PlaybookError(f"{path}: cannot be read: {getattr(error, 'strerror', None) or error}") from None
        if again:
            return data
        if len(data) > MAX_FILE:
            raise PlaybookError(f"{path}: larger than {MAX_FILE} bytes, the limit for one package file")
        if sum(map(len, self._read.values())) + len(data) > MAX_PACKAGE:
            raise PlaybookError(f"{self.manifest_path}: the files it names exceed {MAX_PACKAGE} bytes together")
        self._read[path] = data
        return data

    def _text(self, path):
        data = self._bytes(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PlaybookError(f"{path}: not UTF-8 text ({error})") from None
        if "\0" in text:  # A shell script, and so a worker's bootstrap, cannot carry a NUL byte.
            raise PlaybookError(f"{path}: contains a NUL byte at offset {data.index(0)}; package files must be text")
        return text

    def _json(self, path):
        text = self._text(path)  # Its own errors already name the file.
        try:
            data = json.loads(text)
            # JSON escapes can decode to a lone surrogate even when the source file is valid UTF-8.
            json.dumps(data, ensure_ascii=False).encode("utf-8")
            return data
        except UnicodeEncodeError:
            raise PlaybookError(f"{path}: JSON contains an unpaired Unicode surrogate") from None
        except (ValueError, RecursionError) as error:
            raise PlaybookError(f"{path}: {error if isinstance(error, ValueError) else 'nested too deeply'}") from None

    def _file(self, rel):
        """Resolve a manifest path to a regular file inside the package; anything else names the manifest."""
        try:
            path = (self.base / rel).resolve() if isinstance(rel, str) and rel and not rel.startswith("/") else None
            inside = path is not None and self.base in path.parents
            regular = inside and path.is_file()
        except (OSError, ValueError, RuntimeError) as error:
            raise PlaybookError(f"{self.manifest_path}: path {rel!r} cannot be resolved: "
                                f"{getattr(error, 'strerror', None) or error}") from None
        if not inside:
            raise PlaybookError(f"{self.manifest_path}: path {rel!r} is outside the package")
        if not regular:
            raise PlaybookError(f"{self.manifest_path}: {rel} is not a file in the package")
        return path

    def _parse(self):
        manifest_path = self.manifest_path
        data = self._json(manifest_path)
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise PlaybookError(f"{manifest_path}: expected a schema 1 team description")
        roles = data.get("roles")
        if not isinstance(roles, dict) or not roles:
            raise PlaybookError(f"{manifest_path}: expected a non-empty roles object")
        self.roles, self._bundles, self._summaries = {}, {}, {}
        for role, spec in roles.items():
            count = spec.get("instances") if isinstance(spec, dict) else None
            bundle = spec.get("bundle") if isinstance(spec, dict) else None
            if (not NAME.fullmatch(role) or not isinstance(count, int) or isinstance(count, bool) or count < 1
                    or not isinstance(bundle, list) or not bundle or not all(isinstance(rel, str) for rel in bundle)):
                raise PlaybookError(f"{manifest_path}: role {role!r} needs a name of letters, digits, '.', '_' or "
                                    "'-', a positive instances count and a non-empty bundle of file paths")
            self.roles[role] = {"instances": count, "bundle": list(bundle)}
            texts = [self._text(self._file(rel)) for rel in bundle]
            self._bundles[role] = "\n".join(f"<!-- file: {rel} -->\n{text if text.endswith(chr(10)) else text + chr(10)}"
                                            for rel, text in zip(bundle, texts))
            size = len(self._bundles[role].encode())
            if size > MAX_GUIDE:
                raise PlaybookError(f"{manifest_path}: the {role} guide is {size} bytes, over the {MAX_GUIDE}-byte "
                                    "limit for what one worker receives; shorten its bundle")
            match = re.search(r"^## Who you are\n+(.+?)(?:\n\n|\Z)", texts[0], re.MULTILINE | re.DOTALL)
            sentence = re.split(r"(?<=[.!?])\s", " ".join(match[1].split()) if match else "", maxsplit=1)[0]
            self._summaries[role] = sentence[:300]
        name = data.get("name")
        self.name = name if isinstance(name, str) and NAME.fullmatch(name) else "playbook"
        skills = {}
        for role in self.roles:
            other = skills.setdefault(skill_name(self.name, role), role)
            if other != role:  # A hash collision: vanishingly rare, but never install one guide over another.
                raise PlaybookError(f"{manifest_path}: roles {other!r} and {role!r} share a skill name; rename one")
        version = data.get("version")
        self.version = version if isinstance(version, str) and TEXT.fullmatch(version) else None
        try:
            commit = read_regular(self.root / ".commit", 128).decode("ascii", errors="replace").strip()
        except (OSError, ValueError):
            commit = ""
        self.commit = commit if re.fullmatch(r"[0-9a-f]{7,64}", commit) else None
        rules = data.get("model_rules", [])
        if not isinstance(rules, list):
            raise PlaybookError(f"{manifest_path}: model_rules must be a list")
        self.model_rules = rules
        sets = data.get("model_sets", {})
        if not isinstance(sets, dict) or not all(isinstance(v, str) for v in sets.values()):
            raise PlaybookError(f"{manifest_path}: model_sets must map names to file paths")
        self.model_set_paths = dict(sets)
        self._sets = {name: self._model_set(name, rel) for name, rel in sets.items()}

    def _model_set(self, name, rel):
        """A model set's role assignments as {role: [entry, ...]}, each entry with provider, family and effort.

        Only the shape T3 Boat displays is checked; whether the set covers every role, and every rule between
        roles, is the package's own business.
        """
        path = self._file(rel)
        data = self._json(path)
        assignments = data.get("roles") if isinstance(data, dict) else None
        if not isinstance(assignments, dict) or not assignments:
            raise PlaybookError(f"{path}: expected roles")
        result = {}
        for role, value in assignments.items():
            entries = _entries(value)
            if (not isinstance(entries, list) or not entries or not all(
                    isinstance(e, dict) and isinstance(e.get("provider"), str) and e["provider"] in PROVIDER_IDS
                    and _text_field(e, "family") and _text_field(e, "effort") for e in entries)):
                raise PlaybookError(f"{path}: role {role!r} needs a provider ({', '.join(PROVIDER_IDS)}), "
                                    "family and effort for each entry")
            result[role] = [{"provider": PROVIDER_IDS[e["provider"]], "family": e["family"].strip(),
                             "effort": e["effort"].strip()} for e in entries]
        exceptions = data.get("exceptions", [])
        if not isinstance(exceptions, list):
            raise PlaybookError(f"{path}: exceptions must be a list")
        return {"roles": result, "exceptions": exceptions, "path": path}

    # The loaded package, from memory only.

    def label(self, where=True):
        """How setup, status and the skill name this package; `where` adds its directory on this server."""
        detail = ", ".join(part for part in (self.version and "version " + self.version,
                                              self.commit and "commit " + self.commit[:12]) if part)
        return f"{self.name}" + (f" ({detail})" if detail else "") + (f" at {self.root}" if where else "")

    def bundle(self, role):
        """A role's instruction text: its bundle files in order, each after a comment naming it, as the
        agent-playbook checker's `--print ROLE` assembles them."""
        return self._bundles[role]

    def summary(self, role):
        """The first sentence under a `## Who you are` heading of the role's first file, when it has one."""
        return self._summaries[role]

    def model_set(self, name):
        if name not in self._sets:
            raise PlaybookError(f"{self.manifest_path}: no model set {name!r}")
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
