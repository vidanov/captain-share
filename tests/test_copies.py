"""Managed copies where symbolic links are unavailable (Windows without Developer
Mode, ADR 018). CAPTAIN_SHARE_LINKS=0 makes any system behave that way."""

import os
import unittest
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from tests.test_sharing import SharingFixture


class CopyFixture(SharingFixture):
    def setUp(self):
        super().setUp()
        self.links = patch.dict(os.environ, {"CAPTAIN_SHARE_LINKS": "0"})
        self.links.start()

    def tearDown(self):
        self.links.stop()
        super().tearDown()


class ManagedCopyTests(CopyFixture):
    def test_selection_installs_a_tracked_copy_that_sync_keeps_current(self):
        self.select()
        planned = e.plan(self.repo)
        self.assertEqual([x["op"] for x in planned["changes"]], ["copy"])
        e.apply(planned)
        dest = self.repo / ".kiro/skills/hello"
        self.assertTrue(dest.is_dir())
        self.assertFalse(dest.is_symlink())
        row = e.state(self.repo)["inbound"][".kiro/skills/hello"]
        self.assertEqual(row["copy"], e.snapshot(dest))
        self.assertTrue(e.status(self.repo)["ok"])
        found = e.scan(self.repo)
        self.assertEqual([(r["status"], r.get("copy")) for r in found], [("linked", True)])
        self.assertEqual(e.catalog(self.repo, "team")[0]["destinations"][0]["state"], "installed")
        self.assertEqual(e.plan(self.repo)["changes"], [])

        # The library changes: the next sync archives the old copy and writes the new one.
        (self.lib / "ip/skills/hello/SKILL.md").write_text("---\nname: hello\n---\nNew text")
        planned = e.plan(self.repo)
        self.assertEqual([x["op"] for x in planned["changes"]], ["archive", "copy"])
        e.apply(planned)
        self.assertIn("New text", (dest / "SKILL.md").read_text())
        self.assertTrue(e.status(self.repo)["ok"])
        self.assertEqual(len(e.archives(self.repo)), 1)

        c.select(self.repo, "inbound", "team", "skills", "hello", enabled=False)
        e.apply(e.plan(self.repo))
        self.assertFalse(dest.exists())
        self.assertEqual(e.state(self.repo)["inbound"], {})
        self.assertEqual(len(e.archives(self.repo)), 2)

    def test_an_edited_copy_is_drift_and_sync_refuses_to_replace_it(self):
        self.select()
        e.apply(e.plan(self.repo))
        dest = self.repo / ".kiro/skills/hello/SKILL.md"
        dest.write_text("my local edit")
        self.assertEqual(e.status(self.repo)["installed"][0]["status"], "drift")
        planned = e.plan(self.repo)
        self.assertFalse(planned["can_apply"])
        self.assertIn("Managed link changed", " ".join(planned["errors"]))
        self.assertEqual(dest.read_text(), "my local edit")

    def test_every_enabled_tool_gets_its_own_copy(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.select()
        e.apply(e.plan(self.repo))
        for folder in (".kiro/skills/hello", ".claude/skills/hello"):
            self.assertTrue((self.repo / folder / "SKILL.md").is_file())
            self.assertFalse((self.repo / folder).is_symlink())
        self.assertTrue(e.status(self.repo)["ok"])

    def test_generated_claude_files_are_copied_and_follow_their_original(self):
        steering = self.lib / "ip/steering"
        steering.mkdir(exist_ok=True)
        (steering / "always.md").write_text("---\ninclusion: always\n---\n\nUse tabs.\n")
        c.set_tools(self.repo, ["kiro", "claude"])
        c.select(self.repo, "inbound", "team", "steering", "always.md")
        planned = e.plan(self.repo)
        self.assertTrue(planned["can_apply"], planned["errors"])
        self.assertEqual(sorted(x["op"] for x in planned["changes"]), ["copy", "copy", "render"])
        e.apply(planned)
        rule = self.repo / ".claude/rules/always.md"
        self.assertFalse(rule.is_symlink())
        self.assertEqual(rule.read_text(), "Use tabs.\n")
        self.assertTrue(e.status(self.repo)["ok"])
        self.assertEqual(e.plan(self.repo)["changes"], [])
        (steering / "always.md").write_text("---\ninclusion: always\n---\n\nUse spaces.\n")
        e.apply(e.plan(self.repo))
        self.assertEqual(rule.read_text(), "Use spaces.\n")
        self.assertTrue(e.status(self.repo)["ok"])

    def test_adopt_archives_an_equal_local_copy_and_manages_a_new_one(self):
        local = self.skill(self.repo / ".kiro/skills/hello")
        planned = e.plan(self.repo, action="adopt", kind="skills", asset="hello", source="team")
        self.assertTrue(planned["can_apply"], planned["errors"])
        self.assertEqual([x["op"] for x in planned["changes"]], ["archive", "copy"])
        e.apply(planned)
        self.assertFalse(local.is_symlink())
        self.assertIn("copy", e.state(self.repo)["inbound"][".kiro/skills/hello"])
        self.assertTrue(e.status(self.repo)["ok"])
        self.assertEqual(len(e.archives(self.repo)), 1)

    def test_copies_become_links_once_links_are_available(self):
        self.select()
        e.apply(e.plan(self.repo))
        dest = self.repo / ".kiro/skills/hello"
        with patch.dict(os.environ, {"CAPTAIN_SHARE_LINKS": "1"}):
            planned = e.plan(self.repo)
            self.assertEqual([x["op"] for x in planned["changes"]], ["archive", "link"])
            e.apply(planned)
            self.assertTrue(dest.is_symlink())
            self.assertNotIn("copy", e.state(self.repo)["inbound"][".kiro/skills/hello"])
            self.assertTrue(e.status(self.repo)["ok"])
        # And back: a link is replaced by a copy where links stop working.
        planned = e.plan(self.repo)
        self.assertEqual([x["op"] for x in planned["changes"]], ["archive", "copy"])
        e.apply(planned)
        self.assertFalse(dest.is_symlink())
        self.assertTrue(e.status(self.repo)["ok"])


class PlatformHelperTests(unittest.TestCase):
    def test_windows_link_targets_lose_their_extended_path_prefix(self):
        cases = {
            "\\\\?\\C:\\Users\\me\\library\\skills\\hello": "C:\\Users\\me\\library\\skills\\hello",
            "\\\\?\\UNC\\server\\share\\hello": "\\\\server\\share\\hello",
            "relative\\hello": "relative\\hello",
        }
        for raw, expected in cases.items():
            with patch("captain_share.config.sys.platform", "win32"):
                with patch("captain_share.config.os.readlink", return_value=raw):
                    self.assertEqual(c.read_link("ignored"), expected)

    def test_links_setting_overrides_the_platform_check(self):
        with patch.dict(os.environ, {"CAPTAIN_SHARE_LINKS": "0"}):
            self.assertFalse(c.links_supported())
        with patch.dict(os.environ, {"CAPTAIN_SHARE_LINKS": "1"}):
            self.assertTrue(c.links_supported())

    def test_a_held_lock_is_reported_to_a_second_holder_without_waiting(self):
        path = c.lock_dir() / "test-lock-probe"
        with open(path, "a+") as first, open(path, "a+") as second:
            c.lock_file(first, wait=True)
            try:
                # Both flock and msvcrt lock per open file, so a second one contends.
                with self.assertRaises(BlockingIOError):
                    c.lock_file(second, wait=False)
            finally:
                c.unlock_file(first)
            c.lock_file(second, wait=False)
            c.unlock_file(second)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
