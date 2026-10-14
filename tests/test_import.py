import http.client
import io
import json
import os
import stat
import tempfile
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from captain_share import __version__
from captain_share import config as c
from captain_share import engine as e
from captain_share import imports as im
from tests import test_library_home
from tests.test_library_home import LibraryFixture

SKILL = "---\nname: {name}\ndescription: Imported helper\n---\nUse it well.\n"


def make_zip(entries):
    """Bytes of a zip with (name or ZipInfo, content) entries; names are kept as given."""
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as zf:
        warnings.simplefilter("ignore")  # Duplicate names are written on purpose.
        for name, content in entries:
            info = name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name)
            if not isinstance(name, zipfile.ZipInfo) and name.endswith("/"):
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
            zf.writestr(info, content)
    return buffer.getvalue()


def skill_entries(prefix, name):
    return [(f"{prefix}{name}/SKILL.md", SKILL.format(name=name))]


class ExtractionTests(LibraryFixture):
    def stage(self, data, label="pack.zip"):
        holder = Path(tempfile.mkdtemp(dir=self.base))
        return im.unzip(data, holder, label)

    def test_unsafe_entries_are_refused_and_nothing_is_left(self):
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        fifo = zipfile.ZipInfo("pipe")
        fifo.create_system = 3
        fifo.external_attr = (stat.S_IFIFO | 0o644) << 16
        for bad in (
            [("../x", "escape")],
            [("ok/../../x", "escape")],
            [("/abs", "absolute")],
            [("C:\\x", "drive")],
            [("C:/x", "drive")],
            [("..\\x", "escape")],
            [(link, "/etc/passwd")],
            [(fifo, "")],
            [("same.md", "a"), ("same.md", "b")],
            [("folder", "a file"), ("folder/inside.md", "b")],
        ):
            holder = Path(tempfile.mkdtemp(dir=self.base))
            with self.assertRaises(c.ShareError, msg=bad):
                im.unzip(make_zip(bad), holder, "bad.zip")
            self.assertEqual(list(holder.iterdir()), [], bad)
        self.assertFalse((self.base / "x").exists())
        with self.assertRaises(c.ShareError):
            im.unzip(b"not a zip", Path(tempfile.mkdtemp(dir=self.base)), "x.zip")

    def test_limits_on_entries_and_unpacked_size(self):
        with patch.object(im, "MAX_ENTRIES", 3):
            with self.assertRaisesRegex(c.ShareError, "more than 3 entries"):
                self.stage(make_zip([(f"p{n}.md", "x") for n in range(4)]))
            self.stage(make_zip([(f"p{n}.md", "x") for n in range(3)]))
        with patch.object(im, "MAX_UNPACKED", 10):
            with self.assertRaisesRegex(c.ShareError, "100 MB"):
                self.stage(make_zip([("a.md", "12345"), ("b.md", "123456")]))
            self.stage(make_zip([("a.md", "12345"), ("b.md", "12345")]))

    def test_extracted_files_are_plain_and_mac_clutter_is_ignored(self):
        script = zipfile.ZipInfo("tool/run.sh")
        script.create_system = 3
        script.external_attr = (stat.S_IFREG | 0o755) << 16
        data = make_zip(
            [
                ("__MACOSX/._tool", "resource fork"),
                ("tool/.DS_Store", "finder"),
                (script, "#!/bin/sh\n"),
                *skill_entries("", "tool"),
            ]
        )
        _, root = self.stage(data)
        self.assertFalse((root / "__MACOSX").exists())
        self.assertFalse((root / "tool/.DS_Store").exists())
        mode = stat.S_IMODE((root / "tool/run.sh").stat().st_mode)
        self.assertEqual(mode & 0o111, 0)
        (row,) = im.find(root)
        self.assertEqual((row["id"], row["kind"], row["name"]), ("tool", "skills", "tool"))
        self.assertEqual(row["kinds"], ["skills"])
        self.assertIsNone(row["error"])
        self.assertEqual(row["description"], "Imported helper")

    def test_detection_layouts(self):
        # A zip that is one skill takes the zip's name.
        _, root = self.stage(make_zip([("SKILL.md", SKILL.format(name="x"))]), "Solo Skill.zip")
        self.assertEqual([(r["id"], r["name"]) for r in im.find(root)], [(".", "Solo-Skill")])
        # A folder of skills, with a README that is not a prompt.
        _, root = self.stage(
            make_zip([*skill_entries("", "alpha"), *skill_entries("", "beta"), ("README.md", "hi")])
        )
        self.assertEqual([r["id"] for r in im.find(root)], ["alpha", "beta"])
        # A repository download: one wrapping folder with the library layout.
        _, root = self.stage(
            make_zip(
                [
                    *skill_entries("repo-main/ip/skills/", "gamma"),
                    ("repo-main/steering/style.md", "# Style"),
                    ("repo-main/skills/broken/notes.md", "no SKILL.md"),
                ]
            )
        )
        rows = {r["id"]: r for r in im.find(root)}
        self.assertEqual(
            set(rows),
            {"repo-main/ip/skills/gamma", "repo-main/steering/style.md", "repo-main/skills/broken"},
        )
        self.assertEqual(rows["repo-main/steering/style.md"]["kinds"], ["steering"])
        self.assertIn("SKILL.md", rows["repo-main/skills/broken"]["error"])
        # Loose Markdown defaults to prompts and may be steering.
        _, root = self.stage(make_zip([("notes.md", "# Notes\nDo this.")]))
        (row,) = im.find(root)
        self.assertEqual(
            (row["id"], row["kind"], row["kinds"]), ("notes.md", "prompts", ["prompts", "steering"])
        )
        self.assertEqual(row["description"], "Notes")
        # Outbound review applies: a credential makes a candidate invalid.
        _, root = self.stage(
            make_zip([("leaky/SKILL.md", "---\nname: leaky\n---\nkey AKIAABCDEFGHIJKLMNOP")])
        )
        self.assertIn("credential", im.find(root)[0]["error"])

    def test_stages_drop_the_oldest_and_close_removes_files(self):
        stages = im.Stages(limit=2)
        first = stages.upload(make_zip(skill_entries("", "one")), "one.zip")
        second = stages.upload(make_zip(skill_entries("", "two")), "two.zip")
        first_root = Path(stages.get(first["stage"])["root"])
        self.assertTrue(first_root.is_dir())
        stages.upload(make_zip(skill_entries("", "three")), "three.zip")
        with self.assertRaisesRegex(c.ShareError, "no longer staged"):
            stages.get(first["stage"])
        self.assertFalse(first_root.exists())
        stages.get(second["stage"])
        folder = stages.folder
        stages.close()
        self.assertFalse(folder.exists())
        with self.assertRaises(c.ShareError):
            stages.open_folder(str(self.repo / ".captain/sharing.json"))
        with self.assertRaises(c.ShareError):
            stages.open_folder("relative/path")
        with self.assertRaises(c.ShareError):
            stages.open_folder(str(self.base / "missing"))


class ImportApiTests(LibraryFixture):
    def upload(self, data, token=True, content_type="application/zip", name="pack.zip"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {"Content-Type": content_type}
        if token:
            headers["X-Captain-Token"] = self.token
        conn.request("POST", f"/api/import-upload?name={name}", body=data, headers=headers)
        res = conn.getresponse()
        body = json.loads(res.read())
        conn.close()
        return res.status, body

    def get_raw(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        conn.request("GET", path)
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, res.getheader("Content-Type"), body

    def test_upload_is_authenticated_typed_and_bounded(self):
        self.start()
        data = make_zip(skill_entries("", "mine"))
        self.assertEqual(self.upload(data, token=False)[0], 403)
        self.assertEqual(self.upload(data, content_type="application/json")[0], 400)
        with patch.object(im, "MAX_UPLOAD", 10):
            code, body = self.upload(data)
        self.assertEqual(code, 400)
        self.assertIn("20 MB", body["error"])
        code, body = self.upload(make_zip([("../x", "escape")]))
        self.assertEqual(code, 400)
        self.assertIn("Unsafe", body["error"])
        code, stage = self.upload(data, name="mine.zip")
        self.assertEqual(code, 200, stage)
        self.assertEqual(set(stage), {"stage", "label", "candidates"})
        self.assertEqual(stage["label"], "mine.zip")
        (row,) = stage["candidates"]
        self.assertEqual(set(row), {"id", "kind", "name", "description", "error", "kinds"})
        self.assertEqual((row["id"], row["name"]), ("mine", "mine"))
        folder = self.server.stages.folder
        self.assertTrue(folder.is_dir())
        server, thread = self.servers.pop()
        server.shutdown()
        server.server_close()
        thread.join()
        self.assertFalse(folder.exists())

    def test_upload_plan_apply_commits_only_the_paths_and_pushes(self):
        remote = self.remote()
        (self.lib / "unrelated.md").write_text("staged by the user")
        e.git(self.lib, "add", "unrelated.md")
        self.start()
        data = make_zip(
            [*skill_entries("", "alpha"), *skill_entries("", "beta"), ("notes.md", "x")]
        )
        _, stage = self.upload(data)
        self.assertEqual([r["id"] for r in stage["candidates"]], ["alpha", "beta"])
        items = [
            {"id": "alpha", "kind": "skills", "name": "alpha"},
            {"id": "beta", "kind": "skills", "name": "beta-renamed"},
        ]
        body = {"stage": stage["stage"], "source": "team", "items": items}
        for bad in (
            {**body, "stage": "unknown"},
            {**body, "items": [{"id": "../x", "kind": "skills", "name": "x"}]},
            {**body, "items": [{"id": "alpha", "kind": "prompts", "name": "a.md"}]},
            {**body, "items": []},
            {**body, "source": "missing"},
        ):
            self.assertEqual(self.request("/api/import-plan", bad)[0], 400, bad)
        code, plan = self.request("/api/import-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual(plan["commit_paths"], ["ip/skills/alpha", "ip/skills/beta-renamed"])
        self.assertEqual([x["op"] for x in plan["changes"]], ["copy", "copy"])
        # Neither the ordinary apply nor share-apply applies an import.
        self.assertEqual(self.request("/api/apply", {"fingerprint": plan["fingerprint"]})[0], 400)
        self.assertEqual(
            self.request("/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "x"})[0],
            400,
        )
        self.assertEqual(
            self.request("/api/import-apply", {"fingerprint": plan["fingerprint"], "note": ""})[0],
            400,
        )
        self.assertFalse((self.lib / "ip/skills/alpha").exists())
        code, result = self.request(
            "/api/import-apply",
            {"fingerprint": plan["fingerprint"], "note": "Add alpha and beta", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(set(result), {"commit", "pushed", "push_error", "get_commands"})
        self.assertEqual((result["pushed"], result["push_error"]), (True, None))
        self.assertEqual(
            result["get_commands"],
            ["captain-share get alpha", "captain-share get beta-renamed"],
        )
        head = e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(result["commit"], head)
        self.assertEqual(e.git(remote, "rev-parse", "main").stdout.decode().strip(), head)
        files = e.git(self.lib, "show", "--name-only", "--format=%s", "HEAD").stdout.decode()
        self.assertEqual(
            files.split(),
            [
                "Add",
                "alpha",
                "and",
                "beta",
                "ip/skills/alpha/SKILL.md",
                "ip/skills/beta-renamed/SKILL.md",
            ],
        )
        self.assertIn("A  unrelated.md", e.git(self.lib, "status", "--porcelain").stdout.decode())
        self.assertEqual(
            json.loads((self.repo / ".captain/sharing.json").read_text())["outbound"], {}
        )
        # The same fingerprint cannot be applied twice, and now both exist.
        self.assertEqual(
            self.request(
                "/api/import-apply", {"fingerprint": plan["fingerprint"], "note": "again"}
            )[0],
            400,
        )
        _, again = self.request("/api/import-plan", body)
        self.assertFalse(again["can_apply"])
        self.assertEqual({x["reason"] for x in again["conflicts"]}, {"exists"})
        self.assertEqual(again["changes"], [])

    def test_folder_in_place_conflict_markdown_kind_and_stale_preview(self):
        outside = self.base / "downloads" / "pack"
        outside.mkdir(parents=True)
        self.skill(outside / "hello")  # Already in the library.
        self.skill(outside / "fresh")
        self.start()
        self.assertEqual(self.request("/api/import-folder", {"path": "relative"})[0], 400)
        self.assertEqual(
            self.request("/api/import-folder", {"path": str(outside / "hello/SKILL.md")})[0], 400
        )
        code, stage = self.request("/api/import-folder", {"path": str(outside)})
        self.assertEqual(code, 200, stage)
        self.assertEqual(stage["label"], str(outside))
        self.assertEqual([r["id"] for r in stage["candidates"]], ["fresh", "hello"])
        items = [
            {"id": "fresh", "kind": "skills", "name": "fresh"},
            {"id": "hello", "kind": "skills", "name": "hello"},
        ]
        body = {"stage": stage["stage"], "source": "team", "items": items}
        _, plan = self.request("/api/import-plan", body)
        self.assertFalse(plan["can_apply"])
        (clash,) = plan["conflicts"]
        self.assertEqual((clash["reason"], clash["path"]), ("exists", "hello"))
        self.assertEqual(plan["commit_paths"], ["ip/skills/fresh"])
        code, failed = self.request(
            "/api/import-apply", {"fingerprint": plan["fingerprint"], "note": "Add"}
        )
        self.assertEqual(code, 400)
        self.assertFalse((self.lib / "ip/skills/fresh").exists())
        # A changed source makes the preview stale; the library is untouched.
        _, plan = self.request("/api/import-plan", {**body, "items": items[:1]})
        (outside / "fresh/SKILL.md").write_text("---\nname: fresh\n---\nchanged")
        code, stale = self.request(
            "/api/import-apply", {"fingerprint": plan["fingerprint"], "note": "Add fresh"}
        )
        self.assertEqual(code, 400)
        self.assertIn("stale", stale["error"])
        self.assertFalse((self.lib / "ip/skills/fresh").exists())
        # A loose Markdown file is a prompt by default and may be steering instead.
        (outside.parent / "notes").mkdir()
        (outside.parent / "notes/style.md").write_text("# House style")
        _, stage = self.request("/api/import-folder", {"path": str(outside.parent / "notes")})
        (row,) = stage["candidates"]
        self.assertEqual(row["kinds"], ["prompts", "steering"])
        item = {"id": "style.md", "kind": "steering", "name": "house-style.md"}
        _, plan = self.request(
            "/api/import-plan", {"stage": stage["stage"], "source": "team", "items": [item]}
        )
        self.assertEqual(plan["commit_paths"], ["ip/steering/house-style.md"])
        code, result = self.request(
            "/api/import-apply", {"fingerprint": plan["fingerprint"], "note": "Add style"}
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(result["push_error"], None)
        self.assertFalse(result["pushed"])
        self.assertEqual((self.lib / "ip/steering/house-style.md").read_text(), "# House style")
        self.assertTrue((outside.parent / "notes/style.md").exists())

    def test_refused_push_keeps_the_commit(self):
        e.git(self.lib, "remote", "add", "origin", str(self.base / "missing.git"))
        self.start()
        _, stage = self.upload(make_zip(skill_entries("", "mine")))
        _, plan = self.request(
            "/api/import-plan",
            {
                "stage": stage["stage"],
                "source": "team",
                "items": [{"id": "mine", "kind": "skills", "name": "mine"}],
            },
        )
        code, result = self.request(
            "/api/import-apply",
            {"fingerprint": plan["fingerprint"], "note": "Add mine", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertFalse(result["pushed"])
        self.assertTrue(result["push_error"])
        self.assertEqual(
            e.git(self.lib, "rev-parse", "HEAD").stdout.decode().strip(), result["commit"]
        )

    def test_tour_files_are_served_only_when_present(self):
        web = self.base / "web"
        (web / "tour").mkdir(parents=True)
        with patch("captain_share.server.WEB", web):
            self.start()
            for path in ("/tour", "/tour.js", "/tour.css", "/tour/step-1.png"):
                self.assertEqual(self.get_raw(path)[0], 404, path)
            (web / "tour.html").write_text("<!doctype html><title>Tour</title>")
            (web / "tour/step-1.png").write_bytes(b"\x89PNG fake")
            (web / "tour/photo.jpg").write_bytes(b"jpeg")
            (web / "tour/Bad.png").write_bytes(b"png")
            (web / "secret.png").write_bytes(b"png")
            code, mime, body = self.get_raw("/tour")
            self.assertEqual((code, mime), (200, "text/html; charset=utf-8"))
            self.assertIn(b"Tour", body)
            self.assertEqual(self.get_raw("/tour/step-1.png")[:2], (200, "image/png"))
            self.assertEqual(self.get_raw("/tour/photo.jpg")[:2], (200, "image/jpeg"))
            for path in (
                "/tour/Bad.png",
                "/tour/../secret.png",
                "/tour/%2e%2e/secret.png",
                "/tour/x.svg",
                "/tour/sub/step-1.png",
            ):
                self.assertEqual(self.get_raw(path)[0], 404, path)

    def test_errors_name_the_zip_not_the_temporary_folder(self):
        self.start()
        data = make_zip(
            [
                ("skills/broken/notes.md", "no SKILL.md"),
                *skill_entries("skills/", "fine"),
            ]
        )
        _, stage = self.upload(data, name="pack.zip")
        temp = str(self.server.stages.folder)
        rows = {row["id"]: row for row in stage["candidates"]}
        self.assertEqual(
            rows["skills/broken"]["error"], "Skill lacks SKILL.md: pack.zip/skills/broken"
        )
        _, plan = self.request(
            "/api/import-plan",
            {
                "stage": stage["stage"],
                "source": "team",
                "items": [{"id": "skills/broken", "kind": "skills", "name": "broken"}],
            },
        )
        self.assertFalse(plan["can_apply"])
        self.assertEqual(plan["errors"], ["Skill lacks SKILL.md: pack.zip/skills/broken"])
        self.assertNotIn(temp, json.dumps(plan["conflicts"]))
        # Extraction errors name entries, not the extraction folder.
        with patch("captain_share.imports.os.open", side_effect=OSError(36, "Name too long", temp)):
            code, body = self.upload(data)
        self.assertEqual(code, 400)
        self.assertNotIn(temp, body["error"])
        self.assertIn("Name too long", body["error"])

    def test_tour_page_gets_the_version(self):
        web = self.base / "web"
        web.mkdir()
        (web / "tour.html").write_text("<p>v__APP_VERSION__</p>")
        with patch("captain_share.server.WEB", web):
            self.start()
            code, _, body = self.get_raw("/tour")
        self.assertEqual(code, 200)
        self.assertEqual(body.decode(), f"<p>v{__version__}</p>")


class ImportCliTests(LibraryFixture):
    run_cli = test_library_home.CliTests.run_cli

    def test_import_previews_then_commits_with_note(self):
        downloads = self.base / "downloads"
        downloads.mkdir()
        archive = downloads / "pack.zip"
        archive.write_bytes(make_zip([*skill_entries("", "alpha"), *skill_entries("", "beta")]))
        target = ["--target", str(self.repo)]
        code, out, _ = self.run_cli(*target, "import", str(archive), "--source", "team")
        self.assertEqual(code, 0, out)
        self.assertIn("Commits only: ip/skills/alpha, ip/skills/beta", out)
        self.assertFalse((self.lib / "ip/skills/alpha").exists())
        code, _, err = self.run_cli(*target, "import", str(archive), "--source", "team", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("--note", err)
        code, _, err = self.run_cli(
            *target, "import", str(archive), "--source", "team", "--name", "missing"
        )
        self.assertEqual(code, 1)
        self.assertIn("found: alpha, beta", err)
        made, original = [], tempfile.mkdtemp

        def mkdtemp(**kwargs):
            # The tempfile module itself is patched; call the original.
            kwargs.setdefault("dir", self.base)
            path = original(**kwargs)
            if kwargs.get("prefix") == "captain-share-import-":
                made.append(Path(path))
            return path

        with patch("captain_share.cli.tempfile.mkdtemp", side_effect=mkdtemp):
            code, out, _ = self.run_cli(
                *target,
                "--json",
                "import",
                str(archive),
                "--source",
                "team",
                "--name",
                "beta",
                "--note",
                "Add beta",
                "--apply",
            )
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["get_commands"], ["captain-share get beta"])
        self.assertTrue((self.lib / "ip/skills/beta/SKILL.md").exists())
        self.assertFalse((self.lib / "ip/skills/alpha").exists())
        subject = e.git(self.lib, "log", "-1", "--format=%s").stdout.decode().strip()
        self.assertEqual(subject, "Add beta")
        # The temporary extraction is gone; importing beta again is a conflict.
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0].exists())
        code, out, _ = self.run_cli(
            *target, "import", str(archive), "--source", "team", "--name", "beta"
        )
        self.assertEqual(code, 1)
        self.assertIn("already has skills/beta", out)

    def test_import_folder_markdown_as_steering(self):
        notes = self.base / "notes"
        notes.mkdir()
        (notes / "style.md").write_text("# House style")
        code, out, _ = self.run_cli(
            "--target",
            str(self.repo),
            "import",
            str(notes),
            "--source",
            "team",
            "--kind",
            "steering",
            "--name",
            "house-style.md",
            "--note",
            "Add house style",
            "--apply",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("captain-share get house-style.md", out)
        self.assertTrue((self.lib / "ip/steering/house-style.md").is_file())
        self.assertEqual(os.listdir(notes), ["style.md"])
        plain = self.base / "plain.txt"
        plain.write_text("not a zip")
        code, _, err = self.run_cli(
            "--target", str(self.repo), "import", str(plain), "--source", "team"
        )
        self.assertEqual(code, 1)
        self.assertIn("Not a zip file", err)
