"""The pinned release of the suggested playbook, and an offline check of a folder against it.

The pin is the commit ID, the ID of that commit's file tree, and a table of every file in that tree: its git
mode, git blob ID and SHA-256. Git IDs name their content, so the table is bound to the commit: the tree ID
computed from the table must equal TREE, and a download checks that the fetched commit's tree is TREE. A folder
holds the pinned release only when it contains exactly the table's files, each a regular file (no link anywhere)
whose bytes have both the listed blob ID and the listed SHA-256, plus the `.commit` file naming the commit. A
`.commit` file alone proves nothing: anyone can write one.
"""
import hashlib
import os
import stat
from pathlib import Path
from typing import NamedTuple

URL = "https://github.com/pogorelov17/agent-playbook"
COMMIT = "a27b16a7d6962df7b26fc3233b8503f973f8d715"  # The same commit scripts/fetch-playbook pins.
TREE = "d599d143e98920ac96274965fffedf2a8cf5c0ee"
FILES = {  # Path: (git mode, git blob ID, SHA-256) of every file in TREE.
    ".github/workflows/check.yml": ("100644", "d12fe9d26811d09e8960d49d490fb898991773bb", "a07e14fbcac2cb6207ea6de0ce048fc570d5cd225419155017c97e52b9968e85"),
    ".gitignore": ("100644", "7a60b85e148f80966a550e5ab6a762a907c69ca6", "862263fa1f46c20f0d1e4dac5ffcc75abd55c08211b2c3864c5f8764b9d87793"),
    "LICENSE": ("100644", "907d38ee66f8c8502863f09989be23e58cbff98e", "e8fda3b053dd61e39eec5c5d6bb58b8e2b13aca4ca103ebb9450a4e74a765336"),
    "README.md": ("100644", "833b94d107c2fd998b9d144c650c8330953e9a24", "ff5c28a998d17fa100072d3388eb7ee6049d2ef0de4240e7f120389076bdbd86"),
    "models/claude-only.json": ("100644", "730c2018b4e85bcdb36f2c6470bb392f9730be8b", "a738f744426b2515808a8726f4c27034ef4ca0d563705dcaf725ec111dcac674"),
    "models/codex+claude.json": ("100644", "0ff753ab887afc1dc30c1319aa066b66609f18cc", "b65b36751caa33c148ac6ef103f9ad7881491161fc1b44f71ce6f9abd18c8906"),
    "models/codex-only.json": ("100644", "60bea44a4f22f9e7fae1d38c1f4c3b3de9bbd6a6", "2850c2e545d2b404a76d366ded580160d216607a87b87beb1b27fc722f356063"),
    "roles/arbiter/ROLE.md": ("100644", "a7de5591a612ea313d49f7678411f8c33e16dfb5", "31aab24db21cb918baf5a8b364bae2bd172d80303a87dd36ea79bbcbb686c71d"),
    "roles/executor/ROLE.md": ("100644", "97461a4dd5324f223fc2b71e7047c81ebf80773f", "f0590bda28eef9d91db1425842bd4cdddc43f65423069e78985780061ef7900c"),
    "roles/executor/skills/following-plan.md": ("100644", "a46e8541c44d95b6cfcdce728eafad01f3f83f81", "39f1e405d67edfcb6960e4f05aab0638c10cafda349b1c8fded9e106bf0b04b5"),
    "roles/executor/skills/handling-review.md": ("100644", "64a40dba51481e8184891e1cae7a1a15bc2f4b53", "d6dcc1a96c2a18c422ed2f7012ff860e1e70a1e66330ea9648d3e47a4d7fe736"),
    "roles/executor/skills/tests-first.md": ("100644", "0dcc102d277f86f0b1480b259f44e5e9e16f43d5", "feb9e377a8e4473b30bb0185bfe59359fdeb16f8cbf0bf8c42262a36ab528a5c"),
    "roles/lead/ROLE.md": ("100644", "5cf377455d63b6a924a26d3e5dd22a4f72d3e128", "cf58ab7e120753174570410e8eface42ce651c9cde54fc08bfb11bee91e7afd3"),
    "roles/lead/skills/briefing.md": ("100644", "d57c69ca0e91a851602e1f81db81545c52e3578e", "1a502cc03893cc01a7e0d87049d1d1f275519d670e127e6053b77456748301e8"),
    "roles/lead/skills/clarifying.md": ("100644", "04ab2264cbe5b5e44f8413f255be028aec1a5c56", "3471fa3e88c459b5eced9730c7f1459a03822e3b0da7444dc8d53db5e01ea601"),
    "roles/lead/skills/writing-plan.md": ("100644", "c1f190959b4ced1210b04f6d5d73e020a6f9ba06", "3e676cdc64101d08161fbdf511316a1e47eb44bc247daae6e5db062a83765dea"),
    "roles/lead/skills/writing-spec.md": ("100644", "b1a2124c9997f46f495f237b7793a56a8a8e0130", "02373b210ce08c7b6bbf78bc6e67ba4c14d4be09b8b39012a7f64976a942c65e"),
    "roles/researcher/ROLE.md": ("100644", "91b9213f7d064fc659056829d3c9a77201fa407d", "14d1f9cdb0ac6f4e0f472ec7b2661ddd53550abe1b3fed5320f11a77c6be8c3d"),
    "roles/researcher/skills/researching.md": ("100644", "0d7331730015ffbf574486805512904956a2885c", "6329a0edb9958aa5840449ae9d2b024aad81f813f678942b956951249a862d90"),
    "roles/reviewer/ROLE.md": ("100644", "93a5ab488db445a72b43a93acffc0a50aa6400d5", "1b3b37a3a6d39ea75002d0160f77a4f2b872a3a12afb0221a3f5d73f57b74d11"),
    "roles/reviewer/skills/reviewing.md": ("100644", "a622164aedb158882ddfc77db88507d04ccb34c1", "df12a7900dd4c9bbb3a063da0675bf5f112b7441c6e8e18157f3f9a862fdcb88"),
    "roles/scout/ROLE.md": ("100644", "c3f9cf12c5aae4461add218152328e90de40a979", "672b4642a7a7196269b4c309d8f16544aaa9f92a990f0b5f18b156a0c28e8cca"),
    "roles/scout/skills/exploring.md": ("100644", "be1982ae1af9974aad75f1c3204a4b6e15842850", "e3e3e5e1e2b75b7d470cbbc5f8d007768aa51f204709e6424851269a7e23d8ab"),
    "scripts/check.py": ("100644", "5ff67ede9dcc81bf9101fb4a0692606c7a6fb308", "ce362abd1e0affc3e762d10ef850580254ce772c284085c8f083fe777776ab8b"),
    "shared/debugging.md": ("100644", "d53d43c902900c9cd0e42fb3e797db7109b0e945", "b731380d393ddbf745eec9efb7aabef31bfefb2b4dc5ae18b8b3f89cea1b1742"),
    "shared/reporting.md": ("100644", "00169738783a5d331e7719371a14163bf68e99df", "93f29c563960d6609d0627f310d386b49684b7823ef3f7ee54b502e2a81ebc17"),
    "shared/verification.md": ("100644", "aff50dd991e1ba263a7f7e32445b11f8eef4d8fb", "27055b1b8406361d029dfbeae1362e29824e6f65c1631f74c7c5f8fb20abbcac"),
    "team.json": ("100644", "41de902219deb0bc1212310b2fd9f772579ac67a", "dcbaeca6db7919a8b49a3a8be0d230bed859e4a23f9a2f40b3923035f6b537d6"),
    "tests/test_check.py": ("100644", "9160c233ba8c2ffacd1a7a415a252d07f56ed8f7", "724145a1972224f58310cd8dc3b6461604a46d35cd0071be54da554b38ff32f0"),
}
MAX_BYTES = 64 << 20  # Of a folder checked against a pin; reading stops there.
MAX_ENTRIES = 10000
SHOWN = 5  # Differences named in one message.


class Pin(NamedTuple):
    commit: str
    tree: str
    files: dict


PIN = Pin(COMMIT, TREE, FILES)


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_id(files):
    """The git tree ID of a table of files, computed as git does."""
    def build(entries):
        rows, folders = [], {}
        for parts, (mode, blob) in entries.items():
            if len(parts) == 1:
                rows.append((parts[0].encode(), mode.encode(), bytes.fromhex(blob)))
            else:
                folders.setdefault(parts[0], {})[parts[1:]] = (mode, blob)
        rows += [(name.encode(), b"40000", bytes.fromhex(build(sub))) for name, sub in folders.items()]
        # Git sorts a folder's entries by name, comparing a folder's name as if it ended with "/".
        rows.sort(key=lambda row: row[0] + (b"/" if row[1] == b"40000" else b""))
        body = b"".join(mode + b" " + name + b"\0" + raw for name, mode, raw in rows)
        return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()
    return build({tuple(path.split("/")): (mode, blob) for path, (mode, blob, _) in files.items()})


def _read(path):
    """A regular file's bytes, refusing a link or anything else at the path without blocking."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"{path} is not a regular file")
        return handle.read(MAX_BYTES + 1)


def check(root, pin=None):
    """Compare the folder `root` with a pin. Returns (differences, digests): each difference is a sentence naming
    a path relative to `root`, and digests maps each file that matches to its SHA-256. No difference means the
    folder holds exactly the pinned release."""
    pin = pin or PIN
    if tree_id(pin.files) != pin.tree:
        return [f"the file table of commit {pin.commit[:12]} does not hash to its tree {pin.tree[:12]}"], {}
    root = Path(root)
    differences, digests, seen, total = [], {}, set(), 0
    folders = {"/".join(path.split("/")[:end]) for path in pin.files for end in range(1, path.count("/") + 1)}
    try:
        if root.is_symlink() or not root.is_dir():
            return [f"{root} is not a folder"], {}
    except OSError as error:
        return [f"{root}: {error.strerror or error}"], {}
    pending = [""]
    while pending:
        folder = pending.pop()
        try:
            entries = sorted(os.scandir(root / folder), key=lambda entry: entry.name)
        except OSError as error:
            differences.append(f"{folder or '.'} cannot be read: {error.strerror or error}")
            continue
        for entry in entries:
            rel = f"{folder}/{entry.name}" if folder else entry.name
            if len(seen) >= MAX_ENTRIES or total > MAX_BYTES:
                return differences + [f"it holds more than the pinned release ({MAX_ENTRIES} entries or "
                                      f"{MAX_BYTES} bytes)"], digests
            seen.add(rel)
            try:
                kind = entry.stat(follow_symlinks=False).st_mode
            except OSError as error:
                differences.append(f"{rel} cannot be read: {error.strerror or error}")
                continue
            if stat.S_ISLNK(kind):
                differences.append(f"{rel} is a link")
            elif stat.S_ISDIR(kind):
                if rel in folders:
                    pending.append(rel)
                else:
                    differences.append(f"{rel}/ was added")
            elif rel == ".commit":
                try:
                    if _read(entry.path) != (pin.commit + "\n").encode():
                        differences.append(f".commit does not name commit {pin.commit[:12]}")
                except OSError as error:
                    differences.append(f".commit cannot be read: {error.strerror or error}")
            elif rel not in pin.files:
                differences.append(f"{rel} was added")
            else:
                try:
                    data = _read(entry.path)
                except OSError as error:
                    differences.append(f"{rel} is not a readable regular file: {error.strerror or error}")
                    continue
                total += len(data)
                digest = hashlib.sha256(data).hexdigest()
                _, blob, expected = pin.files[rel]
                if blob_id(data) != blob or digest != expected:
                    differences.append(f"{rel} differs from the pinned commit")
                else:
                    digests[rel] = digest
    differences += [f"{rel} is missing" for rel in sorted(set(pin.files) - seen)]
    if ".commit" not in seen:
        differences.append(".commit is missing")
    return differences, digests


def summary(differences):
    shown = "; ".join(differences[:SHOWN])
    return shown + (f"; and {len(differences) - SHOWN} more" if len(differences) > SHOWN else "")
