import json

from captain_share import engine as e
from captain_share import repositories as r
from tests import test_library_home
from tests.test_library_home import LibraryFixture


def out(path, *args):
    return e.git(path, *args).stdout.decode().strip()


class StarterTests(LibraryFixture):
    run_cli = test_library_home.CliTests.run_cli

    def setUp(self):
        super().setUp()
        self.skill(self.lib / "ip/skills/review")
        (self.lib / "ip/prompts").mkdir(parents=True, exist_ok=True)
        (self.lib / "ip/prompts/plan.md").write_text("Plan the work\n")
        self.commit(self.lib, "More assets")

    def test_view_plan_apply_push_and_clear(self):
        remote = self.remote()
        self.start()
        code, view = self.request("/api/starter?source=team")
        self.assertEqual(code, 200)
        self.assertEqual(
            view,
            {
                "source": "team",
                "path": "profiles/starter.manifest",
                "exists": False,
                "assets": [],
                "error": None,
            },
        )
        for bad in (
            {"source": "team", "assets": []},
            {"source": "team", "assets": ["skills/missing"]},
            {"source": "team", "assets": ["nonsense"]},
            {"source": "team", "assets": ["skills/../x"]},
        ):
            self.assertEqual(self.request("/api/starter-plan", bad)[0], 400)
        (self.lib / "unrelated.md").write_text("staged elsewhere")
        r.run(self.lib, "add", "unrelated.md")
        body = {"source": "team", "assets": ["skills/review", "prompts/plan.md", "skills/hello"]}
        code, plan = self.request("/api/starter-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual(plan["changes"], [{"op": "write", "path": "profiles/starter.manifest"}])
        self.assertEqual(plan["commit_paths"], ["profiles/starter.manifest"])
        self.assertIn("+skills: hello review\n", plan["diff"])
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        self.assertEqual(
            self.request("/api/starter-apply", {"fingerprint": plan["fingerprint"], "note": " "})[
                0
            ],
            400,
        )
        code, result = self.request(
            "/api/starter-apply",
            {"fingerprint": plan["fingerprint"], "note": "Starter set", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            set(result),
            {"commit", "pushed", "push_error", "branch", "review_url"}
            | {"push_error_hint", "push_error_command", "waiting_for_review"}
            | {"library", "landed_on"},
        )
        self.assertEqual((result["pushed"], result["push_error"]), (True, None))
        self.assertEqual(out(remote, "rev-parse", "main"), result["commit"])
        self.assertEqual(
            (self.lib / "profiles/starter.manifest").read_text(),
            "# Starter set: offered to new teammates by Captain Share\n"
            "skills: hello review\nprompts: plan.md\n",
        )
        files = out(self.lib, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(files, ["profiles/starter.manifest"])
        self.assertIn("A  unrelated.md", out(self.lib, "status", "--porcelain"))
        _, view = self.request("/api/starter?source=team")
        self.assertEqual(view["assets"], ["skills/hello", "skills/review", "prompts/plan.md"])
        _, same = self.request("/api/starter-plan", body)
        self.assertEqual(same["conflicts"][0]["reason"], "same")
        # Uncommitted edits of the manifest block a change.
        manifest = self.lib / "profiles/starter.manifest"
        manifest.write_text(manifest.read_text() + "# local edit\n")
        _, edited = self.request(
            "/api/starter-plan", {"source": "team", "assets": ["skills/hello"]}
        )
        self.assertIn("edited", [x["reason"] for x in edited["conflicts"]])
        r.run(self.lib, "checkout", "--", "profiles/starter.manifest")
        _, plan = self.request("/api/starter-plan", {"source": "team", "assets": [], "clear": True})
        self.assertEqual(plan["changes"], [{"op": "remove", "path": "profiles/starter.manifest"}])
        _, stale = self.request(
            "/api/starter-plan", {"source": "team", "assets": [], "clear": True}
        )
        code, result = self.request(
            "/api/starter-apply", {"fingerprint": stale["fingerprint"], "note": "No starter"}
        )
        self.assertEqual(code, 200, result)
        self.assertFalse(manifest.exists())
        self.assertFalse(result["pushed"])
        self.assertEqual(self.request("/api/starter?source=team")[1]["exists"], False)

    def test_stale_preview_and_refused_push_keeps_commit(self):
        r.run(self.lib, "remote", "add", "origin", str(self.base / "missing.git"))
        planned = r.starter_plan(self.repo, "team", ["skills/hello"])
        (self.lib / "profiles").mkdir()
        (self.lib / "profiles/starter.manifest").write_text("skills: review\n")
        with self.assertRaisesRegex(Exception, "stale"):
            r.starter_apply(planned, "Starter")
        (self.lib / "profiles/starter.manifest").unlink()
        planned = r.starter_plan(self.repo, "team", ["skills/hello"])
        result = r.starter_apply(planned, "Starter", push=True)
        self.assertFalse(result["pushed"])
        self.assertTrue(result["push_error"])
        self.assertEqual(out(self.lib, "rev-parse", "HEAD"), result["commit"])

    def test_invalid_manifest_is_reported(self):
        (self.lib / "profiles").mkdir()
        (self.lib / "profiles/starter.manifest").write_text("skills: missing\n")
        self.start()
        _, view = self.request("/api/starter?source=team")
        self.assertEqual((view["exists"], view["assets"]), (True, []))
        self.assertTrue(view["error"])

    def test_cli_lists_adds_removes_and_clears(self):
        base = ["--target", str(self.repo), "starter", "team"]
        code, text, _ = self.run_cli(*base)
        self.assertEqual(code, 0)
        self.assertIn("team has no starter set", text)
        code, text, _ = self.run_cli(*base, "--set", "skills/hello,skills/review")
        self.assertEqual(code, 0, text)
        self.assertIn("+skills: hello review", text)
        self.assertFalse((self.lib / "profiles/starter.manifest").exists())
        self.assertEqual(self.run_cli(*base, "--set", "skills/hello", "--apply")[0], 1)
        code, text, _ = self.run_cli(
            *base, "--set", "skills/hello,skills/review", "--note", "Starter", "--apply"
        )
        self.assertEqual(code, 0, text)
        code, text, _ = self.run_cli(*base, "--add", "prompts/plan.md", "--note", "Add", "--apply")
        self.assertEqual(code, 0, text)
        code, text, _ = self.run_cli(*base, "--remove", "skills/review", "--note", "Rm", "--apply")
        self.assertEqual(code, 0, text)
        code, text, _ = self.run_cli("--json", *base)
        self.assertEqual(json.loads(text)["assets"], ["skills/hello", "prompts/plan.md"])
        self.assertEqual(self.run_cli(*base, "--remove", "skills/nope")[0], 1)
        code, text, _ = self.run_cli(*base, "--clear", "--note", "Clear", "--apply")
        self.assertEqual(code, 0, text)
        self.assertFalse((self.lib / "profiles/starter.manifest").exists())
