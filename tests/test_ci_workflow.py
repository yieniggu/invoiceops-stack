"""The sibling repositories that ci.yml checks out must be the real GitHub remotes.

A wrong slug (a typo such as a missing or doubled letter) only fails after the first push, in the
checkout step, so it is checked here against each sibling's `origin` remote. A sibling without a
git checkout or without an `origin` is skipped.
"""

import re
import subprocess
import unittest

from support import ROOT, V2

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CHECKOUT = re.compile(r"repository:\s*(\S+)\s*\n\s*path:\s*(\S+)")
REMOTE_SLUG = re.compile(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$")


def origin_slug(path):
    result = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    match = REMOTE_SLUG.search(result.stdout.strip())
    return match.group(1) if match else None


class CiWorkflowSlugTests(unittest.TestCase):
    def checkouts(self):
        found = CHECKOUT.findall(WORKFLOW.read_text(encoding="utf-8"))
        self.assertTrue(found, "ci.yml checks out no sibling repository")
        return found

    def test_the_five_siblings_are_checked_out_next_to_the_stack(self):
        self.assertEqual(
            sorted(path for _slug, path in self.checkouts()),
            [
                "invoiceops-app",
                "invoiceops-contracts",
                "invoiceops-ml",
                "invoiceops-mlflow",
                "invoiceops-model-api",
            ],
        )

    def test_every_slug_equals_the_origin_of_the_sibling_it_is_checked_out_as(self):
        for slug, path in self.checkouts():
            with self.subTest(sibling=path):
                actual = origin_slug(V2 / path)
                if actual is None:
                    self.skipTest(f"{path} has no origin remote")
                self.assertEqual(slug, actual)

    def test_origin_slug_parser_handles_ssh_and_https_remotes(self):
        for url in ("git@github.com:yieniggu/invoiceopps-app.git", "https://github.com/yieniggu/invoiceopps-app"):
            self.assertEqual(REMOTE_SLUG.search(url).group(1), "yieniggu/invoiceopps-app")


if __name__ == "__main__":
    unittest.main()


class CiLintStepTests(unittest.TestCase):
    def test_ci_lints_the_tests_and_scripts_with_a_pinned_ruff(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"pip install ruff==\d+\.\d+\.\d+")
        self.assertIn("ruff check tests scripts", workflow)
        self.assertTrue((ROOT / "ruff.toml").is_file(), "ruff.toml pins the lint rules CI uses")
