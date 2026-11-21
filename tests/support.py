"""Shared helpers of the stack tests: sibling repositories, docker and `docker compose config`."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT.parent
SIBLINGS = {
    "INVOICEOPS_APP_DIR": V2 / "invoiceops-app",
    "INVOICEOPS_MLFLOW_DIR": V2 / "invoiceops-mlflow",
    "INVOICEOPS_MODEL_API_DIR": V2 / "invoiceops-model-api",
}
# `invoiceops-ml` is the Model API build context ("ml") and the notebooks' package.
ALL_SIBLINGS = {**SIBLINGS, "INVOICEOPS_ML_CONTEXT": V2 / "invoiceops-ml"}
LOCAL_ENV = ROOT / "local.example.env"
# dev/tickets is a plain folder next to the repositories, not a repository: never required.
TICKETS = V2 / "dev" / "tickets"


def in_ci() -> bool:
    return bool(os.environ.get("CI"))


def missing_prerequisites() -> list[str]:
    missing = [f"{path} (sibling repository)" for path in ALL_SIBLINGS.values() if not path.is_dir()]
    if not shutil.which("docker"):
        missing.append("docker CLI")
    return missing


class RequiresStackPrerequisites(unittest.TestCase):
    """Skip locally, FAIL in CI, when sibling repositories or docker are missing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        missing = missing_prerequisites()
        if missing:
            message = "missing prerequisites: " + ", ".join(missing)
            if in_ci():
                raise AssertionError(message + " (CI must check out the siblings, see .github/workflows/ci.yml)")
            raise unittest.SkipTest(message)


def parse_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def compose(cwd, env_file, *args, files=("compose.yml",), extra_env=None, project=None):
    command = ["docker", "compose"]
    if project:
        command += ["-p", project]
    for name in files:
        command += ["-f", str(cwd / name)]
    command += ["--env-file", str(env_file), *args]
    env = {k: v for k, v in os.environ.items() if k not in parse_env(env_file) and not k.startswith("INVOICEOPS_")}
    env.update(extra_env or {})
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
