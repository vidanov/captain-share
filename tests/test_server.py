import http.client
import json
import threading
from urllib.parse import quote

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
