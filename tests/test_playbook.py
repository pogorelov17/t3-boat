import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import playbook
from packages import CUSTOM_ROLES, write_package


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "pkg"

    def test_custom_team_keeps_its_names_counts_and_bundle_order(self):
        package = playbook.load(write_package(self.root))
        self.assertEqual(package.name, "harbor-crew")
        self.assertEqual({role: spec["instances"] for role, spec in package.roles.items()}, CUSTOM_ROLES)
        text = package.bundle("rigger")
        self.assertEqual(text, "<!-- file: crew/rigger/ROLE.md -->\n" + (self.root / "crew/rigger/ROLE.md").read_text()
                         + "\n<!-- file: crew/rigger/how/steps.md -->\nSTEPS-rigger\n"
                         + "\n<!-- file: common/report.md -->\nCOMMON-REPORT\n")
        self.assertEqual(package.summary("qa_bot"), "You are the qa_bot.")
        self.assertIn("version 7.1.0", package.label())

    def test_model_sets_are_read_as_data_and_filtered_by_provider(self):
        package = playbook.load(write_package(self.root))
        self.assertEqual(package.fitting_sets(("codex",)), ["codex-crew"])
        self.assertEqual(package.fitting_sets(("claudeAgent",)), [])
        self.assertEqual(package.fitting_sets(("codex", "claudeAgent")), ["both-providers", "codex-crew"])
        rigger = package.model_set("both-providers")["roles"]["rigger"]
        self.assertEqual(len(rigger), 3)
        self.assertEqual(rigger[2], {"provider": "claudeAgent", "family": "haiku", "effort": "medium"})
        # A set need not cover every role; that is the package's own rule to check.
        self.assertNotIn("qa_bot", package.model_set("both-providers")["roles"])
        self.assertEqual(package.entry("both-providers", "harbor-master")["family"], "opus")
        self.assertIsNone(package.entry("both-providers", "qa_bot"))

    def test_a_package_without_model_sets_or_rules_is_valid(self):
        package = playbook.load(write_package(self.root, roles={"solo": 1}, sets={}))
        self.assertEqual(package.model_set_paths, {})
        self.assertEqual(package.model_rules, [])
        self.assertEqual(package.fitting_sets(("codex",)), [])

    def test_malformed_packages_name_the_file(self):
        cases = {
            "relative": lambda: playbook.load("pkg"),
            "missing": lambda: playbook.load(str(self.root / "absent")),
        }
        for label, call in cases.items():
            with self.subTest(label), self.assertRaises(playbook.PlaybookError):
                call()
        write_package(self.root)
        manifest = json.loads((self.root / "team.json").read_text())
        broken = {
            "schema": dict(manifest, schema=2),
            "no roles": dict(manifest, roles={}),
            "zero instances": dict(manifest, roles={"a": {"instances": 0, "bundle": ["common/report.md"]}}),
            "bool instances": dict(manifest, roles={"a": {"instances": True, "bundle": ["common/report.md"]}}),
            "spaced name": dict(manifest, roles={"first mate": {"instances": 1, "bundle": ["common/report.md"]}}),
            "null byte": dict(manifest, roles={"a": {"instances": 1, "bundle": ["common/\u0000.md"]}}),
            "long path": dict(manifest, roles={"a": {"instances": 1, "bundle": ["x" * 5000]}}),
            "directory": dict(manifest, roles={"a": {"instances": 1, "bundle": ["common"]}}),
            "sets": dict(manifest, model_sets={"a": ["x"]}),
            "outside": dict(manifest, roles={"a": {"instances": 1, "bundle": ["../secret.md"]}}),
            "absolute": dict(manifest, roles={"a": {"instances": 1, "bundle": ["/etc/passwd"]}}),
            "absent file": dict(manifest, roles={"a": {"instances": 1, "bundle": ["nope.md"]}}),
            "rules": dict(manifest, model_rules={"x": 1}),
        }
        for label, data in broken.items():
            with self.subTest(label):
                (self.root / "team.json").write_text(json.dumps(data))
                with self.assertRaisesRegex(playbook.PlaybookError, "team.json"):
                    playbook.load(str(self.root))

    def test_symlinked_bundle_leaving_the_package_is_refused(self):
        write_package(self.root, roles={"a": 1}, sets={})
        outside = self.root.parent / "outside.md"
        outside.write_text("secret\n")
        (self.root / "crew/a/ROLE.md").unlink()
        (self.root / "crew/a/ROLE.md").symlink_to(outside)
        with self.assertRaisesRegex(playbook.PlaybookError, "outside the package"):
            playbook.load(str(self.root))

    def test_malformed_model_set_names_its_file(self):
        write_package(self.root)
        entry = {"provider": "codex", "family": "luna", "effort": "low"}
        broken = {
            "provider": {"roles": {"rigger": {"provider": "x"}}},
            "provider list": {"roles": {"rigger": dict(entry, provider=["codex"])}},
            "provider object": {"roles": {"rigger": [dict(entry, provider={"codex": 1})]}},
            "family number": {"roles": {"rigger": dict(entry, family=6)}},
            "exceptions": {"roles": {"rigger": entry}, "exceptions": {"rigger": 1}},
            "no roles": {"roles": []},
        }
        for label, data in broken.items():
            with self.subTest(label):
                (self.root / "assignments/codex-crew.json").write_text(json.dumps(data))
                with self.assertRaisesRegex(playbook.PlaybookError, "codex-crew.json"):
                    playbook.load(str(self.root))

    def test_every_file_is_read_as_utf8_text_when_the_package_is_loaded(self):
        write_package(self.root)
        cases = {
            "team.json": b'{"schema": 1, "name": "\xff"}',
            "crew/cartographer/how/steps.md": b"STEPS \xc3\x28\n",
            "assignments/codex-crew.json": b'{"roles": {"rigger": {"provider": "codex", "family": "\xfe"}}}',
        }
        for rel, data in cases.items():
            with self.subTest(rel):
                write_package(self.root)
                (self.root / rel).write_bytes(data)
                with self.assertRaisesRegex(playbook.PlaybookError, f"{rel}: not UTF-8 text"):
                    playbook.load(str(self.root))

    def test_unreadable_and_deeply_nested_files_are_named(self):
        write_package(self.root)
        (self.root / "assignments/codex-crew.json").write_text("[" * 100000)
        with self.assertRaisesRegex(playbook.PlaybookError, "codex-crew.json: nested too deeply"):
            playbook.load(str(self.root))
        write_package(self.root)
        if os.geteuid() != 0:
            (self.root / "common/report.md").chmod(0)
            with self.assertRaisesRegex(playbook.PlaybookError, "report.md: cannot be read: Permission denied"):
                playbook.load(str(self.root))

    def test_size_limits_are_checked_when_the_package_is_read(self):
        write_package(self.root, roles={"long": 1}, sets={})
        # A long guide that is still within the limits loads in full.
        (self.root / "crew/long/ROLE.md").write_text("# long\n" + "x" * 71690 + "\n")
        self.assertGreater(len(playbook.load(str(self.root)).bundle("long").encode()), 71698)
        (self.root / "crew/long/ROLE.md").write_text("x" * (playbook.MAX_FILE + 1))
        with self.assertRaisesRegex(playbook.PlaybookError, "ROLE.md: larger than 1048576 bytes"):
            playbook.load(str(self.root))
        half = "y" * (playbook.MAX_FILE // 2 + 1)
        (self.root / "crew/long/ROLE.md").write_text(half)
        (self.root / "crew/long/how/steps.md").write_text(half)
        with self.assertRaisesRegex(playbook.PlaybookError, "the long guide is [0-9]+ bytes, over the 1048576-byte"):
            playbook.load(str(self.root))

    def test_any_valid_role_names_get_distinct_skill_names_outside_the_boat_skill(self):
        package = playbook.load(write_package(self.root, name="t3", roles={"boat": 1, "qa_bot": 1, "QA-bot": 1,
                                                                            "Q.A.bot": 1}, sets={}))
        names = [playbook.skill_name(package.name, role) for role in package.roles]
        self.assertEqual(len(set(names)), 4)
        self.assertNotIn("t3-boat", names)
        long = playbook.skill_name("p" * 64, "R" * 64)
        for name in (*names, long):
            self.assertRegex(name, r"^playbook-[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertLessEqual(len(name), 64)

    def test_a_package_edited_while_it_is_read_is_read_again(self):
        write_package(self.root)
        original = playbook.Package._parse
        steps = self.root / "crew/rigger/how/steps.md"
        edits = []

        def parse_then_edit(package):
            original(package)
            if len(edits) < 1:  # The owner saves a file while T3 Boat reads the package.
                edits.append(1)
                steps.write_text("EDITED\n")

        with unittest.mock.patch.object(playbook.Package, "_parse", parse_then_edit):
            package = playbook.load(str(self.root))
        self.assertIn("EDITED", package.bundle("rigger"))

        def always_edit(package):
            original(package)
            steps.write_text(steps.read_text() + "more\n")

        with unittest.mock.patch.object(playbook.Package, "_parse", always_edit), \
                self.assertRaisesRegex(playbook.PlaybookError, "kept changing while it was read"):
            playbook.load(str(self.root))

    def test_the_loaded_package_does_not_read_its_files_again(self):
        package = playbook.load(write_package(self.root))
        bundle = package.bundle("rigger")
        (self.root / "crew/rigger/ROLE.md").unlink()
        (self.root / "assignments/codex-crew.json").write_text("{")
        self.assertEqual(package.bundle("rigger"), bundle)
        self.assertEqual(package.summary("rigger"), "You are the rigger.")
        self.assertEqual(package.entry("codex-crew", "rigger")["family"], "luna")


BUNDLED = Path(os.environ.get("T3_BOAT_PLAYBOOK") or playbook.BUNDLED)


@unittest.skipUnless((BUNDLED / "team.json").is_file(), "fetch the pinned playbook with scripts/fetch-playbook playbook")
class BundledPackageTests(unittest.TestCase):
    """The real agent-playbook package, read without changes to it or to T3 Boat."""

    def test_every_bundle_matches_the_package_checker_output(self):
        package = playbook.load(str(BUNDLED.resolve()))
        manifest = json.loads((BUNDLED / "team.json").read_text())
        self.assertEqual(list(package.roles), list(manifest["roles"]))
        for role in package.roles:
            with self.subTest(role=role):
                printed = subprocess.run([sys.executable, "-I", str(BUNDLED / "scripts/check.py"), "--root",
                                          str(BUNDLED), "--print", role], capture_output=True, text=True, check=True)
                self.assertEqual(package.bundle(role).rstrip("\n"), printed.stdout.rstrip("\n"))

    def test_model_sets_fit_the_profiles_they_name(self):
        package = playbook.load(str(BUNDLED.resolve()))
        providers = {"codex-only": ("codex",), "claude-only": ("claudeAgent",),
                     "codex+claude": ("codex", "claudeAgent")}
        for name, enabled in providers.items():
            if name in package.model_set_paths:
                self.assertIn(name, package.fitting_sets(enabled))


if __name__ == "__main__":
    unittest.main()
