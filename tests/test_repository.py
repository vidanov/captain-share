import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from captain_share import config as c
from captain_share import engine as e
from scripts import check_repository, verify
from tests.test_sharing import SharingFixture

ROOT = Path(__file__).resolve().parents[1]


class RepositoryChecks(unittest.TestCase):
    def fixture(self, directory):
        target = Path(directory)
        for name in ("captain_share", "docs"):
            shutil.copytree(
                ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md"):
            shutil.copy2(ROOT / name, target / name)
        return target

    def test_boundary_allows_actual_graph_and_rejects_planted_edge(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(directory)
            self.assertEqual(check_repository.inspect(target), [])
            path = target / "captain_share/engine.py"
            path.write_text(path.read_text() + "\nfrom captain_share import server\n")
            failures = check_repository.inspect(target)
            self.assertTrue(
                any("IMPORT_BOUNDARY:" in f and "cannot import server" in f for f in failures)
            )
            self.assertFalse(any("INVALID_MEASUREMENT" in f for f in failures))

    def test_parse_failure_is_not_mislabeled_as_boundary_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(directory)
            (target / "captain_share/engine.py").write_text("this is not: Python")
            failures = check_repository.inspect(target)
            self.assertTrue(any("INVALID_MEASUREMENT:" in f for f in failures))
            self.assertFalse(any("IMPORT_BOUNDARY:" in f for f in failures))

    def test_broken_local_document_link_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.fixture(directory)
            (target / "README.md").write_text("[missing](docs/does-not-exist.md)\n")
            self.assertIn(
                "BROKEN_LINK: README.md -> docs/does-not-exist.md", check_repository.inspect(target)
            )

    def test_runner_preserves_failure_and_stops_later_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            log = io.StringIO()
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                result = verify.run_steps(
                    [
                        ("planned failure", [sys.executable, "-c", "raise SystemExit(7)"]),
                        ("must not run", [sys.executable, "-c", 'open("unexpected", "w").close()']),
                    ],
                    directory,
                )
            self.assertEqual(result, 7)
            self.assertIn("FAILED: planned failure (exit 7)", log.getvalue())
            self.assertFalse((Path(directory) / "unexpected").exists())

    def test_missing_check_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            log = io.StringIO()
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                result = verify.run_steps(
                    [("missing tool", [str(Path(directory) / "missing")])], directory
                )
            self.assertEqual(result, 2)
            self.assertIn("INVALID_MEASUREMENT: missing tool", log.getvalue())


class ScopeAndResults(SharingFixture):
    def cli(self, cwd, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin/captain-share"), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def test_default_is_exact_current_folder_and_target_overrides_it(self):
        child = self.repo / "subfolder"
        child.mkdir()
        default = self.cli(child, "--json", "status")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(json.loads(default.stdout)["target"], str(child))
        override = self.cli(child, "--target", str(self.repo), "--json", "status")
        self.assertEqual(override.returncode, 0, override.stderr)
        self.assertEqual(json.loads(override.stdout)["target"], str(self.repo))

    def test_status_reports_drift_as_failure_through_cli(self):
        self.select()
        e.apply(e.plan(self.repo))
        (self.repo / ".kiro/skills/hello").unlink()
        result = self.cli(self.repo, "--json", "status")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["installed"][0]["status"], "drift")

    def test_explicit_target_cannot_apply_another_folders_plan(self):
        self.select()
        saved = self.base / "plan.json"
        saved.write_text(json.dumps(e.plan(self.repo)))
        result = self.cli(self.repo, "--target", str(self.lib), "--json", "apply", str(saved))
        self.assertEqual(result.returncode, 1)
        self.assertIn("different folder", json.loads(result.stdout)["error"])
        self.assertFalse((self.repo / ".kiro").exists())

    def test_config_change_stales_saved_preview(self):
        self.select()
        saved = e.plan(self.repo)
        c.select(self.repo, "inbound", "team", "skills", "hello", alias="new-alias")
        with self.assertRaisesRegex(c.ShareError, "Plan is stale"):
            e.apply(saved)
        self.assertFalse((self.repo / ".kiro").exists())
