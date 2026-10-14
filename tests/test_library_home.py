import contextlib
import io
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from captain_share import cli
from captain_share import config as c
from captain_share import engine as e
from captain_share import repositories as r
from captain_share.server import make_server
from tests import test_server
from tests.test_sharing import SharingFixture

AUTHOR = ["-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid"]


class LibraryFixture(SharingFixture):
    """A Git library connected to a project; the home folder is self.base."""

    def setUp(self):
        super().setUp()
        # As in real use, the home folder's .captain is the global layer.
        self.home_env = patch.dict(os.environ, {"CAPTAIN_SHARE_HOME": str(self.base / ".captain")})
        self.home_env.start()
        self.home, self.servers = self.base, []
        try:
            e.git(self.lib, "init", "-b", "main")
            for key, value in (
                ("user.name", "Test Sharer"),
                ("user.email", "sharer@example.invalid"),
                ("commit.gpgsign", "false"),
            ):
                e.git(self.lib, "config", key, value)
            self.commit(self.lib, "Add hello")
        except BaseException:
            self.tearDown()
            raise

    def tearDown(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join()
        # Stop before the fixture's own patch so the outer environment is restored.
        self.home_env.stop()
        super().tearDown()

    def commit(self, path, message="fixture", author=AUTHOR):
        e.git(path, "add", "-A")
        e.git(path, *author, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        return e.git(path, "rev-parse", "HEAD").stdout.decode().strip()

    def start(self):
        """A new server process: a new session for "new since last looked"."""
        self.server, self.token = make_server(self.repo)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((self.server, thread))

    request = test_server.ServerTests.request

    def remote(self):
        remote = self.base / "remote.git"
        e.git(self.base, "clone", "-q", "--bare", str(self.lib), str(remote))
        e.git(self.lib, "remote", "add", "origin", str(remote))
        e.git(self.lib, "fetch", "-q", "origin")
        e.git(self.lib, "branch", "-q", "--set-upstream-to=origin/main", "main")
        return remote

    def colleague_push(self, remote, rel, text):
        other = self.base / "colleague"
        if not other.exists():
            e.git(self.base, "clone", "-q", str(remote), str(other))
        target = other / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.commit(other, "Colleague change")
        e.git(other, "push", "-q")


class EngineOneOffTests(LibraryFixture):
    def test_explicit_selection_copies_one_asset_without_saving_config(self):
        self.skill(self.repo / "skills/mine")
        self.skill(self.repo / "skills/other")
        c.select(self.repo, "outbound", "team", "skills", "other", path="skills/other")
        row = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        planned = e.plan(self.repo, "outbound", selections={"team/skills/mine": row})
        self.assertEqual([x["op"] for x in planned["changes"]], ["copy"])
        self.assertTrue(planned["changes"][0]["path"].endswith("ip/skills/mine"))
        e.apply(planned)
        self.assertTrue((self.lib / "ip/skills/mine/SKILL.md").exists())
        self.assertFalse((self.lib / "ip/skills/other").exists())
        saved = json.loads((self.repo / ".captain/sharing.json").read_text())
        self.assertEqual(list(saved["outbound"]), ["team/skills/other"])
        # A second share after an edit replaces the earlier copy, archived beside it.
        (self.repo / "skills/mine/SKILL.md").write_text("---\nname: mine\n---\nv2")
        again = e.plan(self.repo, "outbound", selections={"team/skills/mine": row})
        self.assertEqual([x["op"] for x in again["changes"]], ["archive", "copy"])
        (self.repo / "skills/mine/SKILL.md").write_text("---\nname: mine\n---\nv3")
        with self.assertRaisesRegex(c.ShareError, "stale"):
            e.apply(again)

    def test_explicit_selections_are_validated(self):
        row = {"source": "team", "kind": "skills", "name": "x", "path": "skills/x"}
        for direction, selections in (
            ("inbound", {"team/skills/x": row}),
            ("outbound", {"team/skills/y": row}),
            ("outbound", {"team/skills/x": {**row, "path": None}}),
            ("outbound", {}),
        ):
            with self.assertRaises(c.ShareError):
                e.plan(self.repo, direction, selections=selections)


class HomeApiTests(LibraryFixture):
    def test_home_lists_libraries_assets_and_ownership(self):
        (self.lib / "profiles").mkdir()
        (self.lib / "profiles/starter.manifest").write_text("skills: hello\n")
        self.commit(self.lib, "Starter set")
        c.add_source(self.home, "team", self.lib)
        c.select(self.home, "inbound", "team", "skills", "hello")
        c.set_tools(self.home, ["kiro", "claude"])
        e.apply(e.plan(self.home))
        self.start()
        code, home = self.request("/api/home")
        self.assertEqual(code, 200)
        self.assertEqual(set(home), {"user", "project", "agents", "libraries", "assets"})
        self.assertEqual((home["user"], home["project"]), (str(self.home), str(self.repo)))
        self.assertEqual(home["agents"], ["kiro", "claude"])
        (lib,) = home["libraries"]
        self.assertEqual(lib["source"], "team")
        self.assertEqual(lib["path"], str(self.lib))
        self.assertTrue(lib["is_repo"])
        self.assertEqual((lib["branch"], lib["new"], lib["updated"]), ("main", 0, 0))
        self.assertEqual(lib["starter"], ["team/skills/hello"])
        datetime.fromisoformat(lib["last_commit_at"])
        (asset,) = home["assets"]
        self.assertEqual(asset["key"], "team/skills/hello")
        self.assertEqual(asset["description"], "Test helper")
        self.assertEqual(asset["shared_by"], "Ada Example")
        datetime.fromisoformat(asset["shared_at"])
        self.assertEqual(asset["have"], {"user": True, "project": False, "from": None})
        self.assertEqual(asset["linked"], {"user": True, "project": False})
        self.assertEqual((asset["new"], asset["updated"], asset["edited"]), (False, False, False))
        self.assertIsNone(asset["power"])
        # A folder above the project provides it: inherited, not the project's own.
        c.select(self.repo.parent, "inbound", "team", "skills", "hello")
        (self.lib / "ip/skills/hello/SKILL.md").write_text("---\nname: hello\n---\nedited")
        _, home = self.request("/api/home")
        self.assertEqual(
            home["assets"][0]["have"],
            {"user": True, "project": True, "from": str(self.repo.parent)},
        )
        self.assertTrue(home["assets"][0]["edited"])

    def test_your_own_commits_are_not_marked_new(self):
        c.add_source(self.home, "team", self.lib)
        self.start()
        self.request("/api/home")  # first sight
        self.skill(self.lib / "ip/skills/yours")
        # Committed with the library's own identity: the user shared it themselves.
        self.commit(self.lib, "Share yours", author=[])
        self.skill(self.lib / "ip/skills/theirs")
        self.commit(self.lib, "Colleague shares theirs")
        _, home = self.request("/api/home")
        marks = {a["name"]: a["new"] for a in home["assets"]}
        self.assertEqual((marks["yours"], marks["theirs"]), (False, True))
        self.assertEqual(home["libraries"][0]["new"], 1)

    def test_new_and_updated_compare_with_the_previous_session(self):
        c.add_source(self.home, "team", self.lib)
        c.select(self.home, "inbound", "team", "skills", "hello")
        self.start()
        _, first = self.request("/api/home")
        # First sight of a library marks nothing as new.
        self.assertEqual((first["libraries"][0]["new"], first["assets"][0]["new"]), (0, False))
        seen = json.loads((c.global_dir() / "seen.json").read_text())
        head = e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(
            (seen["team"]["head"], seen["team"]["assets"]), (head, ["team/skills/hello"])
        )
        self.skill(self.lib / "ip/skills/fresh")
        self.skill(self.lib / "ip/skills/hello", "Better instructions")
        self.commit(self.lib, "Improve hello, add fresh")
        # What arrives while the window is open (Update library) is marked at once.
        _, same_session = self.request("/api/home")
        marks = {a["name"]: (a["new"], a["updated"]) for a in same_session["assets"]}
        self.assertEqual(marks, {"fresh": (True, False), "hello": (False, True)})
        self.start()  # Next session compares with what the first one saw.
        for _ in range(2):  # Marks stay fixed while this window is open.
            _, later = self.request("/api/home")
            marks = {a["name"]: (a["new"], a["updated"]) for a in later["assets"]}
            self.assertEqual(marks, {"fresh": (True, False), "hello": (False, True)})
            self.assertEqual(
                (later["libraries"][0]["new"], later["libraries"][0]["updated"]), (1, 1)
            )
        self.start()
        _, third = self.request("/api/home")
        self.assertFalse(any(a["new"] or a["updated"] for a in third["assets"]))

    def test_fetch_reports_behind_without_changing_files(self):
        remote = self.remote()
        self.colleague_push(remote, "ip/skills/hello/extra.md", "more")
        self.start()
        code, status = self.request("/api/fetch", {"source": "team"})
        self.assertEqual((code, status["behind"], status["ahead"]), (200, 1, 0))
        self.assertFalse((self.lib / "ip/skills/hello/extra.md").exists())
        _, home = self.request("/api/home")
        self.assertEqual(home["libraries"][0]["behind"], 1)
        code, pulled = self.request(
            "/api/git", {"source": "team", "action": "pull", "fingerprint": status["fingerprint"]}
        )
        self.assertEqual(code, 200)
        self.assertTrue((self.lib / "ip/skills/hello/extra.md").exists())
        self.assertEqual(self.request("/api/fetch", {"source": "missing"})[0], 400)
        e.git(self.lib, "remote", "remove", "origin")
        self.assertEqual(self.request("/api/fetch", {"source": "team"})[0], 400)

    def test_profile_saves_selections_for_a_place(self):
        (self.lib / "profiles").mkdir()
        (self.lib / "profiles/starter.manifest").write_text("skills: hello\n")
        c.add_source(self.home, "team", self.lib)
        self.start()
        code, result = self.request(
            "/api/profile", {"target": str(self.home), "source": "team", "profile": "starter"}
        )
        self.assertEqual((code, result["applied"]), (200, True))
        self.assertIn("team/skills/hello", c.effective(self.home)["inbound"])
        self.assertEqual(
            self.request("/api/profile", {"source": "team", "profile": "../x"})[0], 400
        )


class ShareApiTests(LibraryFixture):
    def test_candidates_list_local_originals_and_library_edits(self):
        self.skill(self.repo / ".claude/skills/mine")
        self.skill(self.repo / "skills/hello")
        (self.lib / "ip/skills/hello/notes.md").write_text("in-place edit")
        self.start()
        code, found = self.request("/api/share-candidates")
        self.assertEqual(code, 200)
        self.assertEqual(found["libraries"], ["team"])
        local = {row["name"]: row for row in found["local"]}
        self.assertEqual(local["mine"]["path"], ".claude/skills/mine")
        self.assertEqual(local["mine"]["description"], "Test helper")
        self.assertIsNone(local["mine"]["exists_in"])
        self.assertEqual(local["hello"]["exists_in"], "team")
        self.assertEqual(
            found["edited"],
            [
                {
                    "source": "team",
                    "kind": "skills",
                    "name": "hello",
                    "files": ["ip/skills/hello/notes.md"],
                }
            ],
        )

    def test_direct_share_refuses_a_checkout_off_the_main_branch(self):
        self.remote()
        e.git(self.lib, "switch", "-q", "-c", "share/leftover")
        self.skill(self.repo / "skills/mine")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        _, plan = self.request("/api/share-plan", body)
        self.assertFalse(plan["can_apply"])
        self.assertTrue(
            any("on branch share/leftover, not main" in x["message"] for x in plan["conflicts"])
        )
        # Review mode does not use the checked-out branch, so it stays available.
        _, review = self.request("/api/share-plan", {**body, "mode": "review"})
        self.assertFalse(any("not main" in x["message"] for x in review["conflicts"]))

    def test_global_assets_not_in_a_library_can_be_shared(self):
        c.add_source(self.base, "team", self.lib, scope="global")
        self.skill(self.base / ".kiro/skills/helper")
        self.skill(self.base / ".kiro/skills/hello")  # the library has it: not offered
        (self.base / ".claude/rules").mkdir(parents=True)
        (self.base / ".claude/rules/own.md").write_text("A Claude rule: not offered")
        self.start()
        _, found = self.request("/api/share-candidates")
        self.assertEqual(
            [(row["name"], row["path"], row["target"]) for row in found["global"]],
            [("helper", ".kiro/skills/helper", str(self.base))],
        )
        self.assertNotIn("helper", [row["name"] for row in found["local"]])
        body = {
            "target": str(self.base),
            "source": "team",
            "kind": "skills",
            "name": "helper",
            "path": ".kiro/skills/helper",
        }
        code, plan = self.request("/api/share-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        code, result = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Share helper"}
        )
        self.assertEqual(code, 200, result)
        self.assertTrue((self.lib / "ip/skills/helper/SKILL.md").is_file())
        # Previewing it reads the home folder's copy.
        code, view = self.request(
            "/api/asset?target="
            + str(self.base)
            + "&kind=skills&name=helper&local=.kiro/skills/helper"
        )
        self.assertEqual(code, 200, view)

    def test_library_check_runs_before_the_commit(self):
        import sys

        self.skill(self.repo / "skills/mine")
        (self.lib / "catalog.txt").write_text("old")
        self.commit(self.lib, "Add catalog")
        refresh = "open('catalog.txt', 'w').write('refreshed')"
        c.set_check(self.repo, "team", [sys.executable, "-c", refresh])
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        _, plan = self.request("/api/share-plan", body)
        code, result = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Share mine"}
        )
        self.assertEqual(code, 200, result)
        self.assertIn("-c", result["checked"])
        files = e.git(self.lib, "show", "--name-only", "--format=", "HEAD").stdout.decode().split()
        self.assertEqual(files, ["catalog.txt", "ip/skills/mine/SKILL.md"])
        # A failing check commits nothing and says how to run it again.
        fail = "import sys; print('ip/skills/other: not allowed'); sys.exit(1)"
        c.set_check(self.repo, "team", [sys.executable, "-c", fail])
        self.skill(self.repo / "skills/other")
        body = {**body, "name": "other", "path": "skills/other"}
        _, plan = self.request("/api/share-plan", body)
        head = e.git(self.lib, "rev-parse", "HEAD").stdout
        code, failed = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Share other"}
        )
        self.assertEqual(code, 400)
        self.assertIn("library check failed", failed["error"])
        self.assertIn("not allowed", failed["detail"])
        self.assertIn("cd ", failed["command"])
        self.assertEqual(e.git(self.lib, "rev-parse", "HEAD").stdout, head)
        self.assertTrue((self.lib / "ip/skills/other/SKILL.md").is_file())
        # Settings: a command line is stored as arguments; empty turns it off.
        code, saved = self.request(
            "/api/library-settings", {"source": "team", "check": 'python3 "my check.py" --x'}
        )
        self.assertEqual(saved["check"], ["python3", "my check.py", "--x"])
        _, cleared = self.request("/api/library-settings", {"source": "team", "check": ""})
        self.assertIsNone(cleared["check"])
        with self.assertRaises(c.ShareError):
            c.set_check(self.repo, "team", ["", "x"])

    def test_share_commits_only_the_asset_and_pushes(self):
        remote = self.remote()
        self.skill(self.repo / "skills/mine")
        (self.lib / "unrelated.md").write_text("staged by the user")
        e.git(self.lib, "add", "unrelated.md")
        (self.lib / "untracked.md").write_text("left alone")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        code, plan = self.request("/api/share-plan", body)
        self.assertEqual(code, 200)
        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["commit_paths"], ["ip/skills/mine"])
        self.assertEqual([x["op"] for x in plan["changes"]], ["copy"])
        # The ordinary apply endpoint does not apply a share without its note.
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        for note in ("", "   ", None):
            code, _ = self.request(
                "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": note}
            )
            self.assertEqual(code, 400)
        self.assertFalse((self.lib / "ip/skills/mine").exists())
        code, result = self.request(
            "/api/share-apply",
            {"fingerprint": plan["fingerprint"], "note": "Share mine", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            set(result),
            {"commit", "pushed", "push_error", "get_command", "adopted", "adopt_error", "branch"}
            | {"review_url", "push_error_hint", "push_error_command", "waiting_for_review"}
            | {"library", "landed_on", "checked"},
        )
        # The result says where the commit went.
        self.assertEqual((result["library"], result["landed_on"]), ("team", "main"))
        self.assertEqual((result["adopted"], result["adopt_error"]), (False, None))
        self.assertEqual((result["pushed"], result["push_error"]), (True, None))
        self.assertEqual(result["get_command"], "captain-share get mine")
        head = e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(result["commit"], head)
        self.assertEqual(e.git(remote, "rev-parse", "main").stdout.decode().strip(), head)
        files = e.git(self.lib, "show", "--name-only", "--format=%s", "HEAD").stdout.decode()
        self.assertEqual(files.split(), ["Share", "mine", "ip/skills/mine/SKILL.md"])
        status = e.git(self.lib, "status", "--porcelain").stdout.decode()
        self.assertIn("A  unrelated.md", status)
        self.assertIn("?? untracked.md", status)
        saved = json.loads((self.repo / ".captain/sharing.json").read_text())
        self.assertEqual(saved["outbound"], {})
        # Sharing the same version again has nothing to record.
        _, again = self.request("/api/share-plan", body)
        self.assertFalse(again["can_apply"])

    def test_refused_push_keeps_the_commit_and_stale_preview_is_refused(self):
        self.skill(self.repo / "skills/mine")
        e.git(self.lib, "remote", "add", "origin", str(self.base / "missing.git"))
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        _, plan = self.request("/api/share-plan", body)
        (self.repo / "skills/mine/SKILL.md").write_text("---\nname: mine\n---\nchanged")
        code, stale = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Share"}
        )
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        self.assertFalse((self.lib / "ip/skills/mine").exists())
        _, plan = self.request("/api/share-plan", body)
        code, result = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Share", "push": True}
        )
        self.assertEqual(code, 200)
        self.assertFalse(result["pushed"])
        self.assertTrue(result["push_error"])
        self.assertEqual(
            e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip(), result["commit"]
        )

    def test_a_commit_whose_signature_does_not_verify_is_not_pushed(self):
        remote = self.remote()
        self.skill(self.repo / "skills/mine")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        _, plan = self.request("/api/share-plan", body)
        quiet = r.quiet

        def bad(repo, *args):
            # Git reports a bad signature (%G? = B) for the new commit.
            return "B" if args[:2] == ("log", "-1") else quiet(repo, *args)

        with patch.object(r, "quiet", side_effect=bad):
            code, result = self.request(
                "/api/share-apply",
                {"fingerprint": plan["fingerprint"], "note": "Share mine", "push": True},
            )
        self.assertEqual(code, 200, result)
        self.assertFalse(result["pushed"])
        self.assertIn("signature does not verify", result["push_error"])
        self.assertIn("verify-commit", result["push_error_command"])
        head = e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(result["commit"], head)
        # The commit stays local; the remote does not have it.
        self.assertNotEqual(e.git(remote, "rev-parse", "main").stdout.decode().strip(), head)

    def test_share_into_an_existing_name_or_plain_folder_is_blocked(self):
        self.skill(self.repo / "skills/hello")
        plain = self.base / "plain"
        plain.mkdir()
        c.init(plain, library=True)
        c.add_source(self.repo, "plain", plain)
        self.skill(self.repo / "skills/mine")
        self.start()
        _, clash = self.request(
            "/api/share-plan",
            {"source": "team", "kind": "skills", "name": "hello", "path": "skills/hello"},
        )
        self.assertFalse(clash["can_apply"])
        _, plain_plan = self.request(
            "/api/share-plan",
            {"source": "plain", "kind": "skills", "name": "mine", "path": "skills/mine"},
        )
        self.assertFalse(plain_plan["can_apply"])
        self.assertIn("not a Git repository", " ".join(plain_plan["errors"]))
        for bad in ("../outside", "/abs"):
            code, _ = self.request(
                "/api/share-plan",
                {"source": "team", "kind": "skills", "name": "mine", "path": bad},
            )
            self.assertEqual(code, 400)

    def test_commit_edits_commits_only_that_asset(self):
        remote = self.remote()
        self.skill(self.lib / "ip/skills/other")
        self.commit(self.lib, "Add other")
        (self.lib / "ip/skills/hello/SKILL.md").write_text("---\nname: hello\n---\nimproved")
        (self.lib / "ip/skills/hello/new.md").write_text("new file")
        (self.lib / "ip/skills/other/SKILL.md").write_text("---\nname: other\n---\nwip")
        self.start()
        status = r.status(self.repo, "team")
        body = {
            "source": "team",
            "kind": "skills",
            "name": "hello",
            "note": "Improve hello",
            "push": True,
            "fingerprint": status["fingerprint"],
        }
        self.assertEqual(self.request("/api/commit-edits", {**body, "note": ""})[0], 400)
        self.assertEqual(self.request("/api/commit-edits", {**body, "fingerprint": "x"})[0], 400)
        code, result = self.request("/api/commit-edits", body)
        self.assertEqual(code, 200, result)
        self.assertTrue(result["pushed"])
        self.assertEqual(
            e.git(remote, "rev-parse", "main").stdout.decode().strip(), result["commit"]
        )
        files = e.git(self.lib, "show", "--name-only", "--format=", "HEAD").stdout.decode()
        self.assertEqual(
            sorted(files.split()), ["ip/skills/hello/SKILL.md", "ip/skills/hello/new.md"]
        )
        self.assertEqual(
            e.git(self.lib, "status", "--porcelain").stdout.decode().strip(),
            "M ip/skills/other/SKILL.md",
        )
        body["fingerprint"] = r.status(self.repo, "team")["fingerprint"]
        code, nothing = self.request("/api/commit-edits", body)
        self.assertEqual(code, 400)
        self.assertIn("no uncommitted changes", nothing["error"])


class CliTests(LibraryFixture):
    def run_cli(self, *argv, tty=None, answer="n"):
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO()
        stdin.isatty = lambda: bool(tty)
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch("captain_share.cli.sys.stdin", stdin))
            stack.enter_context(patch("builtins.input", return_value=answer))
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_get_installs_for_the_user_by_default(self):
        c.add_source(self.home, "team", self.lib)
        code, out, _ = self.run_cli("--target", str(self.repo), "get", "hello")
        self.assertEqual(code, 0)
        self.assertIn("captain-share get hello --apply", out)
        self.assertIn("team/skills/hello", c.effective(self.home)["inbound"])
        self.assertFalse((self.home / ".kiro/skills/hello").exists())
        code, out, _ = self.run_cli("--target", str(self.repo), "get", "team/hello", "--apply")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.home / ".kiro/skills/hello").is_symlink())
        self.assertFalse((self.repo / ".kiro/skills/hello").exists())
        code, out, _ = self.run_cli("--target", str(self.repo), "get", "team/skills/hello")
        self.assertIn("already installed", out)

    def test_get_here_asks_and_declining_changes_nothing(self):
        before = (self.repo / ".captain/sharing.json").read_text()
        code, out, _ = self.run_cli("--target", str(self.repo), "get", "hello", "--here", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("Nothing was changed", out)
        self.assertEqual((self.repo / ".captain/sharing.json").read_text(), before)
        code, out, _ = self.run_cli(
            "--target", str(self.repo), "get", "hello", "--here", tty=True, answer="y"
        )
        self.assertEqual(code, 0, out)
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())

    def test_get_reports_ambiguity_missing_and_conflicts(self):
        other = self.base / "other-lib"
        other.mkdir()
        c.init(other, library=True)
        self.skill(other / "ip/skills/hello")
        c.add_source(self.home, "team", self.lib)
        c.add_source(self.home, "other", other)
        code, _, err = self.run_cli("--target", str(self.repo), "get", "hello")
        self.assertEqual(code, 1)
        self.assertIn("other/skills/hello, team/skills/hello", err)
        for ref in ("missing", "team/nonsense/hello", "a/b/c/d", "../hello"):
            self.assertEqual(self.run_cli("--target", str(self.repo), "get", ref)[0], 1)
        self.skill(self.home / ".kiro/skills/hello", "unmanaged copy")
        code, out, _ = self.run_cli(
            "--target", str(self.repo), "--json", "get", "team/hello", "--apply"
        )
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertFalse(report["applied"])
        self.assertFalse(report["plan"]["can_apply"])
        self.assertNotIn("team/skills/hello", c.effective(self.home)["inbound"])

    def test_share_previews_then_commits_with_note(self):
        self.skill(self.repo / "skills/mine")
        code, out, _ = self.run_cli(
            "--target", str(self.repo), "share", "skills", "mine", "--source", "team"
        )
        self.assertEqual(code, 0, out)
        self.assertIn("Commits only: ip/skills/mine", out)
        self.assertFalse((self.lib / "ip/skills/mine").exists())
        code, _, err = self.run_cli(
            "--target", str(self.repo), "share", "skills", "mine", "--source", "team", "--apply"
        )
        self.assertEqual(code, 1)
        self.assertIn("--note", err)
        code, out, _ = self.run_cli(
            "--target",
            str(self.repo),
            "--json",
            "share",
            "skills",
            "mine",
            "--source",
            "team",
            "--note",
            "Share mine",
            "--apply",
        )
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertEqual(result["get_command"], "captain-share get mine")
        subject = e.git(self.lib, "log", "-1", "--format=%s").stdout.decode().strip()
        self.assertEqual(subject, "Share mine")


class AdoptTests(LibraryFixture):
    def adopt(self, **extra):
        args = {"action": "adopt", "kind": "skills", "asset": "hello", "source": "team"}
        return e.plan(self.repo, **{**args, **extra})

    def test_adopt_keeps_a_link_another_tool_made_to_the_original(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        link = self.repo / ".kiro/skills/hello"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.lib / "ip/skills/hello")
        planned = e.plan(self.repo, action="adopt", kind="skills", asset="hello", source="team")
        self.assertTrue(planned["can_apply"], planned["errors"])
        self.assertEqual(
            [
                (x["op"], Path(x["path"]).relative_to(self.repo).as_posix())
                for x in planned["changes"]
            ],
            [("link", ".claude/skills/hello")],
        )
        e.apply(planned)
        self.assertEqual(os.readlink(link), str(self.lib / "ip/skills/hello"))
        self.assertTrue(e.status(self.repo)["ok"])
        self.assertIn(".kiro/skills/hello", e.state(self.repo)["inbound"])

    def test_equal_copies_in_two_tools_are_archived_linked_and_restorable(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        original = self.lib / "ip/skills/hello"
        for folder in (".kiro/skills", ".claude/skills"):
            self.skill(self.repo / folder / "hello")
        planned = self.adopt()
        self.assertTrue(planned["can_apply"], planned["errors"])
        ops = [(x["op"], x["path"].replace(str(self.repo) + "/", "")) for x in planned["changes"]]
        self.assertEqual(
            ops,
            [
                ("archive", ".kiro/skills/hello"),
                ("link", ".kiro/skills/hello"),
                ("archive", ".claude/skills/hello"),
                ("link", ".claude/skills/hello"),
            ],
        )
        result = e.apply(planned)
        self.assertEqual(len(result["archives"]), 2)
        for folder in (".kiro/skills", ".claude/skills"):
            link = self.repo / folder / "hello"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), original)
        saved = json.loads((self.repo / ".captain/sharing.json").read_text())
        self.assertEqual(
            saved["inbound"]["team/skills/hello"],
            {"source": "team", "kind": "skills", "name": "hello"},
        )
        # Nothing left to adopt, and later syncs keep the links.
        self.assertEqual(self.adopt()["changes"], [])
        self.assertEqual(e.plan(self.repo)["changes"], [])
        # The archived copy is recoverable once the link is removed.
        e.apply(e.plan(self.repo, action="remove", kind="skills", asset="hello"))
        # Two archives share this path (the adopted copy, then the removed link);
        # ids made in the same second sort randomly, so pick the copy explicitly.
        record = next(
            row
            for row in e.archives(self.repo)
            if row["relative_path"] == ".kiro/skills/hello" and row["snapshot"]["type"] != "link"
        )
        e.apply(e.plan(self.repo, action="restore", archive_id=record["id"]))
        restored = self.repo / ".kiro/skills/hello"
        self.assertFalse(restored.is_symlink())
        self.assertEqual(e.snapshot(restored, modes=False), e.snapshot(original, modes=False))

    def test_differing_copy_conflicts_until_replaced(self):
        self.skill(self.repo / ".kiro/skills/hello", "My local edits")
        planned = self.adopt()
        self.assertFalse(planned["can_apply"])
        (conflict,) = planned["conflicts"]
        self.assertEqual((conflict["reason"], conflict["path"]), ("differs", ".kiro/skills/hello"))
        self.assertIn("differs from the library version", conflict["message"])
        self.assertNotIn("team/skills/hello", c.effective(self.repo)["inbound"])
        result = e.apply(self.adopt(replace=True))
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())
        (archived,) = result["archives"]
        payload = self.repo / ".captain/archive" / archived["id"] / "payload/SKILL.md"
        self.assertIn("My local edits", payload.read_text())

    def test_missing_original_nothing_to_adopt_and_kept_copies(self):
        missing = self.adopt(asset="absent")
        self.assertFalse(missing["can_apply"])
        self.assertEqual(missing["conflicts"][0]["reason"], "missing")
        with self.assertRaises(c.ShareError):
            self.adopt(source="../x")
        # No local copy: adopting only links and saves the selection.
        e.apply(self.adopt())
        self.assertEqual(self.adopt()["changes"], [])
        # A copy for a tool this place does not link (claude) is kept, not archived.
        self.skill(self.repo / ".claude/skills/hello")
        kept = self.adopt(path=".claude/skills/hello")
        self.assertEqual(kept["changes"], [])
        self.assertEqual(kept["kept"][0]["path"], ".claude/skills/hello")
        self.assertEqual(self.adopt(path="notes/hello")["kept"], [])

    def test_api_adopt_plan_applies_with_fresh_fingerprint_only(self):
        self.skill(self.repo / ".kiro/skills/hello")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "hello"}
        code, planned = self.request("/api/adopt-plan", body)
        self.assertEqual((code, planned["can_apply"]), (200, True))
        self.assertEqual(self.request("/api/adopt-plan", {**body, "replace": "yes"})[0], 400)
        (self.repo / ".kiro/skills/hello/SKILL.md").write_text("edited after preview")
        code, stale = self.request("/api/apply", {"fingerprint": planned["fingerprint"]})
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        self.assertFalse((self.repo / ".kiro/skills/hello").is_symlink())
        _, differs = self.request("/api/adopt-plan", body)
        self.assertEqual(differs["conflicts"][0]["reason"], "differs")
        _, replace = self.request("/api/adopt-plan", {**body, "replace": True})
        self.assertEqual(
            self.request("/api/apply", {"fingerprint": replace["fingerprint"]})[0], 200
        )
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())

    def test_share_with_adopt_commits_pushes_and_links(self):
        remote = self.remote()
        c.set_tools(self.repo, ["kiro", "claude"])
        self.skill(self.repo / ".claude/skills/mine")
        self.start()
        body = {
            "source": "team",
            "kind": "skills",
            "name": "mine",
            "path": ".claude/skills/mine",
            "adopt": True,
        }
        code, planned = self.request("/api/share-plan", body)
        self.assertEqual(code, 200)
        self.assertEqual(
            planned["adopt_changes"],
            [
                {"op": "link", "path": ".kiro/skills/mine"},
                {"op": "archive", "path": ".claude/skills/mine"},
                {"op": "link", "path": ".claude/skills/mine"},
            ],
        )
        code, result = self.request(
            "/api/share-apply",
            {
                "fingerprint": planned["fingerprint"],
                "note": "Share mine",
                "push": True,
                "adopt": True,
            },
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            set(result),
            {"commit", "pushed", "push_error", "get_command", "adopted", "adopt_error", "branch"}
            | {"review_url", "push_error_hint", "push_error_command", "waiting_for_review"}
            | {"library", "landed_on", "checked"},
        )
        self.assertEqual(
            (result["pushed"], result["adopted"], result["adopt_error"]), (True, True, None)
        )
        self.assertEqual(
            e.git(remote, "rev-parse", "main").stdout.decode().strip(), result["commit"]
        )
        original = self.lib / "ip/skills/mine"
        for folder in (".kiro/skills", ".claude/skills"):
            self.assertEqual((self.repo / folder / "mine").resolve(), original)
        self.assertIn("team/skills/mine", c.effective(self.repo)["inbound"])
        self.assertEqual(len(e.archives(self.repo)), 1)

    def test_failed_adopt_keeps_the_commit(self):
        c.set_tools(self.repo, ["kiro", "claude"])
        self.skill(self.repo / ".claude/skills/mine")
        self.skill(self.repo / ".kiro/skills/mine", "An older copy")
        planned = r.share_plan(self.repo, "team", "skills", "mine", ".claude/skills/mine")
        result = r.share_apply(planned, "Share mine", adopt=True)
        self.assertFalse(result["adopted"])
        self.assertIn("differs", result["adopt_error"])
        head = e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(head, result["commit"])
        self.assertFalse((self.repo / ".claude/skills/mine").is_symlink())
        self.assertEqual(e.archives(self.repo), [])


class AdoptCliTests(LibraryFixture):
    run_cli = CliTests.run_cli

    def test_adopt_previews_conflicts_and_replaces(self):
        self.skill(self.repo / ".kiro/skills/hello", "Mine")
        base = ["--target", str(self.repo), "adopt", "skills", "hello", "--source", "team"]
        code, out, _ = self.run_cli(*base)
        self.assertEqual(code, 1)
        self.assertIn("differs from the library version", out)
        code, out, _ = self.run_cli(*base, "--replace")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.repo / ".kiro/skills/hello").is_symlink())
        code, out, _ = self.run_cli(*base, "--replace", "--apply")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.repo / ".kiro/skills/hello").is_symlink())

    def test_share_adopt_from_a_project_folder(self):
        self.skill(self.repo / "skills/mine")
        base = [
            "--target",
            str(self.repo),
            "share",
            "skills",
            "mine",
            "--source",
            "team",
            "--adopt",
        ]
        code, out, _ = self.run_cli(*base)
        self.assertEqual(code, 0, out)
        self.assertIn("then archive  skills/mine", out)
        self.assertIn("then link     .kiro/skills/mine", out)
        code, out, _ = self.run_cli(*base, "--note", "Share mine", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("now uses the library version", out)
        self.assertFalse((self.repo / "skills/mine").exists())
        self.assertTrue((self.repo / ".kiro/skills/mine").is_symlink())


class OutdatedTests(LibraryFixture):
    def states(self, report):
        return {(row["state"], row.get("path") or row["name"]) for row in report["rows"]}

    def test_outdated_reports_newer_edited_and_copies(self):
        import shutil

        remote = self.remote()
        first = (self.lib / "ip/skills/hello/SKILL.md").read_text()
        self.select()
        e.apply(e.plan(self.repo))
        report = r.outdated(self.repo)
        row = next(x for x in report["rows"] if x["name"] == "hello")
        self.assertEqual(row["state"], "current")
        head = e.git(self.lib, "rev-parse", "--short", "HEAD").stdout.decode().strip()
        self.assertEqual(row["have"]["commit"], head)
        # A colleague's push shows only after a fetch.
        self.colleague_push(remote, "ip/skills/hello/SKILL.md", first + "\nnew line")
        self.assertIn(("current", "hello"), self.states(r.outdated(self.repo)))
        report = r.outdated(self.repo, do_fetch=True)
        row = next(x for x in report["rows"] if x["name"] == "hello")
        self.assertEqual((row["state"], row["newer"]), ("newer", 1))
        self.assertTrue(report["checked"]["team"])
        # After pulling, an uncommitted library edit is reported as not shared yet.
        e.git(self.lib, "pull", "-q")
        (self.lib / "ip/skills/hello/notes.md").write_text("mine")
        self.assertIn(("edited", "hello"), self.states(r.outdated(self.repo)))
        (self.lib / "ip/skills/hello/notes.md").unlink()
        # Copies: one equal to the earlier team version, one with the user's changes.
        older = self.repo / ".agents/skills/hello"
        older.mkdir(parents=True)
        (older / "SKILL.md").write_text(first)
        changed = self.repo / ".claude/skills/hello"
        shutil.copytree(self.lib / "ip/skills/hello", changed)
        (changed / "SKILL.md").write_text("my own version")
        states = self.states(r.outdated(self.repo))
        self.assertIn(("older-copy", ".agents/skills/hello"), states)
        self.assertIn(("changed-copy", ".claude/skills/hello"), states)
        self.assertIn(("current", "hello"), states)

    def test_cli_outdated_text(self):
        self.select()
        e.apply(e.plan(self.repo))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--target", str(self.repo), "outdated"])
        self.assertEqual(code, 0)
        self.assertIn("Everything is up to date (1)", out.getvalue())
