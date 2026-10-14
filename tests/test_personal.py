import json
import os
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import personal as me
from tests import test_library_home
from tests.test_library_home import LibraryFixture


def out(path, *args):
    return e.git(path, *args).stdout.decode().strip()


class PersonalFixture(LibraryFixture):
    """Git's global configuration lives in the disposable home folder."""

    def setUp(self):
        super().setUp()
        self.git_home = patch.dict(
            os.environ, {"HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / ".config")}
        )
        self.git_home.start()
        self.identity()
        self.mine = self.base / "Libraries/my-skills"

    def tearDown(self):
        self.git_home.stop()
        super().tearDown()

    def identity(self, present=True):
        config = self.base / ".gitconfig"
        if present:
            config.write_text(
                "[user]\n\tname = Ada Example\n\temail = ada@example.invalid\n"
                "[commit]\n\tgpgsign = false\n"
            )
        elif config.exists():
            config.unlink()

    def outside(self, name="tryout"):
        folder = self.base / f"downloads-{name}" / name
        self.skill(folder)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Try me\n---\nSteps")
        return folder.parent


class CreateTests(PersonalFixture):
    def test_creation_is_idempotent_and_refuses_other_folders(self):
        self.start()
        code, made = self.request("/api/personal-library", {})
        self.assertEqual(code, 200, made)
        self.assertEqual(made, {"source": "my-skills", "path": str(self.mine), "created": True})
        self.assertEqual(c.detect_layout(self.mine), "flat")
        self.assertEqual(out(self.mine, "log", "--format=%an %s"), "Ada Example Start my skills")
        self.assertEqual(out(self.mine, "status", "--porcelain"), "")
        self.assertEqual(out(self.mine, "remote"), "")
        for folder in ("skills", "agents", "prompts"):
            self.assertTrue((self.mine / folder / ".gitkeep").exists())
        sources = c.effective(self.home)["sources"]
        self.assertEqual(sources["my-skills"]["layout"], "flat")
        self.assertIn("my-skills", c.effective(self.repo)["sources"])
        code, again = self.request("/api/personal-library", {})
        self.assertEqual((code, again["created"]), (200, False))
        self.assertEqual(len(out(self.mine, "log", "--format=%H").split()), 1)

    def test_non_empty_folder_and_missing_identity_are_refused(self):
        self.mine.mkdir(parents=True)
        (self.mine / "notes.txt").write_text("something else")
        with self.assertRaisesRegex(c.ShareError, "not a library"):
            me.ensure()
        self.assertEqual([p.name for p in self.mine.iterdir()], ["notes.txt"])
        (self.mine / "notes.txt").unlink()
        self.identity(present=False)
        with self.assertRaisesRegex(c.ShareError, "name and email"):
            me.ensure()
        self.assertEqual(list(self.mine.iterdir()), [])
        self.assertNotIn("my-skills", c.effective(self.home)["sources"])
        self.identity()
        self.assertTrue(me.ensure()["created"])

    def test_an_existing_library_checkout_is_connected(self):
        self.mine.mkdir(parents=True)
        e.git(self.mine, "init", "-q", "-b", "main")
        self.skill(self.mine / "skills/kept")
        result = me.ensure()
        self.assertEqual(result["created"], False)
        self.assertEqual(c.effective(self.home)["sources"]["my-skills"]["path"], str(self.mine))


class PersonalApiTests(PersonalFixture):
    def import_into_mine(self, name="tryout"):
        _, stage = self.request("/api/import-folder", {"path": str(self.outside(name))})
        (row,) = [x for x in stage["candidates"] if x["name"] == name]
        item = {"id": row["id"], "kind": "skills", "name": name}
        code, plan = self.request(
            "/api/import-plan", {"source": "my-skills", "stage": stage["stage"], "items": [item]}
        )
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        return self.request(
            "/api/import-apply",
            {"fingerprint": plan["fingerprint"], "note": f"Add {name}", "push": False},
        )

    def test_import_home_flags_and_candidates(self):
        self.start()
        self.request("/api/personal-library", {})
        code, result = self.import_into_mine()
        self.assertEqual(code, 200, result)
        self.assertEqual((result["pushed"], result["push_error"]), (False, None))
        self.assertEqual(out(self.mine, "log", "-1", "--format=%s"), "Add tryout")
        self.assertTrue((self.mine / "skills/tryout/SKILL.md").exists())
        _, home = self.request("/api/home")
        libs = {row["source"]: row for row in home["libraries"]}
        self.assertTrue(libs["my-skills"]["personal"])
        self.assertFalse(libs["team"]["personal"])
        self.assertEqual((libs["my-skills"]["behind"], libs["my-skills"]["url"]), (None, None))
        self.skill(self.mine / "skills/later")
        self.commit(self.mine, "Add later")
        self.start()  # A later session still marks nothing new in the personal library.
        _, home = self.request("/api/home")
        mine = [a for a in home["assets"] if a["source"] == "my-skills"]
        self.assertEqual(sorted(a["name"] for a in mine), ["later", "tryout"])
        self.assertFalse(any(a["new"] for a in mine))
        self.skill(self.mine / "skills/hello")
        self.commit(self.mine, "Add hello")
        _, found = self.request("/api/share-candidates")
        rows = {row["name"]: row for row in found["personal"]}
        self.assertEqual(rows["tryout"]["path"], "skills/tryout")
        self.assertEqual(rows["tryout"]["description"], "Try me")
        self.assertIsNone(rows["tryout"]["exists_in"])
        self.assertEqual(rows["hello"]["exists_in"], "team")

    def test_promote_moves_asset_relinks_places_and_archives(self):
        remote = self.remote()
        c.add_source(self.home, "team", self.lib, scope="global")
        self.start()
        self.request("/api/personal-library", {})
        self.import_into_mine()
        for place in (self.home, self.repo):
            c.select(place, "inbound", "my-skills", "skills", "tryout")
            e.apply(e.plan(place))
        mine_link = str(self.mine / "skills/tryout")
        self.assertEqual(os.readlink(self.home / ".kiro/skills/tryout"), mine_link)
        body = {"kind": "skills", "name": "tryout", "team": "team"}
        code, plan = self.request("/api/promote-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        ops = [(row["op"], row.get("label")) for row in plan["changes"]]
        self.assertEqual(
            ops,
            [("copy", None), ("relink", "repo"), ("relink", "Your user"), ("archive", None)],
        )
        self.assertEqual(plan["changes"][1]["links"], [".kiro/skills/tryout"])
        self.assertTrue(all(row.get("bound", True) for row in plan["changes"]))
        self.assertEqual(plan["commit_paths"], ["ip/skills/tryout"])
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        code, result = self.request(
            "/api/promote-apply",
            {"fingerprint": plan["fingerprint"], "note": "Share tryout", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            set(result),
            {"commit", "pushed", "push_error", "relinked", "removed", "errors", "branch"}
            | {"review_url", "push_error_hint", "push_error_command", "waiting_for_review"}
            | {"library", "landed_on", "checked"},
        )
        self.assertEqual((result["pushed"], result["removed"], result["errors"]), (True, True, []))
        self.assertEqual(sorted(result["relinked"]), sorted([str(self.repo), str(self.home)]))
        self.assertEqual(out(remote, "rev-parse", "main"), result["commit"])
        team_copy = str(self.lib / "ip/skills/tryout")
        for place in (self.home, self.repo):
            self.assertEqual(os.readlink(place / ".kiro/skills/tryout"), team_copy)
            inbound = c.effective(place)["inbound"]
            self.assertIn("team/skills/tryout", inbound)
            self.assertNotIn("my-skills/skills/tryout", inbound)
            self.assertEqual(e.plan(place)["changes"], [])
        self.assertFalse((self.mine / "skills/tryout").exists())
        self.assertEqual(out(self.mine, "log", "-1", "--format=%s"), "Moved tryout to team")
        self.assertEqual(out(self.mine, "status", "--porcelain"), "")
        (record,) = e.archives(self.mine)
        self.assertEqual(record["relative_path"], "skills/tryout")

    def test_promote_conflicts_and_stale_preview(self):
        self.start()
        self.request("/api/personal-library", {})
        self.skill(self.mine / "skills/used")
        self.commit(self.mine, "Used at home")
        c.select(self.home, "inbound", "my-skills", "skills", "used")
        _, unbound = self.request(
            "/api/promote-plan", {"kind": "skills", "name": "used", "team": "team"}
        )
        self.assertEqual(
            [(x["reason"], x["message"]) for x in unbound["conflicts"]],
            [("unbound", "team is not connected for Your user; connect it there first")],
        )
        self.skill(self.mine / "skills/hello")
        self.skill(self.mine / "skills/mine")
        self.commit(self.mine, "Two skills")
        _, exists = self.request(
            "/api/promote-plan", {"kind": "skills", "name": "hello", "team": "team"}
        )
        self.assertEqual(exists["conflicts"][0]["reason"], "exists")
        (self.mine / "skills/mine/SKILL.md").write_text("---\nname: mine\n---\nwip")
        _, edited = self.request(
            "/api/promote-plan", {"kind": "skills", "name": "mine", "team": "team"}
        )
        self.assertIn("edited", [x["reason"] for x in edited["conflicts"]])
        self.commit(self.mine, "Edit mine")
        _, plan = self.request(
            "/api/promote-plan", {"kind": "skills", "name": "mine", "team": "team"}
        )
        self.assertTrue(plan["can_apply"], plan["errors"])
        (self.mine / "skills/mine/extra.md").write_text("after preview")
        code, stale = self.request(
            "/api/promote-apply", {"fingerprint": plan["fingerprint"], "note": "Share"}
        )
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        self.assertFalse((self.lib / "ip/skills/mine").exists())
        for bad in (
            {"kind": "skills", "name": "absent", "team": "team"},
            {"kind": "skills", "name": "mine", "team": "my-skills"},
        ):
            self.assertEqual(self.request("/api/promote-plan", bad)[0], 400)


class PersonalCliTests(PersonalFixture):
    run_cli = test_library_home.CliTests.run_cli

    def test_install_and_promote(self):
        self.remote()
        c.add_source(self.home, "team", self.lib, scope="global")
        downloads = self.outside()
        base = ["--target", str(self.repo)]
        code, text, _ = self.run_cli(*base, "install", str(downloads))
        self.assertEqual(code, 0, text)
        self.assertIn("Run with --apply", text)
        self.assertFalse(self.mine.exists())
        code, text, _ = self.run_cli(*base, "install", str(downloads), "--apply")
        self.assertEqual(code, 0, text)
        self.assertIn("installed for your user", text)
        link = self.home / ".kiro/skills/tryout"
        self.assertEqual(os.readlink(link), str(self.mine / "skills/tryout"))
        code, text, _ = self.run_cli(
            *base, "--json", "install", str(self.outside("other")), "--here", "--apply"
        )
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)["installed"], ["skills/other"])
        self.assertTrue((self.repo / ".kiro/skills/other").is_symlink())
        code, text, _ = self.run_cli(*base, "promote", "skills", "tryout", "--to", "team")
        self.assertEqual(code, 0, text)
        self.assertIn("relink   Your user", text)
        code, text, _ = self.run_cli(
            *base,
            "promote",
            "skills",
            "tryout",
            "--to",
            "team",
            "--note",
            "Share",
            "--push",
            "--apply",
        )
        self.assertEqual(code, 0, text)
        self.assertIn("Pushed.", text)
        self.assertEqual(os.readlink(link), str(self.lib / "ip/skills/tryout"))
