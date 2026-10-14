from unittest.mock import patch

from captain_share import config as c
from captain_share import engine as e
from captain_share import server
from tests.test_library_home import LibraryFixture


class DiscoverTests(LibraryFixture):
    """The home folder is self.base; the fixture library (self.lib) is connected as team."""

    def flat(self, path, *names):
        for name in names:
            self.skill(path / "skills" / name)
        return path

    def found(self, result):
        return {row["path"]: row for row in result["libraries"]}

    def test_finds_libraries_marks_connected_and_respects_skips_and_depth(self):
        big = self.flat(self.base / "work" / "big-lib", "one", "two")
        small = self.flat(self.base / "work" / "deep" / "team", "solo")  # depth 3
        personal = self.flat(self.base / "personal-lib", "mine")
        c.add_source(self.home, "personal", personal)
        e.git(big, "init", "-q", "-b", "main")
        e.git(big, "remote", "add", "origin", "https://example.invalid/big-lib.git")
        # Not libraries, or not searched.
        self.flat(self.base / "work" / "deep" / "deeper" / "too-deep", "x")  # depth 4
        for skipped in ("Library", "node_modules", ".hidden", "venv", "Music", "Pictures"):
            self.flat(self.base / skipped / "lib", "x")
        (self.base / "empty-layout" / "skills" / "broken").mkdir(parents=True)  # no valid asset
        self.flat(self.base / "empty-layout" / "inside", "found-below")
        self.skill(self.base / "project" / "repo" / ".kiro" / "skills" / "local")
        self.skill(self.base / ".kiro" / "skills" / "user-wide")
        # A library's own folders are not searched for more libraries.
        self.flat(big / "nested", "hidden-inside")

        result = server.discover(self.repo)
        self.assertEqual(result["searched"], [str(self.base)])
        self.assertFalse(result["truncated"])
        rows = self.found(result)
        self.assertEqual(
            set(rows),
            {
                str(big),
                str(small),
                str(personal),
                str(self.lib),
                str(self.base / "empty-layout" / "inside"),
            },
        )
        self.assertEqual(
            rows[str(big)],
            {
                "path": str(big),
                "name": "big-lib",
                "layout": "flat",
                "is_repo": True,
                "remote_url": "https://example.invalid/big-lib.git",
                "counts": {"skills": 2},
                "total": 2,
                "connected": None,
            },
        )
        # A suggested name never repeats a connected source's name.
        self.assertEqual(rows[str(small)]["name"], "team-2")
        self.assertEqual(
            (rows[str(small)]["is_repo"], rows[str(small)]["remote_url"]), (False, None)
        )
        self.assertEqual(rows[str(self.lib)]["connected"], "team")
        self.assertEqual(rows[str(self.lib)]["layout"], "ip")
        self.assertEqual(rows[str(personal)]["connected"], "personal")
        # Not connected first, then the most assets.
        order = [row["path"] for row in result["libraries"]]
        self.assertEqual(order[0], str(big))
        self.assertEqual(
            {row["connected"] for row in result["libraries"][3:]}, {"team", "personal"}
        )

    def test_budget_and_time_limit_truncate(self):
        for name in ("a", "b", "c"):
            self.flat(self.base / "zz" / name, "x")
        with patch.object(server, "DISCOVER_BUDGET", 2):
            result = server.discover(self.repo)
        self.assertTrue(result["truncated"])
        self.assertFalse(any("/zz/" in row["path"] for row in result["libraries"]))
        with patch.object(server, "DISCOVER_SECONDS", -1):
            result = server.discover(self.repo)
        self.assertEqual((result["truncated"], result["libraries"]), (True, []))

    def test_api_is_cached_per_session(self):
        self.start()
        code, first = self.request("/api/discover")
        self.assertEqual(code, 200)
        self.assertEqual(set(first), {"libraries", "searched", "truncated"})
        self.assertEqual(self.request("/api/discover", token=False)[0], 403)
        later = self.flat(self.base / "later-lib", "new")
        self.assertEqual(self.request("/api/discover")[1], first)
        with patch.object(server, "DISCOVER_CACHE", -1):
            _, fresh = self.request("/api/discover")
        self.assertIn(str(later), self.found(fresh))
