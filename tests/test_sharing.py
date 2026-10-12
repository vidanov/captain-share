import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import integrations as i


class SharingFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"CAPTAIN_SHARE_HOME": str(self.base / "global")})
        self.env.start()
        self.repo = self.base / "project" / "repo"
        self.repo.mkdir(parents=True)
        self.lib = self.base / "library"
        self.lib.mkdir()
        c.init(self.lib, library=True)
        self.skill(self.lib / "ip/skills/hello")
        c.add_source(self.repo, "team", self.lib)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def skill(self, p, text="Reusable instructions"):
        p.mkdir(parents=True, exist_ok=True)
        (p / "SKILL.md").write_text("---\nname: hello\ndescription: Test helper\n---\n" + text)
        return p

    def select(self, alias=None):
        return c.select(self.repo, "inbound", "team", "skills", "hello", alias)

    def git(self, *args):
        return e.git(self.repo, *args)


class SharingTests(SharingFixture):
    def test_housekeeping_and_unrelated_entries_are_not_assets(self):
        for base in (self.lib / "ip", self.repo / "ip"):
            for kind in ("skills", "agents", "prompts", "steering"):
                folder = base / kind
                folder.mkdir(parents=True, exist_ok=True)
                for name in (".DS_Store", ".gitkeep"):
                    (folder / name).write_text("metadata")
            (base / "prompts/box").mkdir()
            (base / "prompts/notes.txt").write_text("not a prompt")
        self.assertEqual([r["name"] for r in e.catalog(self.repo, "team")], ["hello"])
        self.assertEqual(e.scan(self.repo), [])

    def test_nested_housekeeping_is_ignored_and_not_exported(self):
        local = self.skill(self.repo / "skills/new")
        (local / ".DS_Store").write_text("metadata")
        (local / "references").mkdir()
        (local / "references/.gitkeep").write_text("")
        (local / "references/guide.md").write_text("Useful content")
        (local / "__pycache__").mkdir()
        (local / "__pycache__/helper.cpython-312.pyc").write_bytes(b"bytecode")
        self.assertEqual(e.scan(self.repo)[0]["status"], "local")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        planned = e.plan(self.repo, "outbound")
        (local / ".DS_Store").write_text("changed metadata")
        e.apply(planned)
        dest = self.lib / "ip/skills/new"
        self.assertFalse((dest / ".DS_Store").exists())
        self.assertFalse((dest / "references/.gitkeep").exists())
        self.assertFalse((dest / "__pycache__").exists())
        self.assertTrue((dest / "references/guide.md").exists())
        self.assertTrue((local / ".DS_Store").exists())
        self.assertEqual(e.snapshot(local), e.snapshot(dest))
        (dest / ".DS_Store").write_text("metadata")
        self.assertEqual(e.catalog(self.repo, "team")[-1]["status"], "available")
        (dest / ".env").write_text("private")
        self.assertEqual(e.catalog(self.repo, "team")[-1]["status"], "invalid")

    def test_live_link_alias_and_inbound_removal_preserve_original(self):
        self.select("greet")
        e.apply(e.plan(self.repo))
        link = self.repo / ".kiro/skills/greet"
        self.assertTrue(link.is_symlink())
        original = self.lib / "ip/skills/hello/SKILL.md"
        original.write_text("updated live")
        self.assertEqual((link / "SKILL.md").read_text(), "updated live")
        self.select("greeting")
        e.apply(e.plan(self.repo))
        self.assertFalse(link.exists())
        self.assertTrue((self.repo / ".kiro/skills/greeting").is_symlink())
        c.select(self.repo, "inbound", "team", "skills", "hello", enabled=False)
        e.apply(e.plan(self.repo))
        self.assertEqual(original.read_text(), "updated live")
        self.assertEqual(len(e.archives(self.repo)), 2)

    def test_layering_masks_inherited_selections(self):
        c.add_source(self.repo, "global-tools", self.lib, scope="global")
        c.add_source(self.repo.parent, "parent-tools", self.lib, scope="folder")
        c.select(self.repo.parent, "inbound", "parent-tools", "skills", "hello")
        self.assertIn("parent-tools/skills/hello", c.effective(self.repo)["inbound"])
        c.select(self.repo, "inbound", "parent-tools", "skills", "hello", enabled=False)
        merged = c.effective(self.repo)
        self.assertNotIn("parent-tools/skills/hello", merged["inbound"])
        self.assertIn("global-tools", merged["sources"])
        self.assertNotIn(str(self.lib), (self.repo / ".captain/sharing.json").read_text())

    def test_home_selections_stay_user_wide_and_are_not_project_defaults(self):
        # Make self.base the "home" folder: its .captain is the global layer.
        with patch.dict(os.environ, {"CAPTAIN_SHARE_HOME": str(self.base / ".captain")}):
            c.add_source(self.base, "personal", self.lib)
            c.select(self.base, "inbound", "personal", "skills", "hello")
            self.assertIn("personal/skills/hello", c.effective(self.base)["inbound"])
            merged = c.effective(self.repo)
            self.assertNotIn("personal/skills/hello", merged["inbound"])
            self.assertIn("personal", merged["sources"])
            c.set_tools(self.base, ["kiro", "claude"])
            self.assertEqual(c.effective(self.repo)["tools"], ["kiro", "claude"])
            self.assertEqual(e.plan(self.repo)["changes"], [])

    def test_preview_is_read_only_and_saved_preview_goes_stale(self):
        self.select()
        before = {str(p): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        planned = e.plan(self.repo)
        self.assertEqual(
            before, {str(p): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        )
        (self.lib / "ip/skills/hello/SKILL.md").write_text("changed")
        with self.assertRaisesRegex(c.ShareError, "stale"):
            e.apply(planned)
        self.assertFalse((self.repo / ".kiro").exists())

    def test_refuses_unowned_and_modified_links(self):
        self.select()
        local = self.skill(self.repo / ".kiro/skills/hello", "mine")
        self.assertFalse(e.plan(self.repo)["can_apply"])
        self.assertIn("mine", (local / "SKILL.md").read_text())
        e.apply(e.plan(self.repo, action="remove", kind="skills", asset="hello"))
        self.select()
        e.apply(e.plan(self.repo))
        local.unlink()
        local.symlink_to(self.lib / "ip/skills/other")
        self.assertFalse(e.plan(self.repo)["can_apply"])

    def test_remove_and_restore_owned_alias_reinstates_selection(self):
        self.select("greet")
        e.apply(e.plan(self.repo))
        result = e.apply(e.plan(self.repo, action="remove", kind="skills", asset="greet"))
        self.assertNotIn("team/skills/hello", c.effective(self.repo)["inbound"])
        archive_id = result["archives"][0]["id"]
        e.apply(e.plan(self.repo, action="restore", archive_id=archive_id))
        self.assertTrue((self.repo / ".kiro/skills/greet").is_symlink())
        self.assertIn("team/skills/hello", c.effective(self.repo)["inbound"])
        self.assertFalse(e.plan(self.repo, action="restore", archive_id=archive_id)["can_apply"])

    def test_archive_restore_local_directory(self):
        local = self.skill(self.repo / ".kiro/skills/local", "local original")
        result = e.apply(e.plan(self.repo, action="remove", kind="skills", asset="local"))
        self.assertFalse(local.exists())
        e.apply(e.plan(self.repo, action="restore", archive_id=result["archives"][0]["id"]))
        self.assertIn("local original", (local / "SKILL.md").read_text())

    def test_outbound_copy_update_and_independent_edit_conflict(self):
        src = self.skill(self.repo / "skills/new", "first")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        e.apply(e.plan(self.repo, "outbound"))
        dest = self.lib / "ip/skills/new"
        self.assertTrue(src.exists())
        self.assertFalse(dest.is_symlink())
        (src / "SKILL.md").write_text("second")
        e.apply(e.plan(self.repo, "outbound"))
        self.assertEqual((dest / "SKILL.md").read_text(), "second")
        self.assertEqual(len(e.archives(self.lib)), 1)
        (dest / "SKILL.md").write_text("colleague edit")
        (src / "SKILL.md").write_text("third")
        self.assertFalse(e.plan(self.repo, "outbound")["can_apply"])
        self.assertEqual((dest / "SKILL.md").read_text(), "colleague edit")

    def test_rollback_restores_outbound_original_on_copy_failure(self):
        src = self.skill(self.repo / "skills/new", "first")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        e.apply(e.plan(self.repo, "outbound"))
        before = e.state(self.repo)
        (src / "SKILL.md").write_text("second")
        planned = e.plan(self.repo, "outbound")
        with patch.object(e.Transaction, "copy", side_effect=OSError("simulated disk failure")):
            with self.assertRaisesRegex(c.ShareError, "rolled back"):
                e.apply(planned)
        self.assertIn("first", (self.lib / "ip/skills/new/SKILL.md").read_text())
        self.assertEqual(e.state(self.repo), before)
        self.assertEqual(e.archives(self.lib), [])

    def test_duplicate_inbound_destinations_are_blocked(self):
        c.add_source(self.repo, "other", self.lib)
        self.select()
        c.select(self.repo, "inbound", "other", "skills", "hello")
        self.assertFalse(e.plan(self.repo)["can_apply"])

    def test_paths_and_nested_symlinks_cannot_escape(self):
        with self.assertRaises(c.ShareError):
            c.select(self.repo, "outbound", "team", "skills", "new", path="../outside")
        with self.assertRaises(c.ShareError):
            self.select("../escape")
        skill = self.skill(self.repo / "skills/new")
        (skill / "external").symlink_to(self.base)
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        self.assertFalse(e.plan(self.repo, "outbound")["can_apply"])
        (self.repo / ".kiro").symlink_to(self.base)
        self.select()
        self.assertFalse(e.plan(self.repo)["can_apply"])

    def test_outbound_credential_and_private_path_detection(self):
        src = self.skill(self.repo / "skills/new", "/Users/" + "somebody/private/file")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        self.assertFalse(e.plan(self.repo, "outbound")["can_apply"])
        (src / "SKILL.md").write_text("-----BEGIN " + "PRIVATE KEY-----")
        self.assertFalse(e.plan(self.repo, "outbound")["can_apply"])

    def test_git_exclusion_and_hook_preservation(self):
        self.git("init", "-q")
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        self.select()
        e.apply(e.plan(self.repo))
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.git("check-ignore", ".kiro/skills/hello").returncode, 0)
        self.assertTrue(e.guard(self.repo)["ok"])
        self.git("add", "-f", ".kiro/skills/hello")
        self.assertFalse(e.guard(self.repo)["ok"])
        self.assertFalse(e.plan(self.repo)["can_apply"])

    def test_tracked_local_asset_is_not_archived(self):
        self.git("init", "-q")
        self.skill(self.repo / ".kiro/skills/local")
        self.git("add", ".kiro/skills/local")
        self.assertFalse(
            e.plan(self.repo, action="remove", kind="skills", asset="local")["can_apply"]
        )

    def test_config_symlink_refused(self):
        (self.repo / ".captain/sharing.local.json").unlink()
        (self.repo / ".captain/sharing.local.json").symlink_to(self.lib / "anything")
        with self.assertRaises(c.ShareError):
            c.effective(self.repo)

    def test_captain_import_does_not_execute_or_take_over(self):
        legacy = self.base / "legacy"
        legacy.mkdir()
        (legacy / "sources.json").write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "src_old",
                            "canonical_root": str(self.lib),
                            "python_executable": "untrusted-command",
                        }
                    ]
                }
            )
        )
        (legacy / "repositories.json").write_text(
            json.dumps({"repositories": [{"id": "repo_old", "canonical_root": str(self.repo)}]})
        )
        preview = i.import_captain(legacy, self.repo)
        self.assertFalse(preview["applied"])
        self.assertNotIn("src_old", c.effective(self.repo)["sources"])
        i.import_captain(legacy, self.repo, True)
        self.assertIn("src_old", c.effective(self.repo)["sources"])
        self.assertEqual(e.state(self.repo)["inbound"], {})

    def test_profile_import_preview_then_selection(self):
        (self.lib / "profiles").mkdir()
        self.skill(self.lib / "ip/skills/other")
        # Legacy toolkit manifests use commas; whitespace-only lists also work.
        (self.lib / "profiles/pilot.manifest").write_text("# pilot\nskills: hello, other\n")
        self.assertFalse(i.import_profile(self.repo, "team", "pilot")["applied"])
        i.import_profile(self.repo, "team", "pilot", True)
        inbound = c.effective(self.repo)["inbound"]
        self.assertIn("team/skills/hello", inbound)
        self.assertIn("team/skills/other", inbound)

    def test_kiro_handoff_is_explicit_and_does_not_grant_trust(self):
        result = i.kiro_handoff(self.repo, "Help choose skills")
        self.assertFalse(result["executed"])
        self.assertNotIn("--trust-all-tools", result["command"])
        self.assertEqual(result["cwd"], str(self.repo))

    def test_saved_plan_recomputes_actions_instead_of_trusting_edited_payload(self):
        self.select()
        planned = e.plan(self.repo)
        planned["changes"] = [{"op": "archive", "path": str(self.base)}]
        e.apply(planned)
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        self.assertTrue(self.lib.exists())

    def test_missing_link_can_be_repaired(self):
        self.select()
        e.apply(e.plan(self.repo))
        link = self.repo / ".kiro/skills/hello"
        link.unlink()
        e.apply(e.plan(self.repo))
        self.assertTrue(link.is_symlink())

    def test_missing_library_does_not_block_archiving_its_link(self):
        self.select()
        e.apply(e.plan(self.repo))
        self.lib.rename(self.base / "moved-library")
        e.apply(e.plan(self.repo, action="remove", kind="skills", asset="hello"))
        self.assertFalse((self.repo / ".kiro/skills/hello").is_symlink())

    def test_identical_unowned_copy_is_not_claimed(self):
        import shutil

        src = self.skill(self.repo / "skills/new")
        shutil.copytree(src, self.lib / "ip/skills/new")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        e.apply(e.plan(self.repo, "outbound"))
        self.assertNotIn("team/skills/new", e.state(self.repo)["outbound"])
        (src / "SKILL.md").write_text("changed")
        self.assertFalse(e.plan(self.repo, "outbound")["can_apply"])

    def test_worktree_installation_preserves_other_worktree(self):
        self.git("init", "-q")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        )
        worktree = self.base / "worktree"
        self.git("worktree", "add", "-b", "test-worktree", str(worktree))
        c.add_source(worktree, "team", self.lib)
        c.select(worktree, "inbound", "team", "skills", "hello")
        self.select()
        e.apply(e.plan(self.repo))
        e.apply(e.plan(worktree))
        e.apply(e.plan(worktree, action="remove", kind="skills", asset="hello"))
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        self.assertFalse((worktree / ".kiro/skills/hello").exists())

    def test_archive_path_cannot_redirect_outside_target(self):
        (self.repo / ".captain/archive").symlink_to(self.base)
        self.select()
        with self.assertRaises(c.ShareError):
            e.plan(self.repo)

    def test_common_tmp_alias_is_accepted_as_global_location(self):
        with patch.dict(os.environ, {"CAPTAIN_SHARE_HOME": str(self.base / "another-global")}):
            c.init(self.repo, scope="global")
            self.assertTrue((self.base / "another-global/sharing.json").exists())

    def test_all_file_asset_kinds_use_their_declared_destinations(self):
        names = {
            "agents": "helper.json",
            "prompts": "summary.md",
            "steering": "guide.md",
            "copilot": "copilot-instructions.md",
            "copilot-instructions": "python.instructions.md",
            "copilot-prompts": "review.prompt.md",
        }
        for kind, name in names.items():
            (self.lib / "ip" / kind / name).write_text(
                "{}" if kind == "agents" else "Reusable guidance"
            )
            c.select(self.repo, "inbound", "team", kind, name)
        e.apply(e.plan(self.repo))
        for kind, name in names.items():
            for _, folder in c.destinations(kind, c.DEFAULT_TOOLS):
                self.assertTrue((self.repo / folder / name).is_symlink())

    def test_kiro_run_uses_explicit_working_folder_and_keeps_approval_flags(self):
        import stat

        bindir = self.base / "fake-bin"
        bindir.mkdir()
        executable = bindir / "kiro-cli"
        executable.write_text(
            '#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath("handoff.json").write_text(json.dumps({"argv":sys.argv[1:],"cwd":os.getcwd()}))\n'
        )
        executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        with patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            result = i.kiro_handoff(self.repo, "Inspect sharing", True)
        self.assertEqual(result["exit_code"], 0)
        call = json.loads((self.repo / "handoff.json").read_text())
        self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual(call["argv"][0], "chat")
        self.assertNotIn("--trust-all-tools", call["argv"])

    def test_default_tools_keep_existing_kiro_only_skill_installation(self):
        self.select()
        e.apply(e.plan(self.repo))
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        self.assertFalse((self.repo / ".claude").exists())

    def test_one_original_links_into_each_enabled_tool(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.select("greet")
        e.apply(e.plan(self.repo))
        original = self.lib / "ip/skills/hello"
        for folder in (".kiro/skills", ".claude/skills"):
            link = self.repo / folder / "greet"
            self.assertEqual(os.readlink(link), str(original))
        self.assertEqual({r["tool"] for r in e.status(self.repo)["installed"]}, {"kiro", "claude"})
        (original / "SKILL.md").write_text("one edit")
        self.assertEqual((self.repo / ".claude/skills/greet/SKILL.md").read_text(), "one edit")
        self.assertEqual(e.plan(self.repo)["changes"], [])

    def test_disabling_a_tool_archives_only_its_links(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.select()
        e.apply(e.plan(self.repo))
        c.set_tools(self.repo, ["kiro"])
        result = e.apply(e.plan(self.repo))
        self.assertEqual(len(result["archives"]), 1)
        self.assertFalse(os.path.lexists(self.repo / ".claude/skills/hello"))
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        self.assertIn("team/skills/hello", c.effective(self.repo)["inbound"])

    def test_removing_a_managed_asset_archives_every_tool_link(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.select()
        e.apply(e.plan(self.repo))
        planned = e.plan(self.repo, action="remove", kind="skills", asset="hello")
        self.assertEqual([x["op"] for x in planned["changes"]], ["archive", "archive"])
        e.apply(planned)
        self.assertNotIn("team/skills/hello", c.effective(self.repo)["inbound"])
        self.assertEqual(e.state(self.repo)["inbound"], {})
        self.assertEqual(e.plan(self.repo)["changes"], [])

    def test_unmanaged_path_removal_keeps_selection_for_library_copy(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        local = self.skill(self.repo / ".claude/skills/hello", "mine")
        self.select()
        planned = e.plan(self.repo)
        self.assertFalse(planned["can_apply"])
        self.assertEqual(
            [(x["path"], x["reason"]) for x in planned["conflicts"]],
            [(".claude/skills/hello", "unmanaged")],
        )
        e.apply(
            e.plan(
                self.repo,
                action="remove",
                kind="skills",
                asset="hello",
                path=".claude/skills/hello",
            )
        )
        self.assertFalse(local.exists())
        self.assertIn("team/skills/hello", c.effective(self.repo)["inbound"])
        e.apply(e.plan(self.repo))
        self.assertTrue((self.repo / ".claude/skills/hello").is_symlink())
        with self.assertRaisesRegex(c.ShareError, "Not a skills destination"):
            e.plan(self.repo, action="remove", kind="skills", asset="hello", path="README.md")

    def test_dangling_link_from_old_library_location_is_archived_then_received(self):
        stale = self.repo / ".kiro/skills/hello"
        stale.parent.mkdir(parents=True)
        stale.symlink_to(self.base / "moved-away/ip/skills/hello")
        self.select()
        self.assertEqual(e.plan(self.repo)["conflicts"][0]["reason"], "unmanaged")
        result = e.apply(
            e.plan(
                self.repo, action="remove", kind="skills", asset="hello", path=".kiro/skills/hello"
            )
        )
        self.assertFalse(os.path.lexists(stale))
        self.assertEqual(len(result["archives"]), 1)
        e.apply(e.plan(self.repo))
        self.assertEqual(os.readlink(stale), str(self.lib / "ip/skills/hello"))

    def test_claude_skill_can_be_contributed_then_received_for_both_tools(self):
        self.skill(self.repo / ".claude/skills/drafts", "claude original")
        row = [r for r in e.scan(self.repo) if r["name"] == "drafts"][0]
        self.assertEqual((row["tool"], row["status"]), ("claude", "local"))
        c.select(self.repo, "outbound", "team", "skills", "drafts", path=row["path"])
        e.apply(e.plan(self.repo, "outbound"))
        self.assertTrue((self.lib / "ip/skills/drafts/SKILL.md").is_file())
        c.set_tools(self.repo, ["kiro", "claude"])
        c.select(self.repo, "inbound", "team", "skills", "drafts")
        planned = e.plan(self.repo)
        self.assertEqual(
            [(x["path"], x["reason"]) for x in planned["conflicts"]],
            [(".claude/skills/drafts", "contributed")],
        )
        listed = {r["name"]: r for r in e.catalog(self.repo, "team")}["drafts"]
        self.assertEqual(
            [(d["tool"], d["state"]) for d in listed["destinations"]],
            [("kiro", "absent"), ("claude", "contributed")],
        )

    def test_kind_without_enabled_tool_is_a_conflict(self):
        (self.lib / "ip/steering/guide.md").write_text("Guidance")
        c.set_tools(self.repo, ["claude"])
        c.select(self.repo, "inbound", "team", "steering", "guide.md")
        planned = e.plan(self.repo)
        self.assertFalse(planned["can_apply"])
        self.assertIn("No enabled tool reads steering", planned["errors"][0])

    def test_tools_are_validated_and_deepest_layer_wins(self):
        c.set_tools(self.repo, ["kiro", "claude"], scope="global")
        self.assertEqual(c.effective(self.repo)["tools"], ["kiro", "claude"])
        c.set_tools(self.repo, ["claude", "kiro"])
        self.assertEqual(c.effective(self.repo)["tools"], ["kiro", "claude"])
        for bad in ([], ["cursor"], ["kiro", "kiro"]):
            with self.assertRaises(c.ShareError):
                c.set_tools(self.repo, bad)
        local = self.repo / ".captain/sharing.local.json"
        data = json.loads(local.read_text())
        data["tools"] = ["kiro"]
        local.write_text(json.dumps(data))
        with self.assertRaisesRegex(c.ShareError, "portable"):
            c.effective(self.repo)

    def test_catalog_reports_install_state_per_tool(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.skill(self.repo / ".kiro/skills/hello", "local")
        row = e.catalog(self.repo, "team")[0]
        self.assertEqual(
            [(d["tool"], d["state"]) for d in row["destinations"]],
            [("kiro", "blocked"), ("claude", "absent")],
        )
        e.apply(e.plan(self.repo, action="remove", kind="skills", asset="hello"))
        self.select()
        e.apply(e.plan(self.repo))
        row = e.catalog(self.repo, "team")[0]
        self.assertEqual({d["state"] for d in row["destinations"]}, {"installed"})

    def power(self, name="demo-power", manifest_name=None, env=None):
        folder = self.lib / "ip/powers" / name
        (folder / "skills/greet").mkdir(parents=True, exist_ok=True)
        manifest = {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": manifest_name or name,
            "version": "1.0.0",
            "description": "Fictional power",
            "author": {"name": "Tests"},
            "keywords": ["demo"],
        }
        (folder / "plugin.json").write_text(json.dumps(manifest))
        (folder / "skills/greet/SKILL.md").write_text("---\nname: greet\ndescription: Hi\n---\n")
        if env is not None:
            servers = {"mcpServers": {"demo": {"command": "demo", "env": env}}}
            (folder / "mcp.json").write_text(json.dumps(servers))
        return folder

    def fake_kiro(self):
        """Mimics kiro-cli 2.29.1: copies by manifest name into ~/.kiro/powers/installed."""
        import stat

        bindir = self.base / "fake-kiro"
        bindir.mkdir(exist_ok=True)
        script = bindir / "kiro-cli"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, shutil, sys\nfrom pathlib import Path\n"
            "home = Path(os.environ['HOME']) / '.kiro/powers/installed'\n"
            "home.mkdir(parents=True, exist_ok=True)\n"
            "_, action, arg = sys.argv[1:4]\n"
            "if action == 'install':\n"
            "    src = Path(arg); m = src / 'plugin.json'\n"
            "    name = json.loads(m.read_text())['name'] if m.exists() else src.name\n"
            "    shutil.rmtree(home / name, ignore_errors=True)\n"
            "    shutil.copytree(src, home / name)\n"
            "    print('Installed power', name)\n"
            "else:\n"
            "    shutil.rmtree(home / arg); print('Uninstalled power', arg)\n"
        )
        script.chmod(stat.S_IRWXU)
        env = patch.dict(
            os.environ,
            {
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "HOME": str(self.base / "home"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        return self.base / "home/.kiro/powers/installed"

    def test_powers_are_validated_as_library_assets(self):
        self.power()
        self.power("renamed", manifest_name="demo-power")
        legacy = self.lib / "ip/powers/legacy"
        legacy.mkdir()
        (legacy / "POWER.md").write_text('---\nname: "legacy"\nkeywords: ["x"]\n---\n# Legacy\n')
        bare = self.lib / "ip/powers/bare"
        bare.mkdir()
        (bare / "README.md").write_text("no manifest")
        rows = {r["name"]: r for r in e.catalog(self.repo, "team") if r["kind"] == "powers"}
        self.assertEqual(rows["demo-power"]["status"], "available")
        self.assertEqual(rows["legacy"]["status"], "available")
        self.assertIn("must match its folder", rows["renamed"]["error"])
        self.assertIn("lacks plugin.json or POWER.md", rows["bare"]["error"])
        self.assertEqual(rows["demo-power"]["destinations"], [])
        with self.assertRaisesRegex(c.ShareError, "powers install"):
            c.select(self.repo, "inbound", "team", "powers", "demo-power")

    def test_power_contribution_requires_mcp_env_references(self):
        import shutil

        local = self.repo / "powers/team-power"
        shutil.move(str(self.power("team-power", env={"API_KEY": "literal-value"})), str(local))
        c.select(self.repo, "outbound", "team", "powers", "team-power", path="powers/team-power")
        planned = e.plan(self.repo, "outbound")
        self.assertIn("use ${VAR} references", planned["errors"][0])
        (local / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"demo": {"command": "demo", "env": {"API_KEY": "${API_KEY}"}}}}
            )
        )
        e.apply(e.plan(self.repo, "outbound"))
        self.assertTrue((self.lib / "ip/powers/team-power/plugin.json").is_file())

    def test_power_install_update_uninstall_through_kiro(self):
        installed = self.fake_kiro()
        original = self.power()
        self.assertEqual(i.powers(self.repo, "team")[0]["kiro"], "not-installed")
        planned = i.power_plan(self.repo, "install", "team", "demo-power")
        self.assertEqual(planned["changes"][0]["op"], "kiro install")
        self.assertEqual(planned["changes"][0]["command"][1:3], ["powers", "install"])
        self.assertFalse(installed.exists())
        i.power_apply(planned)
        self.assertEqual(i.powers(self.repo, "team")[0]["kiro"], "current")
        self.assertEqual(i.power_plan(self.repo, "install", "team", "demo-power")["changes"], [])
        (original / "skills/greet/SKILL.md").write_text("changed")
        self.assertEqual(i.powers(self.repo, "team")[0]["kiro"], "differs")
        update = i.power_plan(self.repo, "install", "team", "demo-power")
        self.assertEqual(update["changes"][0]["op"], "kiro update")
        (original / "skills/greet/SKILL.md").write_text("changed again")
        with self.assertRaisesRegex(c.ShareError, "stale"):
            i.power_apply(update)
        i.power_apply(i.power_plan(self.repo, "install", "team", "demo-power"))
        self.assertEqual(
            (installed / "demo-power/skills/greet/SKILL.md").read_text(), "changed again"
        )
        i.power_apply(i.power_plan(self.repo, "uninstall", "team", "demo-power"))
        self.assertFalse((installed / "demo-power").exists())
        self.assertFalse(i.power_plan(self.repo, "uninstall", "team", "demo-power")["can_apply"])
        self.assertTrue(original.exists())

    def test_cli_powers_preview_then_apply(self):
        installed = self.fake_kiro()
        self.power()
        exe = Path(__file__).resolve().parents[1] / "bin/captain-share"

        def run(*args):
            p = subprocess.run(
                [str(exe), "--target", str(self.repo), "--json", *args],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            return json.loads(p.stdout)

        self.assertEqual(run("powers", "list", "--source", "team")[0]["kiro"], "not-installed")
        self.assertEqual(run("powers", "install", "team", "demo-power")["state"], "not-installed")
        self.assertFalse(installed.exists())
        self.assertTrue(run("powers", "install", "team", "demo-power", "--apply")["ok"])
        self.assertEqual(run("powers", "list", "--source", "team")[0]["kiro"], "current")
        run("powers", "uninstall", "team", "demo-power", "--apply")
        self.assertFalse((installed / "demo-power").exists())

    def test_power_plan_without_kiro_cli_is_blocked(self):
        self.power()
        with patch("captain_share.integrations.shutil.which", return_value=None):
            planned = i.power_plan(self.repo, "install", "team", "demo-power")
        self.assertFalse(planned["can_apply"])
        self.assertIn("kiro-cli", planned["errors"][0])

    def test_minimal_config_can_be_extended(self):
        (self.repo / ".captain/sharing.json").write_text('{"version":1}')
        self.select()
        self.assertIn("team/skills/hello", c.effective(self.repo)["inbound"])

    def test_cli_full_round_trip(self):
        exe = Path(__file__).resolve().parents[1] / "bin/captain-share"

        def run(*args):
            p = subprocess.run(
                [str(exe), "--target", str(self.repo), "--json", *args],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            return json.loads(p.stdout)

        self.assertEqual(run("tools")["tools"], ["kiro", "copilot"])
        self.assertEqual(run("tools", "claude", "kiro")["tools"], ["kiro", "claude"])
        run("select", "inbound", "team", "skills", "hello", "--alias", "greet")
        run("plan", "--save", str(self.base / "plan.json"))
        run("apply", str(self.base / "plan.json"))
        installed = run("status")["installed"]
        self.assertEqual(
            {(r["tool"], r["status"]) for r in installed},
            {("kiro", "linked"), ("claude", "linked")},
        )


if __name__ == "__main__":
    unittest.main()
