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
        self.assertEqual(e.scan(self.repo)[0]["status"], "local")
        c.select(self.repo, "outbound", "team", "skills", "new", path="skills/new")
        planned = e.plan(self.repo, "outbound")
        (local / ".DS_Store").write_text("changed metadata")
        e.apply(planned)
        dest = self.lib / "ip/skills/new"
        self.assertFalse((dest / ".DS_Store").exists())
        self.assertFalse((dest / "references/.gitkeep").exists())
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
        (self.lib / "profiles/pilot.manifest").write_text("skills: hello\n")
        self.assertFalse(i.import_profile(self.repo, "team", "pilot")["applied"])
        i.import_profile(self.repo, "team", "pilot", True)
        self.assertIn("team/skills/hello", c.effective(self.repo)["inbound"])

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
            self.assertTrue((self.repo / c.KINDS[kind] / name).is_symlink())

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

        run("select", "inbound", "team", "skills", "hello", "--alias", "greet")
        run("plan", "--save", str(self.base / "plan.json"))
        run("apply", str(self.base / "plan.json"))
        self.assertEqual(run("status")["installed"][0]["status"], "linked")


if __name__ == "__main__":
    unittest.main()
