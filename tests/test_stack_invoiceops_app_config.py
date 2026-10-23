"""STACK-03 S1 static Compose contract; it never contacts a Docker daemon."""

import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MLFLOW = ROOT.parent / "invoiceops-mlflow"
APP = ROOT.parent / "invoiceops-app"
MODEL_API_OVERLAY = ROOT / "compose.model-api.yml"
APP_OVERLAY = ROOT / "compose.invoiceops-app.yml"

BASE_ENV = {
    "STACK_POSTGRES_PASSWORD": "Synthetic_Db-Password_0123456789abcd",
    "STACK_MINIO_ROOT_USER": "SyntheticStackUser",
    "STACK_MINIO_ROOT_PASSWORD": "Synthetic_S3-Password_0123456789abcd",
    "MLFLOW_AUTH_ADMIN_USERNAME": "synthetic-admin",
    "MLFLOW_AUTH_ADMIN_PASSWORD": "Synthetic_Admin-Password_0123456789",
    "MLFLOW_FLASK_SERVER_SECRET_KEY": "Synthetic_Flask-Secret_0123456789ab",
    "STACK_MODEL_API_MLFLOW_USERNAME": "synthetic-model-reader",
    "STACK_MODEL_API_MLFLOW_PASSWORD": "Synthetic_Reader-Password_0123456789",
    "MODEL_API_ADMIN_TOKEN": "Synthetic_Model-Admin-Token_0123456789",
}
APP_CONNECTION_ENV = {
    "MODEL_API_URL": "http://model-api:8000",
    "MODEL_API_MODEL_ID": "invoice-review",
    "STACK_APP_POSTGRES_PASSWORD": "Synthetic_App-Db-Password_0123456789",
}
SYNTHETIC_ENV = {**BASE_ENV, **APP_CONNECTION_ENV}


class StackInvoiceopsAppConfigTests(unittest.TestCase):
    def compose(self, values=None):
        self.assertTrue(APP_OVERLAY.is_file(), "STACK-03 S1 App overlay is absent")
        supplied = SYNTHETIC_ENV if values is None else values
        allowed = set(SYNTHETIC_ENV) | {
            "STACK_APP_BACKEND_PORT",
            "STACK_APP_FRONTEND_PORT",
            "STACK_APP_TEST_DB_PORT",
            "STACK_MODEL_API_PORT",
            "STACK_MLFLOW_PORT",
        }
        self.assertFalse(set(supplied) - allowed, "Non-allowlisted fixture key")
        command = [
            "docker", "compose", "-p", "invoiceops-stack-app-config-test",
            "--project-directory", str(MLFLOW), "--env-file", "/dev/null", "--profile", "test",
            "-f", str(MLFLOW / "compose.yml"), "-f", str(ROOT / "compose.yml"),
            "-f", str(MODEL_API_OVERLAY), "-f", str(APP / "docker-compose.yml"),
            "-f", str(APP_OVERLAY), "config", "--format", "json",
        ]
        environment = {"PATH": os.environ.get("PATH", os.defpath)}
        environment.update(supplied)
        try:
            return subprocess.run(
                command, cwd=ROOT, env=environment, capture_output=True,
                text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.fail("BLOCKED_ENVIRONMENT: Docker Compose config unavailable or timed out")

    def config(self):
        result = self.compose()
        self.assertEqual(
            result.returncode, 0,
            "ENVIRONMENT/CONFIG: Compose config failed; output suppressed",
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("ENVIRONMENT/CONFIG: Compose returned invalid JSON; output suppressed")

    def test_app_layer_uses_model_api_connection_only_and_loopback_ports(self):
        config = self.config()
        services = config["services"]
        self.assertTrue({"db", "backend", "frontend", "model-api"} <= set(services))

        backend = services["backend"]
        frontend = services["frontend"]
        for service, dockerfile in (
            (backend, "apps/backend/Dockerfile"),
            (frontend, "apps/frontend/Dockerfile"),
        ):
            with self.subTest(dockerfile=dockerfile):
                self.assertEqual(service["build"]["context"], str(APP))
                self.assertEqual(service["build"]["dockerfile"], dockerfile)
        self.assertEqual(
            backend["environment"].get("MODEL_API_URL"), APP_CONNECTION_ENV["MODEL_API_URL"]
        )
        self.assertEqual(
            backend["environment"].get("MODEL_API_MODEL_ID"),
            APP_CONNECTION_ENV["MODEL_API_MODEL_ID"],
        )
        self.assertEqual(
            services["db"]["environment"].get("POSTGRES_PASSWORD"),
            APP_CONNECTION_ENV["STACK_APP_POSTGRES_PASSWORD"],
        )
        self.assertEqual(backend["depends_on"]["model-api"]["condition"], "service_healthy")
        self.assertEqual(
            services["db"]["volumes"],
            [{"type": "volume", "source": "app_postgres_data", "target": "/var/lib/postgresql/data", "volume": {}}],
            "App and MLflow PostgreSQL must never share a volume",
        )
        self.assertNotEqual(services["db"]["volumes"], services["postgres"]["volumes"])
        for key in ("MLFLOW_READ_URL", "MLFLOW_UI_URL", "MLFLOW_READ_USERNAME", "MLFLOW_READ_PASSWORD"):
            self.assertEqual(backend["environment"].get(key), "", "App must not receive MLflow values")
        rendered = json.dumps(backend)
        for value in BASE_ENV.values():
            self.assertNotIn(value, rendered, "Model API or infrastructure value exposed to App")

        expected_ports = {"db-test": 5432, "backend": 3000, "frontend": 80}
        for name, target in expected_ports.items():
            with self.subTest(service=name):
                ports = services[name]["ports"]
                self.assertEqual(len(ports), 1)
                self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
                self.assertEqual(ports[0]["target"], target)

    def test_bootstrap_escapes_runtime_config_variable_from_compose_interpolation(self):
        overlay = APP_OVERLAY.read_text(encoding="utf-8")
        self.assertEqual(overlay.count("$$config"), 3)
        self.assertNotIn('"$config"', overlay)

    def test_missing_or_empty_app_connection_variables_fail_closed(self):
        for key in ("MODEL_API_URL", "MODEL_API_MODEL_ID"):
            for empty in (False, True):
                with self.subTest(variable=key, empty=empty):
                    values = dict(SYNTHETIC_ENV)
                    if empty:
                        values[key] = ""
                    else:
                        del values[key]
                    result = self.compose(values)
                    self.assertNotEqual(result.returncode, 0, "Required input was accepted")
                    self.assertIn(key, result.stderr, "Required-variable diagnostic absent")
                    self.assertFalse(result.stdout.strip(), "Failed config exposed partial output")


if __name__ == "__main__":
    unittest.main()
