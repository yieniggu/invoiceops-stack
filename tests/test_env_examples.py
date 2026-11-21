"""No throwaway secret of local.example.env may appear in any other committed env example.

`local.example.env` is the only env file allowed to carry real-looking (throwaway) secrets. The
scan is read-only and covers every repository next to this one (tracked files plus untracked files
that are not ignored, i.e. what would be committed). Failures name the file and the variable,
never the value.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import LOCAL_ENV, ROOT, V2, RequiresStackPrerequisites, parse_env

SECRET_KEY = re.compile(r"(PASSWORD|SECRET|TOKEN|SEED|_KEY)")
MIN_LENGTH = 16
ENV_EXAMPLE = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.env|[^/]*\.env\.[^/]+|[^/]*example[^/]*\.env)$")


def throwaway_secrets():
    return {
        key: value
        for key, value in parse_env(LOCAL_ENV).items()
        if SECRET_KEY.search(key) and len(value) >= MIN_LENGTH
    }


def committed_env_examples():
    for repo in sorted(V2.glob("invoiceops-*")):
        if not (repo / ".git").exists():
            continue
        listing = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard"],
            text=True, capture_output=True, check=False,
        )  # fmt: skip
        for relative in listing.stdout.splitlines():
            path = repo / relative
            if path == LOCAL_ENV:
                continue
            if ENV_EXAMPLE.search(relative) and "example" in path.name and path.is_file():
                yield path


def find_leaks(paths, secrets):
    leaks = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        leaks += [f"{path.name}: {key}" for key, value in secrets.items() if value in text]
    return leaks


class EnvExampleSecretsTests(RequiresStackPrerequisites):
    def test_local_example_env_actually_has_secrets_to_protect(self):
        self.assertGreaterEqual(len(throwaway_secrets()), 8)

    def test_no_throwaway_secret_leaks_into_another_committed_env_example(self):
        secrets = throwaway_secrets()
        scanned = list(committed_env_examples())
        names = {path.name for path in scanned}
        self.assertIn("production.env.example", names, "the app's production template must be scanned")
        self.assertEqual(find_leaks(scanned, secrets), [], "values of local.example.env found in other env examples")

    def test_the_scan_detects_a_leak(self):
        """Control: a file that repeats one throwaway value is caught by the same rule."""
        secrets = throwaway_secrets()
        key, value = next(iter(secrets.items()))
        with tempfile.TemporaryDirectory() as tmp:
            leaky = Path(tmp) / "production.env.example"
            leaky.write_text(f"OTHER=1\n{key}={value}\n", encoding="utf-8")
            clean = Path(tmp) / "clean.env.example"
            clean.write_text(f"{key}=\n", encoding="utf-8")
            self.assertEqual(find_leaks([leaky, clean], secrets), [f"production.env.example: {key}"])

    def test_local_example_env_documents_the_scanner_allowlist_and_the_right_template_path(self):
        header = "\n".join(LOCAL_ENV.read_text(encoding="utf-8").splitlines()[:12])
        self.assertIn("invoiceops-app/ops/production.env.example", header)
        self.assertRegex(header, r"(?i)allowlist")
        self.assertTrue((V2 / "invoiceops-app" / "ops" / "production.env.example").is_file())
        self.assertTrue(ROOT.name == "invoiceops-stack")


if __name__ == "__main__":
    unittest.main()
