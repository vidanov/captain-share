import json
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from captain_share import config as c
from captain_share import engine as e
from captain_share import personal as me
from captain_share import repositories as r
from tests import test_library_home
from tests.test_library_home import LibraryFixture
from tests.test_personal import PersonalFixture

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = (
    "remote: GitLab: You are not allowed to push code to protected branches on this project.\n"
    "To gitlab.com:team/skills.git\n"
    " ! [remote rejected] main -> main (pre-receive hook declined)\n"
)
UNSIGNED = (
    "remote: GitLab: Commit must be signed with a GPG key\n"
    " ! [remote rejected] main -> main (pre-receive hook declined)\n"
)


def out(path, *args):
    return e.git(path, *args).stdout.decode().strip()


def hook(bare, script):
    """Install a pre-receive hook in a bare remote."""
    path = Path(bare) / "hooks/pre-receive"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)


class ClassifierTests(LibraryFixture):
    def friendly(self, text, remote=None):
        if remote:
            e.git(self.lib, "remote", "add", "origin", remote)
        return r.friendly(text, self.lib)

    def test_https_login_suggests_the_ssh_address_without_credentials(self):
        text = (
            "fatal: could not read Username for 'https://gitlab.com': terminal prompts disabled\n"
            "remote: HTTP Basic: Access denied to https://ada:s3cret@gitlab.com/team/skills.git"
        )
        error = self.friendly(text, "https://ada:s3cret@gitlab.com/group/sub/skills.git")
        self.assertIn("cannot type passwords", str(error))
        self.assertIn("credential helper", error.hint)
        self.assertEqual(
            error.command,
            f"git -C {self.lib} remote set-url origin git@gitlab.com:group/sub/skills.git",
        )
        self.assertIn("HTTP Basic: Access denied", error.detail)
        for value in (str(error), error.hint, error.command, error.detail):
            self.assertNotIn("s3cret", value)
            self.assertNotIn("ada:", value)
        self.assertEqual(set(error.fields()), {"hint", "command", "detail"})

    def test_ssh_address_for_each_host(self):
        for url, ssh in (
            ("https://github.com/team/skills.git", "git@github.com:team/skills.git"),
            ("https://token@gitlab.example.com/a/b/c", "git@gitlab.example.com:a/b/c.git"),
            ("https://git.example.org/x/y.git", "git@git.example.org:x/y.git"),
        ):
            self.assertEqual(r.ssh_address(url), ssh)
        for url in ("git@github.com:team/skills.git", "/srv/repo.git", "", None):
            self.assertIsNone(r.ssh_address(url))
        self.assertEqual(
            r.redact_text("see https://u:p@host.example/x and ssh://git:pw@h/y"),
            "see https://host.example/x and ssh://h/y",
        )

    def test_each_failure_kind(self):
        e.git(self.lib, "remote", "add", "origin", "git@gitlab.com:team/skills.git")
        cases = [
            ("git@gitlab.com: Permission denied (publickey).", "SSH key", "ssh -T git@gitlab.com"),
            ("Host key verification failed.", "host's SSH key", "ssh -T git@gitlab.com"),
            (
                "error: gpg failed to sign the data\nfatal: failed to write commit object",
                "could not sign",
                f"git -C {self.lib} config user.signingkey YOUR_KEY_ID",
            ),
            (
                UNSIGNED,
                "only accepts signed commits",
                f"git -C {self.lib} config commit.gpgsign true",
            ),
            (PROTECTED, "protected", None),
            (" ! [rejected] main -> main (fetch first)", "newer commits", None),
            ("hint: Updates were rejected (non-fast-forward)", "newer commits", None),
        ]
        for text, words, command in cases:
            error = r.friendly(text, self.lib)
            self.assertIn(words, str(error), text)
            self.assertTrue(error.hint, text)
            self.assertEqual(error.command, command, text)
            self.assertEqual(error.detail, text.strip())
        self.assertIn("Ask for review", r.friendly(PROTECTED, self.lib).hint)
        self.assertIn("Update library", r.friendly("(fetch first)", self.lib).hint)
        plain = r.friendly("fatal: something unusual", self.lib)
        self.assertEqual((str(plain), plain.fields()), ("fatal: something unusual", {}))

    def test_production_code_never_disables_signing(self):
        for path in (ROOT / "captain_share").glob("*.py"):
            self.assertNotIn("gpgsign=false", path.read_text().replace(" ", ""), path.name)


class StatusMemoryTests(LibraryFixture):
    def test_fetch_and_pull_are_remembered_and_freshness_follows_head(self):
        remote = self.remote()
        other = self.base / "colleague"
        e.git(self.base, "clone", "-q", str(remote), str(other))
        self.skill(other / "ip/skills/news")
        self.commit(other, "News")
        e.git(other, "push", "-q")
        self.start()
        _, home = self.request("/api/home")
        self.assertEqual(
            (home["libraries"][0]["checked_at"], home["libraries"][0]["checked_fresh"]),
            (None, False),
        )
        code, fetched = self.request("/api/fetch", {"source": "team"})
        self.assertEqual(code, 200, fetched)
        saved = json.loads((c.global_dir() / "library-status.json").read_text())
        self.assertEqual(set(saved["team"]), {"checked_at", "behind", "ahead", "head"})
        self.assertEqual((saved["team"]["behind"], saved["team"]["ahead"]), (1, 0))
        _, home = self.request("/api/home")
        lib = home["libraries"][0]
        self.assertTrue(lib["checked_fresh"])
        self.assertEqual((lib["behind"], lib["checked_at"]), (1, saved["team"]["checked_at"]))
        later = datetime.now(timezone.utc) + timedelta(hours=7)
        self.assertFalse(r.freshness("team", saved["team"]["head"], later)[1])
        code, _ = self.request(
            "/api/git", {"source": "team", "action": "pull", "fingerprint": fetched["fingerprint"]}
        )
        self.assertEqual(code, 200)
        saved = json.loads((c.global_dir() / "library-status.json").read_text())
        self.assertEqual(saved["team"]["behind"], 0)
        self.skill(self.lib / "ip/skills/local")
        self.commit(self.lib, "Local work")
        _, home = self.request("/api/home")
        self.assertFalse(home["libraries"][0]["checked_fresh"])


class PersonalStatusTests(PersonalFixture):
    def test_personal_library_reports_no_remembered_status(self):
        me.ensure()
        r.remember("my-skills", {"behind": 3, "ahead": 0, "head": "0" * 40})
        self.start()
        _, home = self.request("/api/home")
        mine = next(row for row in home["libraries"] if row["source"] == "my-skills")
        self.assertEqual(
            (mine["checked_at"], mine["checked_fresh"], mine["behind"]), (None, False, None)
        )


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is needed for SSH signing")
class SigningTests(LibraryFixture):
    def signing_key(self, broken=False):
        key = self.base / "signing-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
            capture_output=True,
        )
        e.git(self.lib, "config", "gpg.format", "ssh")
        e.git(
            self.lib,
            "config",
            "user.signingkey",
            str(self.base / "missing-key") if broken else str(key),
        )

    def test_sign_opt_in_signs_direct_and_review_commits(self):
        self.signing_key()
        self.remote()
        self.start()
        self.assertEqual(
            self.request("/api/library-settings", {"source": "team", "sign": "yes"})[0], 400
        )
        self.assertEqual(
            self.request("/api/library-settings", {"source": "nope", "sign": True})[0], 400
        )
        code, saved = self.request("/api/library-settings", {"source": "team", "sign": True})
        self.assertEqual((code, saved), (200, {"source": "team", "sign": True}))
        local = json.loads((self.repo / ".captain/sharing.local.json").read_text())
        self.assertIs(local["sources"]["team"]["sign"], True)
        _, state = self.request("/api/state")
        self.assertIs(state["config"]["sources"]["team"]["sign"], True)
        _, home = self.request("/api/home")
        self.assertIs(home["libraries"][0]["sign"], True)
        # Re-connecting the library keeps the machine-local opt-in.
        c.add_source(self.repo, "team", self.lib)
        self.assertTrue(c.effective(self.repo)["sources"]["team"]["sign"])
        self.skill(self.repo / "skills/signed")
        body = {"source": "team", "kind": "skills", "name": "signed", "path": "skills/signed"}
        _, plan = self.request("/api/share-plan", body)
        code, result = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Signed share"}
        )
        self.assertEqual(code, 200, result)
        self.assertIn("gpgsig", out(self.lib, "cat-file", "commit", result["commit"]))
        self.skill(self.repo / "skills/reviewed")
        body = {**body, "name": "reviewed", "path": "skills/reviewed", "mode": "review"}
        _, plan = self.request("/api/share-plan", body)
        code, result = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Signed review"}
        )
        self.assertEqual(code, 200, result)
        self.assertIn("gpgsig", out(self.lib, "cat-file", "commit", result["commit"]))

    def test_failed_signing_refuses_with_a_hint_and_changes_nothing(self):
        self.signing_key(broken=True)
        c.set_sign(self.repo, "team", True)
        head = out(self.lib, "rev-parse", "HEAD")
        self.skill(self.repo / "skills/signed")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "signed", "path": "skills/signed"}
        _, plan = self.request("/api/share-plan", body)
        code, failed = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Signed share"}
        )
        self.assertEqual(code, 400)
        self.assertEqual(failed["error"], "Git could not sign the commit.")
        self.assertIn("user.signingkey", failed["command"])
        self.assertTrue(failed["hint"] and failed["detail"])
        self.assertFalse((self.lib / "ip/skills/signed").exists())
        self.assertEqual(out(self.lib, "rev-parse", "HEAD"), head)
        self.assertEqual(out(self.lib, "status", "--porcelain"), "")


class ReviewModeTests(LibraryFixture):
    def gitlab_remote(self):
        """A bare remote that pretends to be GitLab: protected main, push options
        recorded, and a merge request link printed for review branches."""
        bare = self.remote()
        e.git(bare, "config", "receive.advertisePushOptions", "true")
        hook(
            bare,
            'echo "$GIT_PUSH_OPTION_0|$GIT_PUSH_OPTION_1|$GIT_PUSH_OPTION_2" > '
            f'"{self.base}/push-options"\n'
            "while read old new ref; do\n"
            '  if [ "$ref" = "refs/heads/main" ]; then\n'
            '    echo "GitLab: You are not allowed to push code to protected branches on this project."\n'
            "    exit 1\n"
            "  fi\n"
            '  echo "To create a merge request for ${ref#refs/heads/}, visit:"\n'
            '  echo "  https://gitlab.example.com/team/skills/-/merge_requests/new?merge_request%5Bsource_branch%5D=x"\n'
            "done\n",
        )
        e.git(self.lib, "remote", "set-url", "origin", "https://gitlab.example.com/team/skills.git")
        e.git(self.lib, "remote", "set-url", "--push", "origin", str(bare))
        return bare

    def untouched(self):
        return (
            out(self.lib, "rev-parse", "HEAD"),
            out(self.lib, "status", "--porcelain"),
            out(self.lib, "symbolic-ref", "HEAD"),
        )

    def test_protected_push_explains_and_review_opens_a_merge_request(self):
        bare = self.gitlab_remote()
        self.skill(self.repo / "skills/mine")
        self.start()
        body = {"source": "team", "kind": "skills", "name": "mine", "path": "skills/mine"}
        _, plan = self.request("/api/share-plan", body)
        code, direct = self.request(
            "/api/share-apply", {"fingerprint": plan["fingerprint"], "note": "Mine", "push": True}
        )
        self.assertEqual(code, 200, direct)
        self.assertFalse(direct["pushed"])
        self.assertIn("protected", direct["push_error"])
        self.assertIn("Ask for review", direct["push_error_hint"])
        self.assertIsNone(direct["push_error_command"])
        self.assertEqual(out(self.lib, "rev-parse", "HEAD"), direct["commit"])
        self.assertFalse(direct["waiting_for_review"])
        # Review mode for the next asset: nothing local changes, the branch is pushed.
        self.skill(self.repo / "skills/other")
        before = self.untouched()
        body = {**body, "name": "other", "path": "skills/other", "mode": "review", "adopt": True}
        code, plan = self.request("/api/share-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan["can_apply"], plan["errors"])
        self.assertEqual((plan["mode"], plan["branch"]), ("review", "share/other-test-sharer"))
        self.assertEqual(plan["adopt_changes"], [])
        code, result = self.request(
            "/api/share-apply",
            {"fingerprint": plan["fingerprint"], "note": "Add other\n\nDetails", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            (result["pushed"], result["waiting_for_review"], result["adopted"]), (True, True, False)
        )
        self.assertEqual(
            result["review_url"],
            "https://gitlab.example.com/team/skills/-/merge_requests/new?"
            "merge_request%5Bsource_branch%5D=x",
        )
        self.assertEqual(
            (self.base / "push-options").read_text().strip(),
            "merge_request.create|merge_request.target=main|merge_request.title=Add other",
        )
        self.assertEqual(self.untouched(), before)
        self.assertFalse((self.lib / "ip/skills/other").exists())
        self.assertEqual(
            out(bare, "rev-parse", "refs/heads/share/other-test-sharer"), result["commit"]
        )
        self.assertIn(
            "ip/skills/other/SKILL.md", out(self.lib, "show", "--name-only", result["commit"])
        )
        self.assertTrue((self.repo / "skills/other").is_dir())

    def test_import_and_starter_review_leave_the_checkout_alone(self):
        bare = self.gitlab_remote()
        self.start()
        outside = self.base / "outside"
        self.skill(outside / "fresh")
        before = self.untouched()
        _, stage = self.request("/api/import-folder", {"path": str(outside)})
        item = {"id": "fresh", "kind": "skills", "name": "fresh"}
        code, plan = self.request(
            "/api/import-plan",
            {"source": "team", "stage": stage["stage"], "items": [item], "mode": "review"},
        )
        self.assertEqual(code, 200, plan)
        self.assertEqual(plan["branch"], "share/fresh-test-sharer")
        code, result = self.request(
            "/api/import-apply", {"fingerprint": plan["fingerprint"], "note": "Fresh", "push": True}
        )
        self.assertEqual(code, 200, result)
        self.assertTrue(result["waiting_for_review"] and result["pushed"])
        self.assertEqual(result["get_commands"], ["captain-share get fresh"])
        self.assertEqual(
            out(bare, "rev-parse", "refs/heads/share/fresh-test-sharer"), result["commit"]
        )
        self.assertFalse((self.lib / "ip/skills/fresh").exists())
        code, plan = self.request(
            "/api/starter-plan", {"source": "team", "assets": ["skills/hello"], "mode": "review"}
        )
        self.assertEqual(code, 200, plan)
        self.assertEqual(plan["branch"], "share/starter-set-test-sharer")
        code, result = self.request(
            "/api/starter-apply",
            {"fingerprint": plan["fingerprint"], "note": "Starter", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertTrue(result["waiting_for_review"])
        manifest = out(self.lib, "show", f"{result['commit']}:profiles/starter.manifest")
        self.assertIn("skills: hello", manifest)
        self.assertFalse((self.lib / "profiles/starter.manifest").exists())
        self.assertEqual(self.untouched(), before)
        _, again = self.request(
            "/api/starter-plan", {"source": "team", "assets": ["skills/hello"], "mode": "review"}
        )
        self.assertIn("branch", [x["reason"] for x in again["conflicts"]])
        self.assertEqual(
            self.request(
                "/api/starter-plan", {"source": "team", "assets": ["skills/hello"], "mode": "x"}
            )[0],
            400,
        )

    def test_cli_review_and_hint_output(self):
        self.gitlab_remote()
        self.skill(self.repo / "skills/mine")
        run_cli = test_library_home.CliTests.run_cli
        base = ["--target", str(self.repo), "share", "skills", "mine", "--source", "team"]
        code, text, _ = run_cli(self, *base, "--note", "Mine", "--push", "--apply")
        self.assertEqual(code, 1, text)
        self.assertIn("Not pushed: The library's branch is protected", text)
        self.assertIn("Ask for review", text)
        self.skill(self.repo / "skills/second")
        base[4] = "second"
        code, text, _ = run_cli(self, *base, "--review", "--note", "Second", "--push", "--apply")
        self.assertEqual(code, 0, text)
        self.assertIn("Waiting for review on branch share/second-test-sharer.", text)
        self.assertIn("Open the review: https://gitlab.example.com/", text)


class PromoteReviewTests(PersonalFixture):
    def test_promote_review_copies_to_a_branch_only(self):
        bare = self.remote()
        c.add_source(self.home, "team", self.lib, scope="global")
        me.ensure()
        self.skill(self.mine / "skills/tryout")
        self.commit(self.mine, "Try")
        c.select(self.home, "inbound", "my-skills", "skills", "tryout")
        e.apply(e.plan(self.home))
        link = self.home / ".kiro/skills/tryout"
        lib_before = (out(self.lib, "rev-parse", "HEAD"), out(self.lib, "status", "--porcelain"))
        self.start()
        body = {"kind": "skills", "name": "tryout", "team": "team", "mode": "review"}
        code, plan = self.request("/api/promote-plan", body)
        self.assertEqual(code, 200, plan)
        self.assertEqual([row["op"] for row in plan["changes"]], ["copy"])
        self.assertEqual(plan["branch"], "share/tryout-test-sharer")
        code, result = self.request(
            "/api/promote-apply",
            {"fingerprint": plan["fingerprint"], "note": "Tryout", "push": True},
        )
        self.assertEqual(code, 200, result)
        self.assertEqual(
            (result["waiting_for_review"], result["relinked"], result["removed"], result["errors"]),
            (True, [], False, []),
        )
        self.assertEqual(
            out(bare, "rev-parse", "refs/heads/share/tryout-test-sharer"), result["commit"]
        )
        self.assertEqual(
            (out(self.lib, "rev-parse", "HEAD"), out(self.lib, "status", "--porcelain")), lib_before
        )
        self.assertTrue((self.mine / "skills/tryout").is_dir())
        self.assertEqual(Path(link).resolve(), (self.mine / "skills/tryout").resolve())
        self.assertIn("my-skills/skills/tryout", c.effective(self.home)["inbound"])
