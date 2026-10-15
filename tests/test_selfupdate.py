import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import selfupdate as su
from captain_share import server
from tests.test_sharing import cleanup

AUTHOR = ["-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid"]


class SelfUpdateTests(unittest.TestCase):
    """A disposable Captain Share checkout with its own remote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        seed = self.base / "seed"
        (seed / "captain_share").mkdir(parents=True)
        (seed / "captain_share/__init__.py").write_text('__version__ = "0.1.0"\n')
        e.git(seed, "init", "-q", "-b", "main")
        self.commit(seed, "Start")
        self.remote = self.base / "remote.git"
        e.git(self.base, "clone", "-q", "--bare", str(seed), str(self.remote))
        self.app = self.base / "app"
        e.git(self.base, "clone", "-q", str(self.remote), str(self.app))
        package = patch.object(su, "PACKAGE", self.app / "captain_share")
        package.start()
        self.addCleanup(package.stop)

    def tearDown(self):
        cleanup(self.tmp)

    def commit(self, repo, message):
        e.git(repo, "add", "-A")
        e.git(repo, *AUTHOR, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)

    def colleague(self, message="Newer"):
        other = self.base / "other"
        if not other.exists():
            e.git(self.base, "clone", "-q", str(self.remote), str(other))
        (other / "notes.md").write_text(message)
        self.commit(other, message)
        e.git(other, "push", "-q")

    def test_checkout_status_shows_what_is_new_after_a_fetch(self):
        found = su.status()
        self.assertEqual((found["kind"], found["path"]), ("checkout", str(self.app)))
        self.assertEqual((found["behind"], found["ahead"], found["dirty"]), (0, 0, False))
        self.colleague("Fix the review sheet")
        self.assertEqual(su.status()["behind"], 0)  # nothing asked of the server yet
        found = su.status(fetch=True)
        self.assertEqual(found["behind"], 1)
        self.assertTrue(found["incoming"][0].endswith("Fix the review sheet"))
        self.assertIn("pull --ff-only", found["command"])

    def test_update_fast_forwards_and_reports_the_versions(self):
        self.colleague()
        before = e.git(self.app, "rev-parse", "--short", "HEAD").stdout.decode().strip()
        result = su.update()
        self.assertTrue(result["updated"])
        self.assertEqual(result["from"], before)
        self.assertTrue((self.app / "notes.md").is_file())
        self.assertFalse(su.update()["updated"])

    def test_update_leaves_local_changes_and_own_commits_alone(self):
        self.colleague()
        (self.app / "captain_share/__init__.py").write_text("# mine\n")
        with self.assertRaisesRegex(c.ShareError, "local changes"):
            su.update()
        self.assertFalse((self.app / "notes.md").exists())
        e.git(self.app, "checkout", "--", ".")
        (self.app / "mine.md").write_text("mine")
        self.commit(self.app, "Mine")
        with self.assertRaisesRegex(c.ShareError, "commits the server does not have"):
            su.update()

    def test_unknown_installation_is_refused_with_a_hint(self):
        loose = self.base / "loose/captain_share"
        loose.mkdir(parents=True)
        with patch.object(su, "PACKAGE", loose), patch.object(su, "direct_url", return_value={}):
            self.assertEqual(su.status()["kind"], "unknown")
            with self.assertRaises(c.ShareError):
                su.update()

    def test_pip_from_git_is_recognized(self):
        record = {
            "url": "https://example.com/team/captain-share.git",
            "vcs_info": {"vcs": "git", "requested_revision": "main", "commit_id": "abc"},
        }
        loose = self.base / "site/captain_share"
        loose.mkdir(parents=True)
        with (
            patch.object(su, "PACKAGE", loose),
            patch.object(su, "direct_url", return_value=record),
        ):
            found = su.status()
        self.assertEqual(found["kind"], "pip-git")
        self.assertIn(
            "pip install --upgrade git+https://example.com/team/captain-share.git@main",
            found["command"],
        )

    def test_restart_command_line_and_token_handover(self):
        with patch.object(sys, "argv", ["/x/captain-share", "--target", "/p", "serve", "--open"]):
            self.assertEqual(
                su.restart_argv(), [sys.executable, "/x/captain-share", "--target", "/p", "serve"]
            )
        with patch.object(sys, "argv", ["/x/captain_share/__main__.py", "serve"]):
            self.assertEqual(su.restart_argv(), [sys.executable, "-m", "captain_share", "serve"])
        handle, path = tempfile.mkstemp(prefix="captain-share-restart-", suffix=".json")
        with os.fdopen(handle, "w") as file:
            json.dump({"port": 8123, "token": "secret"}, file)
        with patch.dict(os.environ, {server.RESTART: path}):
            self.assertEqual(server.restarted(), (8123, "secret"))
            self.assertNotIn(server.RESTART, os.environ)
        self.assertFalse(Path(path).exists())
        # A file outside the temporary folder is never trusted.
        outside = self.base / "restart.json"
        outside.write_text(json.dumps({"port": 1, "token": "x"}))
        with patch.dict(os.environ, {server.RESTART: str(outside)}):
            self.assertEqual(server.restarted(), (None, None))


if __name__ == "__main__":
    unittest.main()


class OneWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        lockdir = self.base / "locks"
        lockdir.mkdir(mode=0o700)
        patcher = patch.object(c, "lock_dir", return_value=lockdir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cleanup, self.tmp)

    def test_a_running_window_is_found_and_a_dead_one_is_not(self):
        import threading

        folder = self.base / "project"
        folder.mkdir()
        srv, token = server.make_server(folder)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.assertIsNone(server.open_window(folder))
        server.remember_window(folder, srv.server_port, token)
        self.assertEqual(
            server.open_window(folder), f"http://127.0.0.1:{srv.server_port}/#token={token}"
        )
        self.assertEqual(os.stat(server.windows_file()).st_mode & 0o077, 0)
        # A wrong token or a process that is gone does not count.
        data = json.loads(server.windows_file().read_text())
        data[str(folder)]["token"] = "wrong"
        server.windows_file().write_text(json.dumps(data))
        self.assertIsNone(server.open_window(folder))
        data[str(folder)].update(token=token, pid=999999)
        server.windows_file().write_text(json.dumps(data))
        self.assertIsNone(server.open_window(folder))

    def test_restart_is_offered_when_the_code_on_disk_changes(self):
        import threading
        import urllib.request

        folder = self.base / "project"
        folder.mkdir()
        with patch.object(su, "running_version", return_value="abc"):
            srv, token = server.make_server(folder)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def needed():
            request = urllib.request.Request(
                f"http://127.0.0.1:{srv.server_port}/api/self",
                headers={"X-Captain-Token": token},
            )
            with urllib.request.urlopen(request, timeout=5) as reply:
                return json.loads(reply.read())["restart_needed"]

        with patch.object(su, "running_version", return_value="abc"):
            self.assertFalse(needed())
        with patch.object(su, "running_version", return_value="def"):
            self.assertTrue(needed())
