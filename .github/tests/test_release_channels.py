"""Run with PyYAML and jq installed; all GitHub API requests are mocked."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
REPO = "LOVECHEN/v2rayN-mod"
API = "https://api.github.com/repos/"
UPSTREAM = API + "2dust/v2rayN/releases"
MOD = API + REPO + "/releases"

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default
url = next(arg for arg in args if arg.startswith("https://"))
method = option("-X", "GET")
data = option("-d")
if data and data.startswith("@"):
    data = pathlib.Path(data[1:]).read_text()
calls_file = pathlib.Path(os.environ["MOCK_CALLS"])
calls = json.loads(calls_file.read_text())
calls.append({"method": method, "url": url, "data": json.loads(data) if data else None})
calls_file.write_text(json.dumps(calls))
replies = json.loads(os.environ["MOCK_REPLIES"])
key = method + " " + url
if key not in replies:
    sys.stderr.write("Unexpected API request: " + key + "\n")
    sys.exit(99)
status, body = replies[key]
body = json.dumps(body)
output = option("-o")
if output:
    pathlib.Path(output).write_text(body)
else:
    sys.stdout.write(body)
if "-w" in args:
    sys.stdout.write(str(status))
if status >= 400 and ("--fail-with-body" in args or "-f" in args):
    sys.exit(22)
'''


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def step(name, title, job="sync"):
    return next(s for s in workflow(name)["jobs"][job]["steps"] if s.get("name") == title)


class ReleaseChannels(unittest.TestCase):
    def run_script(self, script, replies, **extra_env):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            curl = temp / "curl"
            curl.write_text(FAKE_CURL)
            curl.chmod(0o755)
            calls = temp / "calls.json"
            calls.write_text("[]")
            output = temp / "output"
            output.touch()
            script = script.replace("${{ github.repository }}", REPO)
            script = script.replace("${{ github.event.inputs.tag }}", "7.25.6")
            script = script.replace("${{ github.event.inputs.prerelease }}", extra_env.pop("PRE", "false"))
            script = script.replace("/tmp/", str(temp) + "/")
            result = subprocess.run(
                ["bash", "-e", "-c", script], text=True, capture_output=True,
                env={**os.environ, "PATH": str(temp) + ":" + os.environ["PATH"],
                     "GH_TOKEN": "mock-token", "GITHUB_OUTPUT": str(output),
                     "OVERRIDE_PRE": "",
                     "RUNNER_TEMP": str(temp), "MOCK_CALLS": str(calls),
                     "MOCK_REPLIES": json.dumps(replies), **extra_env},
            )
            return result, json.loads(calls.read_text()), output.read_text()

    def check(self, upstream_pre, mod_pre, mod_status=200, patch_status=200):
        replies = {
            "GET " + UPSTREAM + "?per_page=1": [200, [{"tag_name": "7.25.6", "prerelease": upstream_pre}]],
            "GET " + MOD + "/tags/7.25.6": [mod_status, {"id": 123, "prerelease": mod_pre}],
            "PATCH " + MOD + "/123": [patch_status, {}],
        }
        return self.run_script(step("sync-upstream.yml", "Check upstream latest release")["run"], replies)

    def test_latest_prerelease_promoted_without_rebuild(self):
        result, calls, output = self.check(False, True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip=true", output)
        self.assertEqual(calls[-1]["data"], {"prerelease": False, "make_latest": "true"})

    def test_existing_matching_channels_are_idempotent(self):
        for pre in (False, True):
            with self.subTest(pre=pre):
                result, calls, output = self.check(pre, pre)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 2)
                self.assertIn("skip=true", output)

    def test_new_release_proceeds_only_on_404(self):
        result, calls, output = self.check(False, None, mod_status=404)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip=false", output)
        self.assertEqual(len(calls), 2)

    def test_mod_api_errors_fail_without_triggering_sync(self):
        for status in (403, 500):
            with self.subTest(status=status):
                result, calls, output = self.check(False, None, mod_status=status)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("skip=false", output)
                self.assertEqual(len(calls), 2)

    def test_failed_promotion_is_not_reported_as_success(self):
        result, _, output = self.check(False, True, patch_status=403)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("skip=true", output)

    def stable(self, latest_is_stable, mod_status=200, mod_pre=True):
        releases = [{"tag_name": "7.25.6", "prerelease": False}]
        if not latest_is_stable:
            releases.insert(0, {"tag_name": "7.25.7", "prerelease": True})
        replies = {
            "GET " + UPSTREAM + "?per_page=30": [200, releases],
            "GET " + MOD + "/tags/7.25.6": [mod_status, {"id": 123, "prerelease": mod_pre}],
            "PATCH " + MOD + "/123": [200, {}],
            "POST " + API + REPO + "/actions/workflows/backfill.yml/dispatches": [204, None],
        }
        return self.run_script(step("sync-upstream.yml", "Ensure latest upstream stable is mirrored")["run"], replies)

    def test_stable_channel_is_repaired_even_when_it_is_latest(self):
        for latest_is_stable in (False, True):
            with self.subTest(latest_is_stable=latest_is_stable):
                result, calls, _ = self.stable(latest_is_stable)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[-1]["method"], "PATCH")
                self.assertEqual(calls[-1]["data"], {"prerelease": False, "make_latest": "true"})

    def test_stable_channel_is_idempotent(self):
        result, calls, _ = self.stable(True, mod_pre=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)

    def test_missing_older_stable_dispatches_backfill(self):
        result, calls, _ = self.stable(False, mod_status=404)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["method"], "POST")
        self.assertEqual(calls[-1]["data"]["inputs"], {"tag": "7.25.6", "prerelease": "false"})

    def test_missing_latest_stable_does_not_dispatch_duplicate_build(self):
        result, calls, _ = self.stable(True, mod_status=404)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)

    def test_stable_api_failure_does_not_dispatch_backfill(self):
        result, calls, _ = self.stable(False, mod_status=503)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_composite_preserves_boolean_false_and_true(self):
        action = yaml.safe_load((ROOT / ".github/actions/release-state/action.yml").read_text())
        for pre in (False, True):
            with self.subTest(pre=pre):
                result, _, output = self.run_script(action["runs"]["steps"][0]["run"], {
                    "GET " + UPSTREAM + "/tags/7.25.6": [200, {"prerelease": pre}],
                }, RELEASE_TAG="7.25.6")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.strip(), "prerelease=" + str(pre).lower())

    def test_composite_rejects_missing_and_non_boolean_flags(self):
        action = yaml.safe_load((ROOT / ".github/actions/release-state/action.yml").read_text())
        for body in ({}, {"prerelease": "false"}, {"message": "API rate limit exceeded"}):
            with self.subTest(body=body):
                result, _, output = self.run_script(action["runs"]["steps"][0]["run"], {
                    "GET " + UPSTREAM + "/tags/7.25.6": [200, body],
                }, RELEASE_TAG="7.25.6")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")

    def test_composite_rejects_http_errors(self):
        action = yaml.safe_load((ROOT / ".github/actions/release-state/action.yml").read_text())
        result, _, output = self.run_script(action["runs"]["steps"][0]["run"], {
            "GET " + UPSTREAM + "/tags/7.25.6": [403, {"message": "rate limited"}],
        }, RELEASE_TAG="7.25.6")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def test_composite_respects_explicit_backfill_channel(self):
        action = yaml.safe_load((ROOT / ".github/actions/release-state/action.yml").read_text())
        for pre in ("true", "false", "invalid"):
            with self.subTest(pre=pre):
                result, calls, output = self.run_script(action["runs"]["steps"][0]["run"], {}, OVERRIDE_PRE=pre)
                self.assertEqual(calls, [])
                if pre == "invalid":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.strip(), "prerelease=" + pre)

    def test_backfill_updates_existing_metadata(self):
        script = step("backfill.yml", "Ensure release exists", job="prepare")["run"]
        for pre in ("true", "false"):
            with self.subTest(pre=pre):
                result, calls, _ = self.run_script(script, {
                    "GET " + MOD + "/tags/7.25.6": [200, {"id": 123}],
                    "PATCH " + MOD + "/123": [200, {}],
                }, PRE=pre)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[-1]["data"], {"prerelease": pre == "true"})

    def test_all_uploads_resolve_channel_and_promote_stable(self):
        uploads = 0
        for name in ("build-windows.yml", "build-windows-desktop.yml", "build-linux.yml", "build-osx.yml"):
            for job in workflow(name)["jobs"].values():
                resolver_seen = False
                for item in job["steps"]:
                    if item.get("uses") == "./.github/actions/release-state":
                        resolver_seen = item["id"] == "release"
                    if item.get("uses", "").startswith("svenstaro/upload-release-action@"):
                        uploads += 1
                        self.assertTrue(resolver_seen, name)
                        self.assertEqual(item["with"]["prerelease"], "${{ steps.release.outputs.prerelease }}")
                        self.assertEqual(item["with"]["promote"], "${{ steps.release.outputs.prerelease == 'false' }}")
        self.assertEqual(uploads, 7)
        prepare = step("backfill.yml", "Build bf branch (upstream tag + de-promo + build tooling)", job="prepare")["run"]
        self.assertIn("cp -r .github/actions/release-state /tmp/ghsave/", prepare)
        self.assertIn("cp -r /tmp/ghsave/release-state .github/actions/", prepare)
        dispatch = step("backfill.yml", "Dispatch platform builds", job="prepare")["run"]
        self.assertIn('\\"prerelease\\":\\"${{ github.event.inputs.prerelease }}\\"', dispatch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
