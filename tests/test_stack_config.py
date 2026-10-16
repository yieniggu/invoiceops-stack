import json
import os
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / "invoiceops-mlflow"
SYNTHETIC_ENV = {
    "STACK_POSTGRES_PASSWORD": "Synthetic_Db-Password_0123456789abcd",
    "STACK_MINIO_ROOT_USER": "SyntheticStackUser",
    "STACK_MINIO_ROOT_PASSWORD": "Synthetic_S3-Password_0123456789abcd",
    "MLFLOW_AUTH_ADMIN_USERNAME": "synthetic-admin",
    "MLFLOW_AUTH_ADMIN_PASSWORD": "Synthetic_Admin-Password_0123456789",
    "MLFLOW_FLASK_SERVER_SECRET_KEY": "Synthetic_Flask-Secret_0123456789ab",
}


class StackConfigTests(unittest.TestCase):
    @classmethod
    def compose(cls, *, base_only=False, project="invoiceops-stack", values=None):
        environment = {"PATH": os.environ.get("PATH", os.defpath)}
        environment.update(SYNTHETIC_ENV if values is None else values)
        command = [
            "docker", "compose", "-p", project,
            "--project-directory", str(BASE), "--env-file", "/dev/null",
            "-f", str(BASE / "compose.yml"),
        ]
        if not base_only:
            command.extend(["-f", str(ROOT / "compose.yml")])
        command.extend(["config", "--format", "json"])
        try:
            return subprocess.run(
                command, cwd=ROOT, env=environment, capture_output=True,
                text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AssertionError(
                "Docker Compose config unavailable or timed out; output suppressed"
            ) from None

    @classmethod
    def config(cls, **kwargs):
        result = cls.compose(**kwargs)
        if result.returncode != 0:
            raise AssertionError(
                f"Docker Compose config failed (exit {result.returncode}); "
                "check CLI compatibility and file contracts; output suppressed"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise AssertionError("Compose returned invalid JSON; output suppressed") from None

    @classmethod
    def setUpClass(cls):
        cls.base = cls.config(base_only=True)
        cls.merged = cls.config()

    def test_base_services_build_and_images_are_reused(self):
        services = self.merged["services"]
        self.assertEqual(set(services), {"postgres", "minio", "minio-init", "mlflow"})
        self.assertEqual(set(services), set(self.base["services"]))
        for name, service in services.items():
            with self.subTest(service=name):
                original = self.base["services"][name]
                for key in ("image", "build", "command", "entrypoint", "depends_on",
                            "healthcheck", "restart", "volumes"):
                    self.assertEqual(service.get(key), original.get(key))
        build = services["mlflow"]["build"]
        self.assertEqual(Path(build["context"]), BASE)
        self.assertEqual(build["dockerfile"], "Dockerfile.mlflow")

    def test_only_mlflow_has_one_loopback_port_default_and_custom(self):
        for port in (None, "15432"):
            with self.subTest(port=port):
                values = dict(SYNTHETIC_ENV)
                if port is not None:
                    values["STACK_MLFLOW_PORT"] = port
                config = self.merged if port is None else self.config(values=values)
                for name, service in config["services"].items():
                    if name != "mlflow":
                        self.assertFalse(service.get("ports"))
                        continue
                    ports = service["ports"]
                    self.assertEqual(len(ports), 1)
                    self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
                    self.assertEqual(ports[0]["published"], port or "5000")
                    self.assertEqual(ports[0]["target"], 5000)
                    self.assertEqual(ports[0]["protocol"], "tcp")

    def test_network_and_persistent_volumes_belong_to_each_project(self):
        for project in ("invoiceops-stack", "invoiceops-stack-isolation-test"):
            with self.subTest(project=project):
                config = self.merged if project == "invoiceops-stack" else self.config(project=project)
                self.assertEqual(config["name"], project)
                self.assertEqual(set(config["networks"]), {"default"})
                self.assertEqual(set(config["volumes"]), {"postgres_data", "minio_data"})
                for kind in ("networks", "volumes"):
                    for name, resource in config[kind].items():
                        self.assertEqual(resource["name"], f"{project}_{name}")
                        self.assertFalse(resource.get("external"))
                for service in config["services"].values():
                    self.assertEqual(service["networks"], {"default": None})
                    self.assertFalse("container_name" in service, "Fixed container name found")
                    self.assertFalse("network_mode" in service, "Network mode override found")
                for name, target in (("postgres", "/var/lib/postgresql/data"), ("minio", "/data")):
                    mounts = config["services"][name]["volumes"]
                    self.assertEqual(len(mounts), 1)
                    self.assertEqual(mounts[0]["type"], "volume")
                    self.assertEqual(mounts[0]["source"], f"{name}_data")
                    self.assertEqual(mounts[0]["target"], target)

    def test_all_internal_credentials_are_replaced_consistently(self):
        services = self.merged["services"]
        postgres = services["postgres"]["environment"]
        self.assertEqual(postgres, {
            "POSTGRES_DB": "mlflow", "POSTGRES_USER": "mlflow",
            "POSTGRES_PASSWORD": SYNTHETIC_ENV["STACK_POSTGRES_PASSWORD"],
        })
        for name in ("minio", "minio-init"):
            environment = services[name]["environment"]
            self.assertEqual(environment["MINIO_ROOT_USER"], SYNTHETIC_ENV["STACK_MINIO_ROOT_USER"])
            self.assertEqual(environment["MINIO_ROOT_PASSWORD"], SYNTHETIC_ENV["STACK_MINIO_ROOT_PASSWORD"])
        environment = services["mlflow"]["environment"]
        self.assertEqual(environment["AWS_ACCESS_KEY_ID"], SYNTHETIC_ENV["STACK_MINIO_ROOT_USER"])
        self.assertEqual(environment["AWS_SECRET_ACCESS_KEY"], SYNTHETIC_ENV["STACK_MINIO_ROOT_PASSWORD"])
        self.assertEqual(environment["MLFLOW_AUTH_DATABASE_URI"], environment["MLFLOW_BACKEND_STORE_URI"])
        self.assertRegex(SYNTHETIC_ENV["STACK_POSTGRES_PASSWORD"], r"^[A-Za-z0-9_-]{32,}$")
        for key in ("MLFLOW_AUTH_DATABASE_URI", "MLFLOW_BACKEND_STORE_URI"):
            parsed = urlsplit(environment[key])
            self.assertEqual(parsed.scheme, "postgresql+psycopg2")
            self.assertEqual(parsed.username, "mlflow")
            self.assertEqual(parsed.password, SYNTHETIC_ENV["STACK_POSTGRES_PASSWORD"])
            self.assertEqual((parsed.hostname, parsed.port, parsed.path), ("postgres", 5432, "/mlflow"))
            self.assertFalse(parsed.query or parsed.fragment)
        serialized = json.dumps(self.merged)
        for default in ("mlflow-local-password", "minio-local-password"):
            self.assertFalse(default in serialized, "Base credential default remains in merged config")

    def test_required_variables_fail_closed_when_missing_or_empty(self):
        for key in SYNTHETIC_ENV:
            for empty in (False, True):
                with self.subTest(variable=key, empty=empty):
                    values = dict(SYNTHETIC_ENV)
                    if empty:
                        values[key] = ""
                    else:
                        del values[key]
                    result = self.compose(values=values)
                    self.assertNotEqual(result.returncode, 0, "Required variable was accepted")
                    self.assertTrue(key in result.stderr, "Expected variable diagnostic missing")
                    self.assertFalse(bool(result.stdout.strip()), "Failed config exposed partial output")

    def test_non_numeric_port_is_rejected(self):
        values = dict(SYNTHETIC_ENV, STACK_MLFLOW_PORT="not-a-port")
        result = self.compose(values=values)
        self.assertNotEqual(result.returncode, 0, "Invalid port was accepted")
        self.assertFalse(bool(result.stdout.strip()), "Failed config exposed partial output")

    def test_auth_workspaces_entrypoint_and_transport_are_preserved(self):
        service = self.merged["services"]["mlflow"]
        self.assertEqual(service["command"], [
            "mlflow", "server", "--app-name=basic-auth", "--enable-workspaces",
        ])
        self.assertIsNone(service.get("entrypoint"))
        environment = service["environment"]
        for key in ("MLFLOW_AUTH_ADMIN_USERNAME", "MLFLOW_AUTH_ADMIN_PASSWORD",
                    "MLFLOW_FLASK_SERVER_SECRET_KEY"):
            self.assertEqual(environment[key], SYNTHETIC_ENV[key])
        for key, value in {
            "MLFLOW_AUTH_CONFIG_PATH": "/run/mlflow-auth/auth.ini",
            "MLFLOW_HOST": "0.0.0.0", "MLFLOW_PORT": "5000",
            "MLFLOW_S3_ENDPOINT_URL": "http://minio:9000",
            "MLFLOW_ARTIFACTS_DESTINATION": "s3://mlflow-artifacts",
        }.items():
            self.assertEqual(environment[key], value)
        dockerfile = (BASE / "Dockerfile.mlflow").read_text(encoding="utf-8")
        self.assertIn('ENTRYPOINT ["/usr/local/bin/mlflow-entrypoint"]', dockerfile)
        entrypoint = (BASE / "docker/mlflow-entrypoint.sh").read_text(encoding="utf-8")
        for guard in ("umask 077", "default_permission = READ",
                      "grant_default_workspace_access = false"):
            self.assertIn(guard, entrypoint)


if __name__ == "__main__":
    unittest.main()
