import contextlib
import io
import os
import shutil
from pathlib import Path
from unittest.mock import patch

from captain_share import cli
from captain_share import config as c
from captain_share import engine as e
from captain_share import imports as im
from tests.test_library_home import LibraryFixture


def repository(base):
    """A downloaded repository with skills nested by category, like many public ones."""
    root = base / "download"
    for category, name in (("engineering", "tdd"), ("engineering", "review-it"), ("misc", "notes")):
        folder = root / "skills" / category / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n"
        )
    (root / "skills/deprecated").mkdir()
    (root / "skills/deprecated/README.md").write_text("Old")
    (root / "README.md").write_text("Docs, not a prompt")
    return root


class AddFromGitTests(LibraryFixture):
    def test_repository_addresses(self):
        self.assertEqual(
            im.repository_url("mattpocock/skills"), "https://github.com/mattpocock/skills"
        )
        for good in (
            "https://github.com/a/b",
            "https://gitlab.example.com/g/sub/r.git",
            "git@github.com:a/b.git",
        ):
            self.assertEqual(im.repository_url(good), good)
        for bad in (
            "file:///etc",
            "https://user:secret@github.com/a/b",
            "-uhack",
            "../x",
            "",
            "ext::sh -c x",
        ):
            with self.assertRaises(c.ShareError):
                im.repository_url(bad)

    def test_skills_at_any_depth_and_no_noise(self):
        root = repository(self.base)
        rows = im.find_repository(root, "example")
        self.assertEqual(
            [(x["name"], x["id"]) for x in rows],
            [
                ("notes", "skills/misc/notes"),
                ("review-it", "skills/engineering/review-it"),
                ("tdd", "skills/engineering/tdd"),
            ],
        )
        self.assertTrue(all(x["error"] is None for x in rows))

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--target", str(self.repo), *args])
        return code, out.getvalue()

    def test_add_lists_then_installs_one_skill_with_its_source(self):
        root = repository(self.base)

        def fake_clone(url, dest, ref=None):
            shutil.copytree(root, dest)
            return Path(dest), "abc1234def", "example.com/team/skills@abc1234"

        home = Path(os.environ["CAPTAIN_SHARE_HOME"]).parent
        (home / ".gitconfig").write_text(
            "[user]\n\tname = Ada Example\n\temail = ada@example.invalid\n"
            "[commit]\n\tgpgsign = false\n"
        )
        env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        cloning = patch.object(im, "clone", side_effect=fake_clone)
        with cloning, patch.dict(os.environ, env):
            code, out = self.run_cli("add", "team/skills")
            self.assertEqual(code, 0)
            self.assertIn("tdd · tdd skill", out)
            code, out = self.run_cli("add", "team/skills", "--skill", "missing", "--apply")
            self.assertNotEqual(code, 0)
            code, out = self.run_cli("add", "team/skills", "--skill", "tdd", "--apply")
        self.assertEqual(code, 0, out)
        library = home / "Libraries/my-skills"
        self.assertTrue((library / "skills/tdd/SKILL.md").is_file())
        self.assertFalse((library / "skills/review-it").exists())
        message = e.git(library, "log", "-1", "--format=%s").stdout.decode().strip()
        self.assertEqual(message, "Add tdd from example.com/team/skills@abc1234")
        self.assertTrue((home / ".kiro/skills/tdd").is_symlink())
