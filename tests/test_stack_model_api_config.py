"""STACK-02 AC01/AC02/AC05 static, daemon-free preimplementation evidence.

The optional third layer is intentionally not supplied by these tests. Missing
configuration is an explicit failure, separate from the existing-layer preflight.
No image build, service startup, model loading, prediction, or refresh is tested.
"""

import ast
import importlib.util
import json
import math
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MLFLOW = ROOT.parent / "invoiceops-mlflow"
MODEL_API = ROOT.parent / "invoiceops-model-api"
OVERLAY = ROOT / "compose.model-api.yml"
BASE_SERVICES = {"postgres", "minio", "minio-init", "mlflow"}
BASE_ENV = {
    "STACK_POSTGRES_PASSWORD": "Synthetic_Db-Password_0123456789abcd",
    "STACK_MINIO_ROOT_USER": "SyntheticStackUser",
    "STACK_MINIO_ROOT_PASSWORD": "Synthetic_S3-Password_0123456789abcd",
    "MLFLOW_AUTH_ADMIN_USERNAME": "synthetic-admin",
    "MLFLOW_AUTH_ADMIN_PASSWORD": "Synthetic_Admin-Password_0123456789",
    "MLFLOW_FLASK_SERVER_SECRET_KEY": "Synthetic_Flask-Secret_0123456789ab",
}
MODEL_ENV = {
    "STACK_MODEL_API_MLFLOW_USERNAME": "synthetic-model-reader",
    "STACK_MODEL_API_MLFLOW_PASSWORD": "Synthetic_Reader-Password_0123456789",
    "MODEL_API_ADMIN_TOKEN": "Synthetic_Model-Admin-Token_0123456789",
}
SYNTHETIC_ENV = {**BASE_ENV, **MODEL_ENV}
spec = importlib.util.spec_from_file_location("stack02_cli", ROOT / "scripts/stack_model_api_smoke.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def validate_model_readiness_probe(command):
    """Recognize a bounded Python urllib probe without executing its source.

    Supported form: one urllib.request import followed by an unconditional
    urlopen(literal_model_url, timeout=positive_number), optionally read/close.
    Uncaught HTTP/transport exceptions give Python a nonzero exit. Additional
    control flow, exception handling, exit overrides, or shell suffixes are not
    accepted. This is a static recognizer, not a general Python/shell verifier;
    other legitimate probe forms require a separately reviewed test extension.
    """
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ValueError("Readiness command must be a string list")
    if command and command[0] == "CMD":
        argv = command[1:]
    elif len(command) == 2 and command[0] == "CMD-SHELL":
        try:
            argv = shlex.split(command[1])
        except ValueError:
            raise ValueError("Malformed readiness shell quoting") from None
        # Canonical shell quoting prevents expansions hidden inside a Python
        # comment/string from changing what the shell actually executes.
        if command[1] != shlex.join(argv):
            raise ValueError("Readiness shell command requires canonical safe quoting")
    else:
        raise ValueError("Unsupported readiness command form")
    if len(argv) != 3 or argv[0] not in ("python", "python3") or argv[1] != "-c":
        raise ValueError("Readiness must directly execute a Python -c probe")
    try:
        body = ast.parse(argv[2]).body
    except SyntaxError:
        raise ValueError("Invalid readiness Python syntax") from None
    if len(body) != 2 or not isinstance(body[1], ast.Expr):
        raise ValueError("Readiness requires an import and an unconditional request")
    imported = body[0]
    if isinstance(imported, ast.Import) and len(imported.names) == 1:
        name = imported.names[0]
        if name.name != "urllib.request":
            raise ValueError("Unsupported readiness import")
        request_function = f"{name.asname}.urlopen" if name.asname else "urllib.request.urlopen"
    elif (
        isinstance(imported, ast.ImportFrom) and imported.module == "urllib.request"
        and imported.level == 0 and len(imported.names) == 1
        and imported.names[0].name == "urlopen"
    ):
        request_function = imported.names[0].asname or "urlopen"
    else:
        raise ValueError("Readiness must import urllib.request.urlopen")
    request = body[1].value
    if (
        isinstance(request, ast.Call) and isinstance(request.func, ast.Attribute)
        and request.func.attr in ("read", "close")
        and not request.args and not request.keywords
    ):
        request = request.func.value
    expected_function = ast.parse(request_function, mode="eval").body
    if (
        not isinstance(request, ast.Call)
        or ast.dump(request.func) != ast.dump(expected_function)
        or len(request.args) != 1 or not isinstance(request.args[0], ast.Constant)
        or request.args[0].value not in (
            "http://127.0.0.1:8000/models/invoice-review/health",
            "http://localhost:8000/models/invoice-review/health",
        )
    ):
        raise ValueError("Readiness must call urlopen on the model health endpoint")
    if len(request.keywords) != 1 or request.keywords[0].arg != "timeout":
        raise ValueError("Readiness request must have a bounded timeout")
    timeout = request.keywords[0].value
    if (
        not isinstance(timeout, ast.Constant) or type(timeout.value) not in (int, float)
        or not math.isfinite(timeout.value) or timeout.value <= 0
    ):
        raise ValueError("Readiness timeout must be a finite positive literal")


class StackModelApiConfigTests(unittest.TestCase):
    def compose(self, *, overlay=True, values=None, project="invoiceops-stack"):
        if overlay:
            self.assertTrue(
                OVERLAY.is_file(),
                "STACK-02 preimplementation: compose.model-api.yml is absent; "
                "the optional third layer must be implemented in a separately "
                "approved production phase before its rendered contract can pass.",
            )
        supplied = SYNTHETIC_ENV if values is None else values
        allowed = set(SYNTHETIC_ENV) | {"STACK_MODEL_API_PORT", "STACK_MLFLOW_PORT"}
        self.assertFalse(set(supplied) - allowed, "Non-allowlisted fixture key")
        try:
            executable = cli.trusted_executable(os.environ.get("STACK_MODEL_API_COMPOSE_BINARY"), cli.COMPOSE)
        except cli.Failure:
            self.fail("BLOCKED_ENVIRONMENT: trusted Compose executable unavailable or invalid")
        command = [
            executable, "--host", cli.LOCAL, "-p", project,
            "--project-directory", str(MLFLOW), "--env-file", "/dev/null",
            "-f", str(MLFLOW / "compose.yml"), "-f", str(ROOT / "compose.yml"),
        ]
        if overlay:
            command.extend(["-f", str(OVERLAY)])
        command.extend(["config", "--format", "json"])
        try:
            with tempfile.TemporaryDirectory(prefix="stack02-config-") as directory:
                environment = cli.isolated_cli_environment(directory, executable)
                environment.update(supplied)
                return subprocess.run(
                    command, cwd=ROOT, env=environment, capture_output=True,
                    text=True, check=False, timeout=30,
                )
        except (OSError, subprocess.TimeoutExpired):
            self.fail(
                "ENVIRONMENT: Docker Compose config unavailable or timed out "
                "after 30s; subprocess output suppressed"
            )

    def config(self, **kwargs):
        result = self.compose(**kwargs)
        self.assertEqual(
            result.returncode, 0,
            f"ENVIRONMENT/CONFIG: Compose config failed (exit {result.returncode}); "
            "check CLI compatibility and file contracts; output suppressed",
        )
        try:
            config = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("ENVIRONMENT/CONFIG: Compose returned invalid JSON; output suppressed")
        self.assertTrue(isinstance(config, dict), "Compose did not return a config object")
        return config

    def test_existing_two_layer_config_preflight_without_model_api_inputs(self):
        """AC01: CLI/config availability is independent of the missing layer."""
        config = self.config(overlay=False, values=BASE_ENV)
        self.assertEqual(set(config["services"]), BASE_SERVICES)
        published = {
            name for name, service in config["services"].items() if service.get("ports")
        }
        self.assertEqual(published, {"mlflow"})

    def test_optional_layer_adds_model_api_and_only_local_mlflow_health_grace(self):
        """AC01: only MLflow's explicitly approved startup grace may differ."""
        merged = self.config()
        baseline = self.config(overlay=False, values=BASE_ENV)
        self.assertEqual(set(merged["services"]), BASE_SERVICES | {"model-api"})
        for name in sorted(BASE_SERVICES):
            with self.subTest(service=name):
                expected = dict(baseline["services"][name])
                if name == "mlflow":
                    expected["healthcheck"] = dict(expected["healthcheck"], start_period="5m30s")
                self.assertTrue(
                    merged["services"][name] == expected,
                    "ML service changed beyond approved grace; rendered configuration suppressed",
                )
        build = merged["services"]["model-api"]["build"]
        self.assertEqual(Path(build["context"]).resolve(), MODEL_API.resolve())
        dockerfile = Path(build.get("dockerfile", "Dockerfile"))
        self.assertEqual((MODEL_API / dockerfile).resolve(), MODEL_API / "Dockerfile")

    def test_mlflow_grace_is_local_and_keeps_current_command_and_probes(self):
        baseline = self.config(overlay=False, values=BASE_ENV)["services"]["mlflow"]
        merged = self.config()["services"]["mlflow"]
        self.assertEqual(baseline["healthcheck"]["start_period"], "10s")
        self.assertEqual(merged["healthcheck"], {
            **baseline["healthcheck"], "start_period": "5m30s",
        })
        self.assertEqual(baseline["command"], [
            "mlflow", "server", "--app-name=basic-auth", "--enable-workspaces",
        ])
        self.assertEqual(merged["command"], baseline["command"])
        self.assertEqual(merged["environment"], baseline["environment"])

    def test_project_scoped_networks_and_volumes_have_no_new_global_resources(self):
        """AC01: neither project gains external networks, fixed names, or volumes."""
        for project in ("invoiceops-stack", "invoiceops-stack-model-isolation-test"):
            with self.subTest(project=project):
                config = self.config(project=project)
                self.assertEqual(config["name"], project)
                self.assertEqual(set(config["networks"]), {"default"})
                self.assertEqual(set(config["volumes"]), {"postgres_data", "minio_data"})
                for kind in ("networks", "volumes"):
                    for name, resource in config[kind].items():
                        self.assertEqual(resource["name"], f"{project}_{name}")
                        self.assertFalse(resource.get("external"))
                for service in config["services"].values():
                    self.assertEqual(service["networks"], {"default": None})
                    self.assertNotIn("container_name", service)
                    self.assertNotIn("network_mode", service)
                self.assertFalse(config["services"]["model-api"].get("volumes"))

    def test_model_api_receives_internal_tracking_and_dedicated_credentials_only(self):
        """AC02: the API receives reader credentials, not infrastructure secrets."""
        service = self.config()["services"]["model-api"]
        environment = service["environment"]
        for key, value in {
            "MLFLOW_TRACKING_URI": "http://mlflow:5000",
            "MLFLOW_TRACKING_USERNAME": MODEL_ENV["STACK_MODEL_API_MLFLOW_USERNAME"],
            "MLFLOW_TRACKING_PASSWORD": MODEL_ENV["STACK_MODEL_API_MLFLOW_PASSWORD"],
            "MODEL_API_ADMIN_TOKEN": MODEL_ENV["MODEL_API_ADMIN_TOKEN"],
            "MODEL_REFRESH_TIMEOUT_SECONDS": "120",
            "MODEL_STARTUP_TIMEOUT_SECONDS": "180",
        }.items():
            self.assertEqual(environment.get(key), value)
        self.assertEqual(len(set(MODEL_ENV.values())), len(MODEL_ENV))
        forbidden_prefixes = ("POSTGRES_", "MINIO_", "AWS_", "STACK_", "MLFLOW_AUTH_")
        for key in environment:
            self.assertFalse(key.startswith(forbidden_prefixes), "Infrastructure credential key exposed")
        for key in ("MLFLOW_BACKEND_STORE_URI", "MLFLOW_AUTH_DATABASE_URI"):
            self.assertNotIn(key, environment)
        serialized = json.dumps(service)
        for value in BASE_ENV.values():
            self.assertFalse(value in serialized, "Infrastructure secret exposed to ModelAPI")

    def test_tracking_host_protection_accepts_exact_internal_and_loopback_hosts(self):
        for port in ("5000", "18574"):
            services = self.config(values={**SYNTHETIC_ENV, "STACK_MLFLOW_PORT": port})["services"]
            hosts = services["mlflow"]["environment"].get("MLFLOW_SERVER_ALLOWED_HOSTS", "")
            self.assertEqual(set(hosts.split(",")),
                             {"mlflow:5000", "127.0.0.1:5000", f"127.0.0.1:{port}"})
            self.assertNotIn("*", hosts)

    def test_model_api_port_is_loopback_only_with_default_and_custom_publication(self):
        """AC02: publish target 8000 only on loopback; leave MLflow unchanged."""
        for port in (None, "15800"):
            with self.subTest(port=port):
                values = dict(SYNTHETIC_ENV)
                if port is not None:
                    values["STACK_MODEL_API_PORT"] = port
                services = self.config(values=values)["services"]
                ports = services["model-api"]["ports"]
                self.assertEqual(len(ports), 1)
                self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
                self.assertEqual(ports[0]["published"], port or "5800")
                self.assertEqual(ports[0]["target"], 8000)
                self.assertEqual(ports[0]["protocol"], "tcp")
                self.assertEqual(
                    {name for name, service in services.items() if service.get("ports")},
                    {"mlflow", "model-api"},
                )

    def test_required_model_api_values_reject_missing_and_empty_inputs(self):
        """AC02/AC05: each new required input must fail interpolation closed."""
        for key in MODEL_ENV:
            for empty in (False, True):
                with self.subTest(variable=key, empty=empty):
                    values = dict(SYNTHETIC_ENV)
                    if empty:
                        values[key] = ""
                    else:
                        del values[key]
                    result = self.compose(values=values)
                    self.assertNotEqual(result.returncode, 0, "Required input was accepted")
                    self.assertTrue(key in result.stderr, "Required input diagnostic absent")
                    self.assertFalse(bool(result.stdout.strip()), "Failed config exposed partial output")

    def test_non_numeric_model_api_port_is_rejected(self):
        """AC02: a malformed host-port override must not render successfully."""
        values = dict(SYNTHETIC_ENV, STACK_MODEL_API_PORT="not-a-port")
        result = self.compose(values=values)
        self.assertNotEqual(result.returncode, 0, "Invalid ModelAPI port was accepted")
        self.assertFalse(bool(result.stdout.strip()), "Failed config exposed partial output")

    def test_readiness_targets_invoice_review_model_not_process_liveness(self):
        """AC05: inspect the declared probe, not runtime model availability."""
        healthcheck = self.config()["services"]["model-api"]["healthcheck"]
        self.assertFalse(healthcheck.get("disable"))
        validate_model_readiness_probe(healthcheck["test"])

    def test_readiness_validator_accepts_bounded_request_fixtures_without_execution(self):
        """Synthetic validator evidence only; not overlay rendering or HTTP proof."""
        url = "http://127.0.0.1:8000/models/invoice-review/health"
        scripts = (
            f"import urllib.request; urllib.request.urlopen({url!r}, timeout=2)",
            f"import urllib.request as req; req.urlopen({url!r}, timeout=1.5).read()",
            f"from urllib.request import urlopen as get; get({url!r}, timeout=3).close()",
        )
        for script in scripts:
            with self.subTest(script=script):
                validate_model_readiness_probe(["CMD", "python", "-c", script])
                validate_model_readiness_probe([
                    "CMD-SHELL", f"python3 -c {shlex.quote(script)}",
                ])

    def test_readiness_validator_rejects_inert_and_failure_masking_fixtures(self):
        """Parse fixtures only: never run a request or arbitrary probe source."""
        url = "http://127.0.0.1:8000/models/invoice-review/health"
        probe = f"import urllib.request; urllib.request.urlopen({url!r}, timeout=2)"
        scripts = (
            f"print({url!r}, 'urllib.request')",
            f"import urllib.request; {url!r}",
            f"import urllib.request; lambda: urllib.request.urlopen({url!r}, timeout=2)",
            f"import urllib.request\nif False: urllib.request.urlopen({url!r}, timeout=2)",
            f"import urllib.request\ntry: urllib.request.urlopen({url!r}, timeout=2)\nexcept: pass",
            probe + "; raise SystemExit(0)",
            probe.replace("/models/invoice-review/health", "/health"),
            probe.replace("timeout=2", "timeout=0"),
            probe.replace(", timeout=2", ""),
            probe.replace("urllib.request.urlopen(", "print("),
            "not valid Python!",
        )
        for index, script in enumerate(scripts):
            with self.subTest(fixture=index):
                with self.assertRaises(ValueError):
                    validate_model_readiness_probe(["CMD", "python", "-c", script])
        for command in (
            ["CMD-SHELL", f"echo {shlex.quote(url)} urllib.request"],
            ["CMD-SHELL", f"python -c {shlex.quote(probe)} || true"],
            ["CMD-SHELL", f"python -c {shlex.quote(probe)}; exit 0"],
            ["CMD-SHELL", f'python -c "{probe} # $(echo inert)"'],
            ["CMD", "echo", url, "urllib.request"],
            ["NONE"],
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_model_readiness_probe(command)


if __name__ == "__main__":
    unittest.main()
