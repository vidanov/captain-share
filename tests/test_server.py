import http.client
import json
import subprocess
import threading
from unittest.mock import patch
from urllib.parse import quote

from captain_share import __version__
from captain_share import config as c
from captain_share.server import make_server
from tests.test_sharing import SharingFixture


class ServerTests(SharingFixture):
    def setUp(self):
        super().setUp()
        self.server, self.token = make_server(self.repo)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path, data=None, token=True, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {}
        if token:
            headers["X-Captain-Token"] = self.token
        if origin:
            headers["Origin"] = origin
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn.request(
            "POST" if data is not None else "GET",
            path,
            body=json.dumps(data) if data is not None else None,
            headers=headers,
        )
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, json.loads(body)

    def test_folder_picker_lists_directories_and_preserves_filesystem(self):
        (self.repo / "folder with spaces").mkdir()
        (self.repo / ".hidden-folder").mkdir()
        (self.repo / "file.txt").write_text("not a folder")
        before = set(self.repo.rglob("*"))
        code, listing = self.request("/api/folders")
        self.assertEqual(code, 200)
        self.assertEqual(listing["path"], str(self.repo))
        names = [row["name"] for row in listing["folders"]]
        self.assertIn("folder with spaces", names)
        self.assertNotIn(".hidden-folder", names)
        self.assertNotIn("file.txt", names)
        _, hidden = self.request("/api/folders?hidden=1")
        self.assertIn(".hidden-folder", [row["name"] for row in hidden["folders"]])
        _, child = self.request("/api/folders?path=" + quote(str(self.repo / "folder with spaces")))
        self.assertEqual(child["parent"], str(self.repo))
        for invalid in (self.repo / "missing", self.repo / "file.txt"):
            self.assertEqual(self.request("/api/folders?path=" + quote(str(invalid)))[0], 400)
        self.assertEqual(self.request("/api/folders", token=False)[0], 403)
        self.assertEqual(self.request("/api/folders", origin="https://outside.example")[0], 403)
        self.assertEqual(set(self.repo.rglob("*")), before)

    def test_about_deep_link_serves_packaged_version_and_license(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            conn.request("GET", "/about")
            response = conn.getresponse()
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("Version " + __version__, body)
            self.assertNotIn("__APP_VERSION__", body)
            self.assertIn("MIT License", body)
            self.assertIn('id="about-page"', body)
            self.assertNotIn(str(self.repo), body)
        finally:
            conn.close()

    def test_reveal_uses_finder_without_following_leaf_link_and_checks_scope(self):
        link = self.repo / "shortcut"
        link.symlink_to(self.lib / "ip/skills/hello")
        with (
            patch("captain_share.server.sys.platform", "darwin"),
            patch("captain_share.server.subprocess.run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            code, _ = self.request("/api/reveal", {"path": str(link)})
            self.assertEqual(code, 200)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-R", str(link)])
            self.assertEqual(self.request("/api/reveal", {"path": str(self.lib)})[0], 200)
            run.reset_mock()
            for value in (
                str(self.base),
                str(self.repo / "missing"),
                "relative",
                str(self.repo / ".." / ".."),
            ):
                self.assertEqual(self.request("/api/reveal", {"path": value})[0], 400)
            self.assertEqual(self.request("/api/reveal", {"path": str(link)}, token=False)[0], 403)
            self.assertEqual(
                self.request("/api/reveal", {"path": str(link)}, origin="https://outside.example")[
                    0
                ],
                403,
            )
            run.assert_not_called()

    def test_api_requires_launch_token_and_same_origin(self):
        self.assertEqual(self.request("/api/state", token=False)[0], 403)
        self.assertEqual(self.request("/api/state", origin="https://outside.example")[0], 403)
        self.assertEqual(self.request("/api/state")[0], 200)

    def test_api_selection_preview_apply_archive_restore(self):
        code, _ = self.request(
            "/api/selections",
            {
                "direction": "inbound",
                "source": "team",
                "selections": [{"kind": "skills", "name": "hello", "alias": "web-hello"}],
            },
        )
        self.assertEqual(code, 200)
        code, plan = self.request("/api/plan", {"direction": "inbound"})
        self.assertTrue(plan["can_apply"])
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 200)
        self.assertTrue((self.repo / ".kiro/skills/web-hello").is_symlink())
        _, remove = self.request(
            "/api/plan", {"action": "remove", "kind": "skills", "name": "web-hello"}
        )
        _, result = self.request("/api/apply", {"fingerprint": remove["fingerprint"]})
        _, restore = self.request(
            "/api/plan", {"action": "restore", "id": result["archives"][0]["id"]}
        )
        self.assertEqual(
            self.request("/api/apply", {"fingerprint": restore["fingerprint"]})[0], 200
        )

    def test_api_cancelled_selection_save_is_restored(self):
        def inbound():
            return json.loads((self.repo / ".captain/sharing.json").read_text())["inbound"]

        self.request(
            "/api/selections",
            {
                "direction": "inbound",
                "source": "team",
                "selections": [{"kind": "skills", "name": "hello"}],
            },
        )
        before = inbound()
        _, saved = self.request(
            "/api/selections",
            {
                "direction": "inbound",
                "source": "team",
                "selections": [
                    {"kind": "skills", "name": "hello", "enabled": False},
                    {"kind": "skills", "name": "other"},
                ],
            },
        )
        self.assertEqual(saved["previous"]["team/skills/other"], None)
        code, _ = self.request(
            "/api/restore-selections",
            {"direction": "inbound", "previous": saved["previous"]},
        )
        self.assertEqual((code, inbound()), (200, before))
        self.assertEqual(
            self.request("/api/restore-selections", {"direction": "sideways", "previous": {}})[0],
            400,
        )

    def test_api_tools_and_conflict_resolution_by_path(self):
        self.assertEqual(self.request("/api/tools", {"tools": ["cursor"]})[0], 400)
        code, saved = self.request("/api/tools", {"tools": ["claude", "kiro"]})
        self.assertEqual((code, saved["tools"]), (200, ["kiro", "claude"]))
        _, state = self.request("/api/state")
        self.assertEqual(state["config"]["tools"], ["kiro", "claude"])
        self.assertIn("claude", state["destinations"]["skills"])
        self.skill(self.repo / ".claude/skills/hello", "local")
        self.request(
            "/api/selections",
            {
                "direction": "inbound",
                "source": "team",
                "selections": [{"kind": "skills", "name": "hello"}],
            },
        )
        _, catalog = self.request("/api/catalog?source=team")
        self.assertEqual([d["state"] for d in catalog[0]["destinations"]], ["absent", "blocked"])
        _, blocked = self.request("/api/plan", {"direction": "inbound"})
        self.assertEqual(blocked["conflicts"][0]["path"], ".claude/skills/hello")
        _, archive = self.request(
            "/api/plan",
            {"action": "remove", "kind": "skills", "name": "hello", "path": ".claude/skills/hello"},
        )
        self.assertEqual(len(archive["changes"]), 1)
        self.request("/api/apply", {"fingerprint": archive["fingerprint"]})
        _, plan = self.request("/api/plan", {"direction": "inbound"})
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 200)
        self.assertTrue((self.repo / ".claude/skills/hello").is_symlink())
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())

    def test_library_lookup_and_picker_mark_library_folders(self):
        _, info = self.request("/api/library?path=" + quote(str(self.lib / "ip")))
        self.assertEqual(info["path"], str(self.lib))
        self.assertEqual((info["layout"], info["assets"]), ("ip", {"skills": 1}))
        self.assertEqual(info["connected_as"], "team")
        other = self.base / "Other Tools"
        (other / "agents").mkdir(parents=True)
        (other / "agents/helper.json").write_text("{}")
        _, info = self.request("/api/library?path=" + quote(str(other)))
        self.assertEqual(
            (info["name"], info["layout"], info["connected_as"]), ("other-tools", "flat", None)
        )
        _, listing = self.request("/api/folders?path=" + quote(str(self.base)))
        marks = {row["name"]: row["library"] for row in listing["folders"]}
        self.assertEqual(
            (marks["library"], marks["Other Tools"], marks["project"]), ("ip", "flat", None)
        )
        _, inside = self.request("/api/folders?path=" + quote(str(self.lib)))
        self.assertEqual(inside["library"], "ip")
        self.assertTrue(all(row["library"] is None for row in inside["folders"]))
        self.assertEqual(self.request("/api/library?path=relative")[0], 400)
        self.assertEqual(self.request("/api/source", {"name": "other", "path": str(other)})[0], 200)
        self.assertEqual(self.request("/api/catalog?source=other")[1][0]["name"], "helper.json")

    def select_at(self, target, name="hello"):
        return self.request(
            "/api/selections",
            {
                "target": str(target),
                "direction": "inbound",
                "source": "team",
                "selections": [{"kind": "skills", "name": name}],
            },
        )

    def test_window_acts_only_on_its_places(self):
        home, folder = self.base, self.repo.parent  # CAPTAIN_SHARE_HOME is <base>/global
        _, state = self.request("/api/state")
        kinds = {row["path"]: row["kind"] for row in state["places"]}
        self.assertEqual(
            kinds, {str(self.repo): "project", str(folder): "folder", str(home): "user"}
        )
        outside = self.base.parent
        self.assertEqual(self.request("/api/state?target=" + quote(str(outside)))[0], 400)
        code, _ = self.request(
            "/api/selections",
            {"target": str(outside), "direction": "inbound", "source": "team", "selections": []},
        )
        self.assertEqual(code, 400)
        self.assertEqual(
            self.request("/api/state?target=" + quote(str(home)))[1]["target"], str(home)
        )

    def test_folder_template_is_inherited_and_user_installs_are_marked(self):
        # As in real use, the home folder's .captain is the global layer.
        with patch.dict("os.environ", {"CAPTAIN_SHARE_HOME": str(self.base / ".captain")}):
            self._folder_template_and_user_installs()

    def _folder_template_and_user_installs(self):
        folder = self.repo.parent
        c.add_source(self.base, "team", self.lib)
        self.assertEqual(self.select_at(folder)[0], 200)
        _, state = self.request("/api/state")
        self.assertIn("team/skills/hello", state["config"]["inbound"])
        self.assertEqual(state["config"]["origins"]["inbound"]["team/skills/hello"], str(folder))
        _, plan = self.request("/api/plan", {"direction": "inbound"})
        self.assertEqual(len(plan["changes"]), 1)
        self.skill(self.lib / "ip/skills/personal")
        self.select_at(self.base, "personal")
        _, home_plan = self.request("/api/plan", {"target": str(self.base), "direction": "inbound"})
        self.request("/api/apply", {"fingerprint": home_plan["fingerprint"]})
        _, state = self.request("/api/state")
        self.assertEqual(state["user_installed"], ["team/skills/personal"])
        self.assertNotIn("team/skills/personal", state["config"]["inbound"])

    def test_registered_projects_sync_in_one_batch(self):
        other = self.base / "other"
        other.mkdir()
        code, registered = self.request("/api/projects", {"path": str(other)})
        self.assertEqual((code, registered["name"]), (200, "other"))
        for target in (self.repo, other):
            c.add_source(target, "team", self.lib)
            self.select_at(target)
        _, rows = self.request("/api/projects")
        self.assertEqual(
            {r["path"]: r["changes"] for r in rows}, {str(self.repo): 1, str(other): 1}
        )
        _, first = self.request("/api/plan", {"target": str(self.repo), "direction": "inbound"})
        _, second = self.request(
            "/api/plan", {"target": str(other), "direction": "inbound", "batch": True}
        )
        for plan in (first, second):
            self.assertEqual(
                self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 200
            )
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        self.assertTrue((other / ".kiro/skills/hello").is_symlink())
        _, rows = self.request("/api/projects")
        self.assertEqual({r["changes"] for r in rows}, {0})

    def test_invalid_batch_does_not_save_partial_selection(self):
        code, _ = self.request(
            "/api/selections",
            {
                "direction": "inbound",
                "source": "team",
                "selections": [
                    {"kind": "skills", "name": "hello"},
                    {"kind": "skills", "name": "../bad"},
                ],
            },
        )
        self.assertEqual(code, 400)
        _, state = self.request("/api/state")
        self.assertEqual(state["config"]["inbound"], {})

    def test_duplicate_outbound_originals_do_not_overwrite_selection_silently(self):
        self.skill(self.repo / "skills/duplicate")
        self.skill(self.repo / ".kiro/skills/duplicate")
        body = {
            "direction": "outbound",
            "source": "team",
            "selections": [
                {
                    "kind": "skills",
                    "name": "duplicate",
                    "path": "skills/duplicate",
                    "enabled": True,
                },
                {
                    "kind": "skills",
                    "name": "duplicate",
                    "path": ".kiro/skills/duplicate",
                    "enabled": True,
                },
            ],
        }
        self.assertEqual(self.request("/api/selections", body)[0], 400)
        body["selections"][1]["enabled"] = False
        self.assertEqual(self.request("/api/selections", body)[0], 200)
        _, state = self.request("/api/state")
        self.assertEqual(
            state["config"]["outbound"]["team/skills/duplicate"]["path"], "skills/duplicate"
        )
