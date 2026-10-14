import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

from captain_share import config as c
from captain_share import engine as e
from captain_share import repositories as r
from tests import test_library_home
from tests.test_library_home import LibraryFixture


def out(path, *args):
    return e.git(path, *args).stdout.decode().strip()


class ExtendedCopyFixture(LibraryFixture):
    def extended(self, text="My project rules"):
        """A local copy of the team skill with an extra line and file."""
        copy = self.skill(self.repo / ".kiro/skills/hello", "Reusable instructions\n" + text)
        (copy / "notes.md").write_text("project notes\n")
        return copy


class DiffTests(ExtendedCopyFixture):
    def test_diff_lists_changed_added_removed_and_binary_files(self):
        copy = self.extended()
        (copy / ".DS_Store").write_text("housekeeping")
        (copy / "logo.png").write_bytes(b"\x89PNG\0\0data")
        (self.lib / "ip/skills/hello/old.md").write_text("only in the library\n")
        self.start()
        code, result = self.request("/api/diff?source=team&kind=skills&name=hello")
        self.assertEqual(code, 200, result)
        self.assertEqual(result["path"], ".kiro/skills/hello")
        rows = {row["path"]: row for row in result["files"]}
        self.assertEqual(
            {k: v["status"] for k, v in rows.items()},
            {"SKILL.md": "changed", "notes.md": "added", "old.md": "removed", "logo.png": "added"},
        )
        self.assertIn("+My project rules", rows["SKILL.md"]["diff"])
        self.assertTrue(rows["logo.png"]["binary"])
        self.assertIsNone(rows["logo.png"]["diff"])
        self.assertFalse(result["truncated"])
        explicit = "/api/diff?source=team&kind=skills&name=hello&path=" + quote(
            ".kiro/skills/hello"
        )
        self.assertEqual(self.request(explicit)[1]["files"], result["files"])
        for bad in ("&path=../x", "&path=.kiro/skills/none"):
            self.assertEqual(
                self.request("/api/diff?source=team&kind=skills&name=hello" + bad)[0], 400
            )

    def test_diff_is_bounded(self):
        copy = self.extended()
        for n in range(5):
            (copy / f"extra{n}.md").write_text("line\n" * 50)
        with patch.object(e, "MAX_DIFF_FILES", 3):
            limited = e.diff(self.repo, "team", "skills", "hello")
        self.assertEqual((len(limited["files"]), limited["truncated"]), (3, True))
        with patch.object(e, "MAX_DIFF_BYTES", 400):
            small = e.diff(self.repo, "team", "skills", "hello")
        self.assertTrue(small["truncated"])
        self.assertTrue(any(row["diff"] is None for row in small["files"]))
        self.assertLessEqual(sum(len((row["diff"] or "").encode()) for row in small["files"]), 400)

    def test_candidates_mark_copies_that_differ(self):
        self.extended()
        self.skill(self.repo / ".kiro/skills/same")
        self.skill(self.lib / "ip/skills/same")
        self.start()
        _, found = self.request("/api/share-candidates")
        rows = {row["name"]: row for row in found["local"]}
        self.assertEqual((rows["hello"]["exists_in"], rows["hello"]["differs"]), ("team", True))
        self.assertEqual(rows["same"]["differs"], False)
        self.assertEqual(found["versions"], [])


class OlderVersionTests(ExtendedCopyFixture):
    def test_copy_of_an_earlier_team_version_is_marked_older_not_edited(self):
        # The team changed the skill after the local copy was taken.
        self.skill(self.repo / ".kiro/skills/hello")
        self.skill(self.lib / "ip/skills/hello", "Improved team instructions")
        self.commit(self.lib, "Improve hello")
        self.skill(self.repo / ".kiro/skills/mine", "Something new")
        self.skill(self.lib / "ip/skills/mine", "Team text")
        self.commit(self.lib, "Add mine")
        self.start()
        _, found = self.request("/api/share-candidates")
        rows = {row["name"]: row for row in found["local"]}
        self.assertTrue(rows["hello"]["differs"])
        self.assertIsNotNone(rows["hello"]["older"])
        self.assertEqual(len(rows["hello"]["older"]["commit"]), 10)
        # A copy that matches no team version is the user's own change.
        self.assertTrue(rows["mine"]["differs"])
        self.assertIsNone(rows["mine"]["older"])


class ProposeTests(ExtendedCopyFixture):
    def test_direct_proposal_updates_commits_pushes_and_adopts(self):
        remote = self.remote()
        self.start()
        self.skill(self.repo / ".kiro/skills/hello")
        body = {"source": "team", "kind": "skills", "name": "hello", "mode": "direct"}
        _, same = self.request("/api/propose-plan", body)
        self.assertEqual(same["conflicts"][0]["reason"], "same")
        self.extended()
        (self.lib / "unrelated.md").write_text("staged elsewhere")
        e.git(self.lib, "add", "unrelated.md")
        code, plan = self.request("/api/propose-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual((plan["mode"], plan["branch"]), ("direct", None))
        self.assertEqual(plan["commit_paths"], ["ip/skills/hello"])
        self.assertEqual([x["op"] for x in plan["changes"]], ["update"])
        self.assertEqual(plan["push_url"], str(remote))
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        code, result = self.request(
            "/api/propose-apply",
            {"fingerprint": plan["fingerprint"], "note": "Add project rules", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            set(result),
            {"commit", "pushed", "push_error", "adopted", "adopt_error", "branch", "review_url"}
            | {"push_error_hint", "push_error_command", "waiting_for_review"}
            | {"library", "landed_on"},
        )
        self.assertEqual(
            (result["pushed"], result["adopted"], result["branch"], result["review_url"]),
            (True, True, None, None),
        )
        self.assertEqual(out(remote, "rev-parse", "main"), result["commit"])
        files = out(self.lib, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(files), ["ip/skills/hello/SKILL.md", "ip/skills/hello/notes.md"])
        self.assertIn("A  unrelated.md", e.git(self.lib, "status", "--porcelain").stdout.decode())
        self.assertIn("My project rules", (self.lib / "ip/skills/hello/SKILL.md").read_text())
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        # The previous library version is archived beside the library, not lost.
        self.assertEqual(len(e.archives(self.lib)), 1)

    def test_review_proposal_leaves_checkout_untouched_and_pushes_a_branch(self):
        remote = self.remote()
        self.extended()
        (self.lib / "ip/skills/hello/SKILL.md").write_text("---\nname: hello\n---\nwip edit")
        head, before = out(self.lib, "rev-parse", "HEAD"), out(self.lib, "status", "--porcelain")
        branch_before = out(self.lib, "symbolic-ref", "HEAD")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "hello", "mode": "review"}
        code, plan = self.request("/api/propose-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual(plan["branch"], "share/hello-test-sharer")
        _, stale_source = self.request("/api/propose-plan", body)
        (self.repo / ".kiro/skills/hello/notes.md").write_text("changed after preview")
        code, stale = self.request(
            "/api/propose-apply", {"fingerprint": stale_source["fingerprint"], "note": "x"}
        )
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        _, plan = self.request("/api/propose-plan", body)
        code, result = self.request(
            "/api/propose-apply",
            {"fingerprint": plan["fingerprint"], "note": "Propose project rules", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            (result["branch"], result["pushed"], result["adopted"], result["adopt_error"]),
            ("share/hello-test-sharer", True, False, None),
        )
        self.assertIsNone(result["review_url"])  # A local remote has no review page.
        self.assertEqual(out(self.lib, "rev-parse", "HEAD"), head)
        self.assertEqual(out(self.lib, "symbolic-ref", "HEAD"), branch_before)
        self.assertEqual(out(self.lib, "status", "--porcelain"), before)
        self.assertEqual(
            out(remote, "rev-parse", "refs/heads/share/hello-test-sharer"), result["commit"]
        )
        self.assertEqual(out(self.lib, "rev-parse", f"{result['commit']}^"), head)
        content = out(self.lib, "show", f"{result['commit']}:ip/skills/hello/SKILL.md")
        self.assertIn("My project rules", content)
        self.assertEqual(
            out(self.lib, "show", f"{result['commit']}:ip/skills/hello/notes.md"),
            "changed after preview",
        )
        self.assertFalse((self.repo / ".kiro/skills/hello").is_symlink())
        _, again = self.request("/api/propose-plan", body)
        self.assertEqual(again["conflicts"][-1]["reason"], "branch")


class ReviewUrlTests(unittest.TestCase):
    def test_review_links_and_redaction(self):
        branch = "share/hello-ada"
        for url in (
            "https://user:token@github.com/team/skills.git",
            "git@github.com:team/skills.git",
            "ssh://git@github.com/team/skills",
        ):
            self.assertEqual(
                r.review_url(url, "main", branch),
                "https://github.com/team/skills/compare/main...share/hello-ada?expand=1",
            )
        self.assertEqual(
            r.review_url(
                "https://oauth2:secret@gitlab.example.com/group/sub/skills.git", "main", branch
            ),
            "https://gitlab.example.com/group/sub/skills/-/merge_requests/new?"
            "merge_request%5Bsource_branch%5D=share%2Fhello-ada",
        )
        for url in ("https://git.example.com/team/skills.git", "/srv/skills.git", "", None):
            self.assertIsNone(r.review_url(url, "main", branch))
        self.assertNotIn(
            "secret", r.review_url("https://x:secret@gitlab.example.com/a/b", "m", branch)
        )
        self.assertEqual(
            r.redact("https://user:token@github.com/team/skills.git"),
            "https://github.com/team/skills.git",
        )
        self.assertEqual(r.redact("ssh://git:pw@host.example/x"), "ssh://git@host.example/x")
        self.assertEqual(
            r.redact("git@github.com:team/skills.git"), "git@github.com:team/skills.git"
        )
        self.assertIsNone(r.redact(""))


class KeepTests(ExtendedCopyFixture):
    def test_keep_renames_records_version_and_tracks_team_changes(self):
        self.extended()
        self.skill(self.repo / ".kiro/skills/taken")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "hello", "link_team": True}
        for new_name, reason in (("taken", "taken"), ("hello", "invalid"), ("bad name", "invalid")):
            _, blocked = self.request("/api/keep-plan", {**body, "new_name": new_name})
            self.assertFalse(blocked["can_apply"])
            self.assertIn(reason, [x["reason"] for x in blocked["conflicts"]])
        code, plan = self.request("/api/keep-plan", {**body, "new_name": "hello-mine"})
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        ops = [(x["op"], x["path"].replace(str(self.repo) + "/", "")) for x in plan["changes"]]
        self.assertEqual(
            ops,
            [
                ("rename", ".kiro/skills/hello-mine"),
                ("archive", ".kiro/skills/hello"),
                ("link", ".kiro/skills/hello"),
            ],
        )
        self.assertEqual(plan["changes"][0]["edit"], "name: hello -> hello-mine")
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 200)
        mine = (self.repo / ".kiro/skills/hello-mine/SKILL.md").read_text()
        self.assertTrue(mine.startswith("---\nname: hello-mine\n"))
        self.assertIn("My project rules", mine)
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        head = out(self.lib, "rev-parse", "HEAD")
        saved = json.loads((self.repo / ".captain/sharing.json").read_text())
        self.assertEqual(
            saved["versions"],
            {
                "skills/hello-mine": {
                    "source": "team",
                    "kind": "skills",
                    "name": "hello",
                    "base": head,
                }
            },
        )
        self.assertIn("team/skills/hello", saved["inbound"])
        _, found = self.request("/api/share-candidates")
        (version,) = found["versions"]
        self.assertEqual(
            {k: version[k] for k in ("key", "name", "path", "of", "base", "team_changed")},
            {
                "key": "skills/hello-mine",
                "name": "hello-mine",
                "path": ".kiro/skills/hello-mine",
                "of": "hello",
                "base": head,
                "team_changed": False,
            },
        )
        self.skill(self.lib / "ip/skills/hello", "Team improvement")
        self.commit(self.lib, "Improve hello")
        _, found = self.request("/api/share-candidates")
        self.assertEqual(
            (found["versions"][0]["team_changed"], found["versions"][0]["team_files"]),
            (True, ["ip/skills/hello/SKILL.md"]),
        )
        code, changed = self.request("/api/version-diff?key=" + quote("skills/hello-mine"))
        self.assertEqual(code, 200, changed)
        (row,) = changed["files"]
        self.assertEqual((row["path"], row["status"]), ("SKILL.md", "changed"))
        self.assertIn("+Team improvement", row["diff"])
        self.assertEqual(self.request("/api/version-diff?key=skills/none")[0], 400)
        _, home = self.request("/api/home")
        hello = next(a for a in home["assets"] if a["key"] == "team/skills/hello")
        self.assertEqual(
            hello["versions"],
            [
                {
                    "key": "skills/hello-mine",
                    "name": "hello-mine",
                    "place": "project",
                    "team_changed": True,
                }
            ],
        )

    def test_keep_without_team_link_and_invalid_versions_config(self):
        self.extended()
        planned = e.plan(
            self.repo,
            action="keep",
            kind="skills",
            asset="hello",
            source="team",
            new_name="hello-local",
            link_team=False,
        )
        self.assertEqual([x["op"] for x in planned["changes"]], ["rename", "archive"])
        e.apply(planned)
        self.assertFalse((self.repo / ".kiro/skills/hello").exists())
        self.assertNotIn("team/skills/hello", c.effective(self.repo)["inbound"])
        cfg_path = self.repo / ".captain/sharing.json"
        data = json.loads(cfg_path.read_text())
        data["versions"]["skills/x"] = {
            "source": "team",
            "kind": "agents",
            "name": "x",
            "base": None,
        }
        with self.assertRaises(c.ShareError):
            c.validate(data, cfg_path)


class ExtendedCliTests(ExtendedCopyFixture):
    # Borrowed without importing CliTests, which would run its tests here again.
    run_cli = test_library_home.CliTests.run_cli

    def test_diff_propose_keep_and_versions(self):
        self.remote()
        self.extended()
        base = ["--target", str(self.repo)]
        code, text, _ = self.run_cli(*base, "diff", "skills", "hello", "--source", "team")
        self.assertEqual(code, 0)
        self.assertIn("+My project rules", text)
        code, text, _ = self.run_cli(
            *base, "propose", "skills", "hello", "--source", "team", "--review"
        )
        self.assertEqual(code, 0, text)
        self.assertIn("Review branch: share/hello-test-sharer", text)
        code, text, _ = self.run_cli(
            *base,
            "propose",
            "skills",
            "hello",
            "--source",
            "team",
            "--review",
            "--note",
            "Project rules",
            "--push",
            "--apply",
        )
        self.assertEqual(code, 0, text)
        self.assertIn("on share/hello-test-sharer", text)
        code, text, _ = self.run_cli(
            *base, "keep", "skills", "hello", "--source", "team", "--as", "hello-mine", "--apply"
        )
        self.assertEqual(code, 0, text)
        self.assertTrue((self.repo / ".kiro/skills/hello-mine").is_dir())
        code, text, _ = self.run_cli(*base, "versions")
        self.assertEqual(code, 0)
        self.assertIn("skills/hello-mine", text)
        self.assertIn("up to date with team/skills/hello", text)
