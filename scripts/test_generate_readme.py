"""Offline tests for the generator and the workflows.

  python3 -B -m unittest discover -s scripts
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import generate_readme  # noqa: E402

DATA = json.loads((ROOT / "boards.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "boards.schema.json").read_text(encoding="utf-8"))
WORKFLOWS = ROOT / ".github/workflows"


def load_workflow(name):
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("PyYAML not installed")
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        self.readme = generate_readme.render(DATA)

    def test_data_validates(self):
        self.assertEqual(generate_readme.validate(DATA, SCHEMA), [])
        try:
            import jsonschema
        except ImportError:
            return
        jsonschema.validate(DATA, SCHEMA)  # the stdlib subset agrees with a full validator

    def test_every_entry_once_and_text_only_unlinked(self):
        for b in DATA["boards"]:
            self.assertIn(b["name"], self.readme)
            if b["link"]:
                self.assertEqual(self.readme.count(f"]({b['url']})"), 1, b["name"])
            else:
                self.assertIn(f"**{b['name']}**", self.readme)
                if b["url"]:
                    host = re.sub(r"^https://|/.*$", "", b["url"])
                    self.assertNotRegex(self.readme, r"\]\([^)]*" + re.escape(host), b["name"])
        self.assertNotRegex(self.readme, r"\]\([^)]*moltsbooks")

    def test_awesome_conventions(self):
        links = re.findall(r"\]\((https?://[^)]+)\)", self.readme)
        self.assertEqual(len(links), len(set(links)), "duplicate links")
        self.assertIn("https://awesome.re/badge.svg", self.readme)
        self.assertIn("https://swarmmemo.com/guides/agent-board-map", self.readme)
        for line in self.readme.splitlines():
            if line.startswith("- ") and " - " in line:
                self.assertTrue(line.endswith("."), line)
        titles = [generate_readme.anchor(h[3:]) for h in self.readme.splitlines() if h.startswith("## ")]
        for anchor in re.findall(r"\(#([^)]+)\)", self.readme):
            self.assertIn(anchor, titles)

    def test_validation_catches_bad_entries(self):
        bad = json.loads(json.dumps(DATA))
        bad["boards"].append(dict(bad["boards"][0]))
        self.assertTrue(any("duplicate" in e for e in generate_readme.validate(bad, SCHEMA)))
        bad = json.loads(json.dumps(DATA))
        bad["boards"][-1]["reason"] = "No period"
        self.assertTrue(generate_readme.validate(bad, SCHEMA))

    def test_check_detects_stale_readme(self):
        with tempfile.TemporaryDirectory() as work:
            out = pathlib.Path(work)
            for name in ("boards.json", "boards.schema.json"):
                shutil.copyfile(ROOT / name, out / name)
            script = [sys.executable, "-B", str(HERE / "generate_readme.py")]
            self.assertEqual(subprocess.run(script + [str(out)], capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(script + ["--check", str(out)], capture_output=True).returncode, 0)
            (out / "README.md").write_text("stale", encoding="utf-8")
            self.assertEqual(subprocess.run(script + ["--check", str(out)], capture_output=True).returncode, 1)


class ReadmeWorkflowTest(unittest.TestCase):
    """Only the push-to-main job may write, and only its push step sees the token."""

    def test_shape(self):
        flow = load_workflow("readme.yml")
        self.assertEqual(set(flow[True]), {"push", "workflow_dispatch"})  # YAML 1.1 reads `on` as true
        self.assertEqual(flow[True]["push"], {"branches": ["main"]})
        self.assertEqual(flow["permissions"], {})
        job = flow["jobs"]["readme"]
        self.assertEqual(job["permissions"], {"contents": "write"})
        steps = job["steps"]
        uses = [s["uses"] for s in steps if "uses" in s]
        self.assertEqual(len(uses), 1)
        self.assertRegex(uses[0], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertIs(steps[0]["with"]["persist-credentials"], False)
        with_token = [s["name"] for s in steps if "GITHUB_TOKEN" in json.dumps(s)]
        self.assertEqual(with_token, ["Commit README.md if it changed"])

    def test_validate_is_read_only(self):
        flow = load_workflow("validate.yml")
        self.assertEqual(flow[True], {"pull_request": None})
        self.assertEqual(flow["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", (WORKFLOWS / "validate.yml").read_text(encoding="utf-8"))
        for name in ("readme.yml", "validate.yml"):
            self.assertNotIn("pull_request_target", (WORKFLOWS / name).read_text(encoding="utf-8"))


class AnnounceWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = (WORKFLOWS / "announce.yml").read_text(encoding="utf-8")
        self.flow = load_workflow("announce.yml")
        self.step = self.flow["jobs"]["announce"]["steps"][0]

    def test_shape(self):
        on = self.flow[True]
        self.assertEqual(on, {"pull_request_target": {"types": ["opened", "closed"]}})
        self.assertEqual(self.flow["permissions"], {})
        self.assertEqual(len(self.flow["jobs"]["announce"]["steps"]), 1)
        self.assertNotIn("uses", self.step)
        self.assertNotIn("${{", self.step["run"])
        self.assertNotIn("secrets.", self.text)
        self.assertIn("merged == true", self.flow["jobs"]["announce"]["if"])

    def dry_run(self, **env):
        base = {"PR_ACTION": "opened", "PR_NUMBER": "7", "PR_AUTHOR": "octocat", "PR_TITLE": "Add a board",
                "PR_URL": "https://github.com/Hugo0/awesome-agent-boards/pull/7", "DRY_RUN": "true"}
        base.update(env)
        return subprocess.run(["bash", "-c", self.step["run"]], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], **base})

    def test_dry_run_body(self):
        result = self.dry_run(PR_TITLE='Add "X"\n$(touch /tmp/pwned)' + chr(0x202e) + '`id`' + "y" * 200)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "dry run, would POST https://swarmmemo.com/w/boards/main")
        body = json.loads(lines[1])
        self.assertEqual(body["request_id"], "awesome-pr-7-opened")
        self.assertTrue(body["text"].startswith('awesome-agent-boards: PR #7 opened by @octocat: "Add "X" $(touch /tmp/pwned) `id`y'))
        self.assertTrue(body["text"].endswith(chr(0x2026) + '" <https://github.com/Hugo0/awesome-agent-boards/pull/7>'))
        title = body["text"].split(': "', 1)[1].rsplit('" <', 1)[0]
        self.assertEqual(len(title), 120)
        self.assertFalse(re.search(r"[\x00-\x1f‮]", body["text"]))
        merged = json.loads(self.dry_run(PR_ACTION="merged").stdout.splitlines()[1])
        self.assertEqual(merged["request_id"], "awesome-pr-7-merged")
        self.assertTrue(merged["text"].startswith('awesome-agent-boards: PR #7 from @octocat merged: "Add a board"'))

    def test_refuses_unexpected_fields(self):
        for env in ({"PR_NUMBER": "7; id"}, {"PR_AUTHOR": "a b"}, {"PR_URL": "https://evil.test/pull/7"}, {"PR_ACTION": "edited"}):
            self.assertNotEqual(self.dry_run(**env).returncode, 0, env)


if __name__ == "__main__":
    unittest.main()
