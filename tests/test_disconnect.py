import json
import os
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import integrations as i
from captain_share import personal as me
from tests import test_library_home
from tests.test_library_home import LibraryFixture
from tests.test_personal import PersonalFixture


def own(place, name="sharing.json"):
    path = place / ".captain" / name
    return json.loads(path.read_text()) if path.exists() else {}


class DisconnectFixture(LibraryFixture):
    def connect_everywhere(self):
        """team at the user (global) and the project, with hello linked at both."""
        c.add_source(self.home, "team", self.lib, scope="global")
        for place in (self.home, self.repo):
            c.select(place, "inbound", "team", "skills", "hello")
            e.apply(e.plan(place))
        self.assertTrue((self.home / ".kiro/skills/hello").is_symlink())
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())


class DisconnectApiTests(DisconnectFixture):
    def test_disconnect_archives_links_and_drops_selections_and_bindings(self):
        self.connect_everywhere()
        self.skill(self.repo / ".claude/skills/hello", "my own copy")
        library_files = sorted(p for p in self.lib.rglob("*") if ".git" not in p.parts)
        self.start()
        code, plan = self.request("/api/disconnect-plan", {"source": "team"})
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual(plan["folder"], str(self.lib))
        ops = sorted((row["op"], row["place_label"]) for row in plan["changes"])
        self.assertEqual(
            ops,
            sorted(
                [
                    ("unlink", "repo"),
                    ("deselect", "repo"),
                    ("disconnect", "repo"),
                    ("disconnect", "repo"),
                    ("unlink", "Your user"),
                    ("deselect", "Your user"),
                    ("disconnect", "Your user"),
                    ("disconnect", "Your user"),
                ]
            ),
        )
        self.assertEqual({row["label"] for row in plan["places"]}, {"repo", "Your user"})
        self.assertEqual(
            plan["kept"],
            [
                {
                    "reason": "copy",
                    "path": ".claude/skills/hello",
                    "place": str(self.repo),
                    "place_label": "repo",
                }
            ],
        )
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        code, result = self.request("/api/disconnect-apply", {"fingerprint": plan["fingerprint"]})
        self.assertEqual(code, 200, result)
        self.assertEqual(set(result), {"disconnected", "places", "archives", "kept"})
        self.assertEqual(result["disconnected"], "team")
        self.assertEqual(sorted(result["places"]), sorted([str(self.repo), str(self.home)]))
        self.assertEqual(len(result["archives"]), 2)
        for place in (self.home, self.repo):
            self.assertFalse(os.path.lexists(place / ".kiro/skills/hello"))
            self.assertNotIn("team", own(place).get("sources", {}))
            self.assertNotIn("team", own(place, "sharing.local.json").get("sources", {}))
            self.assertFalse(any(k.startswith("team/") for k in own(place).get("inbound", {})))
            later = e.plan(place)
            self.assertEqual((later["changes"], later["can_apply"]), ([], True))
            self.assertNotIn("team", c.effective(place)["sources"])
        self.assertTrue((self.repo / ".claude/skills/hello/SKILL.md").exists())
        self.assertEqual(
            sorted(p for p in self.lib.rglob("*") if ".git" not in p.parts), library_files
        )
        self.assertEqual(e.git(self.lib, "status", "--porcelain").stdout, b"")
        # The archived link is recoverable like any other archive.
        (record,) = e.archives(self.repo)
        self.assertEqual(record["relative_path"], ".kiro/skills/hello")
        self.assertEqual(self.request("/api/disconnect-plan", {"source": "team"})[0], 400)

    def test_installed_power_is_kept_and_kiro_is_not_called(self):
        c.add_source(self.home, "team", self.lib, scope="global")
        power = self.lib / "ip/powers/demo"
        power.mkdir(parents=True)
        (power / "POWER.md").write_text("---\nname: demo\n---\nA power")
        installed = self.base / "kiro-installed"
        (installed / "demo").mkdir(parents=True)
        (installed / "demo/POWER.md").write_text("---\nname: demo\n---\nA power")
        with (
            patch.object(i, "kiro_powers_dir", return_value=installed),
            patch.object(i, "power_apply") as run,
        ):
            self.start()
            _, plan = self.request("/api/disconnect-plan", {"source": "team"})
            self.assertEqual(
                [row for row in plan["kept"] if row["reason"] == "power"],
                [
                    {
                        "reason": "power",
                        "name": "demo",
                        "path": str(installed / "demo"),
                        "state": "current",
                        "command": "captain-share powers uninstall team demo",
                    }
                ],
            )
            code, _ = self.request("/api/disconnect-apply", {"fingerprint": plan["fingerprint"]})
            self.assertEqual(code, 200)
            run.assert_not_called()
        self.assertTrue((installed / "demo/POWER.md").exists())

    def test_changed_link_elsewhere_and_stale_plan_are_refused(self):
        self.connect_everywhere()
        link = self.repo / ".kiro/skills/hello"
        link.unlink()
        link.symlink_to(self.base / "somewhere-else")
        self.start()
        _, changed = self.request("/api/disconnect-plan", {"source": "team"})
        self.assertFalse(changed["can_apply"])
        self.assertEqual(
            [(row["reason"], row["place_label"]) for row in changed["conflicts"]],
            [("changed", "repo")],
        )
        link.unlink()
        link.symlink_to(self.lib / "ip/skills/hello")
        _, plan = self.request("/api/disconnect-plan", {"source": "team"})
        self.assertTrue(plan["can_apply"], plan["errors"])
        c.select(self.repo, "inbound", "team", "skills", "hello", alias="hello-two")
        code, stale = self.request("/api/disconnect-apply", {"fingerprint": plan["fingerprint"]})
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        self.assertTrue((self.home / ".kiro/skills/hello").is_symlink())
        self.assertIn("team", c.effective(self.home)["sources"])
        # A registered project below a folder this window does not manage.
        outside = self.base / "other/proj"
        outside.mkdir(parents=True)
        self.request("/api/projects", {"path": str(outside)})
        c.add_source(outside.parent, "team", self.lib)
        _, elsewhere = self.request("/api/disconnect-plan", {"source": "team"})
        self.assertIn("elsewhere", [row["reason"] for row in elsewhere["conflicts"]])


class DisconnectPersonalTests(PersonalFixture):
    def test_personal_library_can_be_disconnected_and_its_folder_stays(self):
        me.ensure()
        self.skill(self.mine / "skills/tryout")
        self.commit(self.mine, "Try")
        c.select(self.home, "inbound", "my-skills", "skills", "tryout")
        e.apply(e.plan(self.home))
        self.start()
        _, plan = self.request("/api/disconnect-plan", {"source": "my-skills"})
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual(plan["folder"], str(self.mine))
        code, result = self.request("/api/disconnect-apply", {"fingerprint": plan["fingerprint"]})
        self.assertEqual(code, 200, result)
        self.assertNotIn("my-skills", c.effective(self.home)["sources"])
        self.assertFalse(os.path.lexists(self.home / ".kiro/skills/tryout"))
        self.assertTrue((self.mine / "skills/tryout/SKILL.md").exists())
        # Creating it again reconnects the same folder without a new commit.
        self.assertEqual(me.ensure()["created"], False)


class DisconnectCliTests(DisconnectFixture):
    run_cli = test_library_home.CliTests.run_cli

    def test_preview_then_apply_and_source_remove_still_works(self):
        self.connect_everywhere()
        base = ["--target", str(self.repo), "source", "disconnect", "team"]
        code, text, _ = self.run_cli(*base)
        self.assertEqual(code, 0, text)
        self.assertIn(f"the folder {self.lib} stays", text)
        self.assertIn("unlink", text)
        self.assertIn("Ready. Add --apply to disconnect.", text)
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        code, text, _ = self.run_cli(*base, "--apply")
        self.assertEqual(code, 0, text)
        self.assertIn("Disconnected team: 2 links archived in 2 places", text)
        self.assertFalse(os.path.lexists(self.repo / ".kiro/skills/hello"))
        c.add_source(self.repo, "again", self.lib)
        code, _, _ = self.run_cli("--target", str(self.repo), "source", "remove", "again")
        self.assertEqual(code, 0)
        self.assertEqual(own(self.repo)["sources"]["again"], {"enabled": False})
