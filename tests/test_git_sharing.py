import subprocess
import threading
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import repositories as r
from captain_share.server import make_server
from tests import test_server
from tests.test_sharing import SharingFixture


class GitSharingTests(SharingFixture):
    def commit(self, path, message="fixture"):
        e.git(path, "add", ".")
        e.git(
            path,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    def fixture_remote(self):
        e.git(self.lib, "init", "-b", "main")
        self.commit(self.lib)
        remote = self.base / "remote.git"
        e.git(self.base, "clone", "--bare", str(self.lib), str(remote))
        dest = self.base / "cloned"
        r.clone(self.repo, "cloned", str(remote), str(dest))
        return remote, dest

    def test_clone_connect_status_push_and_fast_forward_pull(self):
        remote, dest = self.fixture_remote()
        self.assertEqual(c.effective(self.repo)["sources"]["cloned"]["path"], str(dest))
        self.assertTrue(r.status(self.repo, "cloned")["clean"])
        (dest / "new.md").write_text("new content")
        state = r.status(self.repo, "cloned")
        self.assertEqual(state["changes"], [{"status": "??", "path": "new.md"}])
        with self.assertRaisesRegex(c.ShareError, "Commit or stash"):
            r.operate(self.repo, "cloned", "pull", state["fingerprint"])
        self.commit(dest)
        state = r.status(self.repo, "cloned")
        self.assertEqual(state["ahead"], 1)
        r.operate(self.repo, "cloned", "push", state["fingerprint"])
        self.assertEqual(
            e.git(remote, "rev-parse", "main").stdout, e.git(dest, "rev-parse", "HEAD").stdout
        )
        self.assertEqual(r.status(self.repo, "cloned")["ahead"], 0)
        other = self.base / "colleague"
        e.git(self.base, "clone", str(remote), str(other))
        (other / "colleague.md").write_text("new work")
        self.commit(other)
        e.git(other, "push")
        state = r.status(self.repo, "cloned")
        r.operate(self.repo, "cloned", "pull", state["fingerprint"])
        self.assertTrue((dest / "colleague.md").exists())

    def test_git_rejects_stale_preview_and_divergence_without_losing_work(self):
        remote, dest = self.fixture_remote()
        state = r.status(self.repo, "cloned")
        (dest / "local.md").write_text("local")
        self.commit(dest)
        with self.assertRaisesRegex(c.ShareError, "changed"):
            r.operate(self.repo, "cloned", "push", state["fingerprint"])
        (self.lib / "remote.md").write_text("remote")
        self.commit(self.lib)
        e.git(self.lib, "push", str(remote), "main")
        head = e.git(dest, "rev-parse", "HEAD").stdout
        state = r.status(self.repo, "cloned")
        with self.assertRaises(c.ShareError):
            r.operate(self.repo, "cloned", "pull", state["fingerprint"])
        self.assertEqual(e.git(dest, "rev-parse", "HEAD").stdout, head)
        self.assertTrue((dest / "local.md").exists())
        with self.assertRaises(c.ShareError):
            r.operate(self.repo, "cloned", "push", r.status(self.repo, "cloned")["fingerprint"])

    def test_clone_rejects_existing_destination_credentials_and_command_protocol(self):
        for url in (
            "ext::touch /tmp/unwanted",
            "--upload-pack=bad",
            "https://user:pass@example.invalid/repo",
        ):
            with self.assertRaises(c.ShareError):
                r.clone(self.repo, "new", url, str(self.base / "new"))
        with self.assertRaisesRegex(c.ShareError, "must not exist"):
            r.clone(self.repo, "new", str(self.lib), str(self.repo))
        self.assertNotIn("new", c.effective(self.repo)["sources"])
        with patch(
            "captain_share.repositories.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 120),
        ):
            with self.assertRaisesRegex(c.ShareError, "timed out"):
                r.run(self.repo, "fetch")


class GitApiTests(SharingFixture):
    # Use server helpers without inheriting and rerunning its full test suite.
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

    request = test_server.ServerTests.request

    def test_repository_api_and_protected_mutations(self):
        self.assertEqual(self.request("/api/repository?source=team")[1]["is_repo"], False)
        self.assertEqual(self.request("/api/repository?source=missing")[0], 400)
        for endpoint in ("/api/git", "/api/clone"):
            self.assertEqual(self.request(endpoint, {}, token=False)[0], 403)
            self.assertEqual(self.request(endpoint, {}, origin="https://outside.example")[0], 403)
        self.assertEqual(self.request("/api/git", {"action": "delete", "fingerprint": "x"})[0], 400)
