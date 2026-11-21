"""Contract of the unified stack: compose.yml (production), compose.local.yml and local.example.env.

Only `docker compose config` is used (no daemon, no containers). Secrets are dummy values.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from support import (
    LOCAL_ENV,
    ROOT,
    SIBLINGS,
    V2,
    RequiresStackPrerequisites,
    compose,
    parse_env,
)

# Names that production already uses and the unified project must keep using.
PROD_VOLUME = "invoiceops_postgres_data"
PROD_NETWORKS = {"api": "invoiceops_api", "db": "invoiceops_db", "edge": "aglai_edge"}
SERVICES = {"db", "backend", "web", "postgres", "minio", "minio-init", "mlflow", "gateway", "model-api"}

DUMMY = {
    "POSTGRES_PASSWORD": "dummy-owner-password",
    "POSTGRES_APP_PASSWORD": "dummy-app-password",
    "MLFLOW_AUTH_ADMIN_USERNAME": "svc-mlflow-admin",
    "MLFLOW_AUTH_ADMIN_PASSWORD": "a" * 40,
    "MLFLOW_FLASK_SERVER_SECRET_KEY": "b" * 40,
    "MLFLOW_DB_PASSWORD": "c" * 24,
    "MINIO_ROOT_USER": "minio-dummy",
    "MINIO_ROOT_PASSWORD": "d" * 24,
    "INTERNAL_INTROSPECTION_SECRET": "e" * 40,
    "GATEWAY_PUBLIC_ORIGIN": "https://mlflow-invoiceops.aglai.dev",
    "MLFLOW_PUBLIC_URL": "https://mlflow-invoiceops.aglai.dev",
    "APP_PUBLIC_URL": "https://invoiceops.aglai.dev",
    "MODEL_API_SERVICE_TOKEN": "f" * 40,
    "GATEWAY_SERVICE_READ_TOKEN": "g" * 40,
    "OFFICIAL_VALIDATION_SEED": "h" * 40,
}


class UnifiedStackConfigTests(RequiresStackPrerequisites):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp(prefix="stack-config-"))
        lines = [f"{key}={value}" for key, value in {**DUMMY, **{k: str(v) for k, v in SIBLINGS.items()}}.items()]
        cls.prod_env = cls.tmp / "production.env"
        cls.prod_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cls.prod = cls.config(cls.prod_env)
        cls.local = cls.config(LOCAL_ENV, files=("compose.yml", "compose.local.yml"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def config(cls, env_file, files=("compose.yml",)):
        result = compose(ROOT, env_file, "config", "--format", "json", files=files)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    # -- production ---------------------------------------------------------------------------
    def test_config_quiet_passes_for_production_and_local(self):
        self.assertEqual(compose(ROOT, self.prod_env, "config", "-q").returncode, 0)
        local = compose(ROOT, LOCAL_ENV, "config", "-q", files=("compose.yml", "compose.local.yml"))
        self.assertEqual(local.returncode, 0, local.stderr)

    def test_project_keeps_the_name_of_the_running_app_project(self):
        self.assertEqual(self.prod["name"], "invoiceops")

    def test_every_repository_contributes_its_services(self):
        self.assertEqual(set(self.prod["services"]), SERVICES)

    def test_app_database_volume_resolves_to_the_existing_production_name(self):
        self.assertEqual(self.prod["volumes"]["postgres_data"]["name"], PROD_VOLUME)
        self.assertNotIn("external", self.prod["volumes"]["postgres_data"])
        mounts = {v["source"] for v in self.prod["services"]["db"]["volumes"] if v["type"] == "volume"}
        self.assertEqual(mounts, {"postgres_data"})
        # MLflow data is new in the unified stack: deterministic names, no clash with the app volume.
        self.assertEqual(self.prod["volumes"]["mlflow_postgres_data"]["name"], "invoiceops_mlflow_postgres_data")
        self.assertEqual(self.prod["volumes"]["mlflow_minio_data"]["name"], "invoiceops_mlflow_minio_data")

    def test_networks_keep_their_production_names(self):
        networks = self.prod["networks"]
        for key, name in PROD_NETWORKS.items():
            self.assertEqual(networks[key]["name"], name)
        self.assertTrue(networks["db"]["internal"])
        self.assertTrue(networks["edge"]["external"])  # created by the tunnel host, not by this project
        self.assertNotIn("external", networks["api"])  # created by this project (it already exists)

    def test_names_that_cloudflared_targets_are_unchanged(self):
        services = self.prod["services"]
        self.assertEqual(services["web"]["networks"]["edge"]["aliases"], ["invoiceops-web"])
        self.assertEqual(services["backend"]["networks"]["api"]["aliases"], ["invoiceops-backend"])
        gateway = services["gateway"]["networks"]
        self.assertEqual(gateway["edge"]["aliases"], ["invoiceops-mlflow-gateway"])
        self.assertEqual(gateway["api"]["aliases"], ["invoiceops-mlflow-gateway"])
        self.assertEqual(services["model-api"]["networks"]["api"]["aliases"], ["invoiceops-model-api"])
        # Only web and gateway are reachable from the tunnel network.
        on_edge = {name for name, s in services.items() if "edge" in s.get("networks", {})}
        self.assertEqual(on_edge, {"web", "gateway"})

    def test_production_publishes_no_host_port(self):
        for name, service in self.prod["services"].items():
            self.assertFalse(service.get("ports"), f"{name} publishes a port")

    def test_model_api_keeps_its_hardening(self):
        model_api = self.prod["services"]["model-api"]
        self.assertEqual(model_api["mem_limit"], "2147483648")
        self.assertEqual(model_api["cap_drop"], ["ALL"])
        self.assertEqual(model_api["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(list(model_api["networks"]), ["api"])
        # No docker-init as PID 1 (CR-605): it would carry the full environment, secrets included.
        self.assertFalse(model_api.get("init", False))

    def test_internal_services_are_not_on_the_edge_network(self):
        for name in ("db", "backend", "postgres", "minio", "mlflow", "model-api"):
            self.assertNotIn("edge", self.prod["services"][name].get("networks", {}), name)

    def test_secrets_are_required_and_come_from_the_env_file_only(self):
        for key in ("POSTGRES_PASSWORD", "MODEL_API_SERVICE_TOKEN", "MINIO_ROOT_PASSWORD", "INTERNAL_INTROSPECTION_SECRET"):
            env = {k: v for k, v in parse_env(self.prod_env).items() if k != key}
            broken = self.tmp / f"missing-{key}.env"
            broken.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
            result = compose(ROOT, broken, "config", "-q")
            self.assertNotEqual(result.returncode, 0, key)
            self.assertIn(key, result.stderr)
        text = (ROOT / "compose.yml").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)(password|secret|token)\s*[:=]\s*\S")

    def test_server_layout_works_with_default_relative_paths(self):
        layout = self.tmp / "opt-invoiceops"
        layout.mkdir()
        for link, target in (("app", "invoiceops-app"), ("mlflow", "invoiceops-mlflow"),
                             ("model-api", "invoiceops-model-api"), ("ml", "invoiceops-ml")):
            (layout / link).symlink_to(V2 / target, target_is_directory=True)
        stack = layout / "stack"
        stack.mkdir()
        shutil.copy(ROOT / "compose.yml", stack / "compose.yml")
        env_file = layout / "invoiceops.env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in DUMMY.items()), encoding="utf-8")
        result = compose(stack, env_file, "config", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["services"]["model-api"]["build"]["additional_contexts"]["ml"], str(layout / "ml"))
        self.assertEqual(set(config["services"]), SERVICES)

    # -- local --------------------------------------------------------------------------------
    def test_local_uses_its_own_project_volumes_and_networks(self):
        local = self.local
        self.assertEqual(local["name"], "invoiceops-local")
        names = json.dumps({"volumes": local["volumes"], "networks": local["networks"]})
        for production_name in (PROD_VOLUME, "invoiceops_api", "invoiceops_db", "aglai_edge"):
            self.assertNotIn(production_name, names)
        self.assertFalse(any(n.get("external") for n in local["networks"].values()), "no external network locally")

    def test_local_publishes_only_web_and_gateway_on_loopback(self):
        published = {n: s["ports"] for n, s in self.local["services"].items() if s.get("ports")}
        self.assertEqual(set(published), {"web", "gateway"})
        for ports in published.values():
            for port in ports:
                self.assertEqual(port["host_ip"], "127.0.0.1")
        self.assertEqual(published["web"][0]["target"], 80)
        self.assertEqual(published["gateway"][0]["target"], 8080)

    def test_local_http_origins_are_distinct_localhost_hosts_and_the_sso_handoff_is_coherent(self):
        services = self.local["services"]
        app_origin = services["backend"]["environment"]["PUBLIC_ORIGIN"]
        mlflow_origin = services["backend"]["environment"]["MLFLOW_PUBLIC_URL"]
        self.assertTrue(app_origin.startswith("http://") and ".localhost:" in app_origin)
        self.assertTrue(mlflow_origin.startswith("http://") and ".localhost:" in mlflow_origin)
        self.assertNotEqual(app_origin.split(":")[1], mlflow_origin.split(":")[1], "same host would mix cookies")
        gateway = services["gateway"]["environment"]
        self.assertEqual(gateway["GATEWAY_ALLOW_INSECURE_HTTP"], "1")
        self.assertEqual(gateway["GATEWAY_PUBLIC_ORIGIN"], mlflow_origin)  # the app only redirects back to this origin
        self.assertEqual(gateway["GATEWAY_APP_ORIGIN"], app_origin)
        self.assertEqual(services["backend"]["environment"]["NODE_ENV"], "development")
        # Host ports match the ports written in the origins.
        self.assertTrue(app_origin.endswith(f":{services['web']['ports'][0]['published']}"))
        self.assertTrue(mlflow_origin.endswith(f":{services['gateway']['ports'][0]['published']}"))
        # Internal URLs are the production ones.
        self.assertEqual(services["backend"]["environment"]["MODEL_API_URL"], "http://invoiceops-model-api:8000")
        self.assertEqual(services["model-api"]["mem_limit"], "2147483648")

    def test_local_has_no_tunnel_and_keeps_the_hardening(self):
        self.assertFalse([n for n in self.local["services"] if "cloudflared" in n or "tunnel" in n])
        model_api = self.local["services"]["model-api"]
        self.assertEqual(model_api["cap_drop"], ["ALL"])
        self.assertEqual(model_api["security_opt"], ["no-new-privileges:true"])

    def test_local_example_env_is_safe_to_commit_and_complete(self):
        self.assertFalse(LOCAL_ENV.name.startswith(".env"))
        values = parse_env(LOCAL_ENV)
        for key in DUMMY:
            self.assertIn(key, values, key)
        secrets = [values[k] for k in ("MODEL_API_SERVICE_TOKEN", "GATEWAY_SERVICE_READ_TOKEN",
                                       "INTERNAL_INTROSPECTION_SECRET", "OFFICIAL_VALIDATION_SEED",
                                       "MLFLOW_AUTH_ADMIN_PASSWORD")]
        self.assertEqual(len(set(secrets)), len(secrets), "each shared secret must be different")
        self.assertTrue(all(len(s) >= 32 for s in secrets))
        self.assertEqual(values["PUBLIC_ORIGIN"], f"http://invoiceops.localhost:{values['LOCAL_APP_PORT']}")
        self.assertEqual(values["MLFLOW_PUBLIC_URL"], f"http://mlflow-invoiceops.localhost:{values['LOCAL_MLFLOW_PORT']}")
        self.assertEqual(values["MLFLOW_PUBLIC_URL"], values["GATEWAY_PUBLIC_ORIGIN"])
        self.assertEqual(values["PUBLIC_ORIGIN"], values["APP_PUBLIC_URL"])


class RepositoryContentTests(unittest.TestCase):
    def test_compose_yml_includes_the_three_repositories_and_keeps_the_project_name(self):
        text = (ROOT / "compose.yml").read_text(encoding="utf-8")
        self.assertIn("\nname: invoiceops\n", text)
        self.assertIn("include:", text)
        for needle in ("INVOICEOPS_APP_DIR:-../app", "INVOICEOPS_MLFLOW_DIR:-../mlflow",
                       "INVOICEOPS_MODEL_API_DIR:-../model-api"):
            self.assertIn(needle, text)
        self.assertNotIn("services:", text, "services belong to their own repositories")

    def test_guides_exist_and_pass_the_project_explicitly(self):
        for name in ("LOCAL_E2E.md", "README.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("docker compose -p invoiceops-local -f compose.yml", text, name)
            self.assertNotIn("docker compose -f compose.yml -f compose.local.yml", text, name)
        self.assertIn("/opt/invoiceops/invoiceops.env", (ROOT / "README.md").read_text(encoding="utf-8"))


# A minimal copy of the three included repositories. Each one has a stray `.env` next to its compose
# file that sets LEAK: the real `compose.yml` must never read it (`env_file: /dev/null`).
FIXTURE_SERVICE = """services:
  {name}:
    image: busybox
    environment:
      LEAK: ${{LEAK:-unset}}
"""


class IncludedDotEnvIsolationTests(RequiresStackPrerequisites):
    def build_layout(self, compose_text):
        tmp = Path(tempfile.mkdtemp(prefix="stack-isolation-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for repo, files in (("app", ("compose.production.yml",)),
                            ("mlflow", ("compose.yml", "compose.gateway.yml")),
                            ("model-api", ("compose.production.yml",))):
            (tmp / repo).mkdir()
            for index, file in enumerate(files):
                (tmp / repo / file).write_text(FIXTURE_SERVICE.format(name=f"{repo}-{index}"), encoding="utf-8")
            (tmp / repo / ".env").write_text("LEAK=from-dotenv\n", encoding="utf-8")
        (tmp / "stack").mkdir()
        (tmp / "stack" / "compose.yml").write_text(compose_text, encoding="utf-8")
        env_file = tmp / "stack.env"
        env_file.write_text("".join(
            f"{key}={tmp / name}\n"
            for key, name in (("INVOICEOPS_APP_DIR", "app"), ("INVOICEOPS_MLFLOW_DIR", "mlflow"),
                              ("INVOICEOPS_MODEL_API_DIR", "model-api"))), encoding="utf-8")
        return tmp, env_file

    def leaked_values(self, compose_text):
        tmp, env_file = self.build_layout(compose_text)
        result = compose(tmp / "stack", env_file, "config", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertEqual(len(services), 4)
        return {service["environment"]["LEAK"] for service in services.values()}

    def test_a_dotenv_inside_an_included_repository_is_never_read(self):
        self.assertEqual(self.leaked_values((ROOT / "compose.yml").read_text(encoding="utf-8")), {"unset"})

    def test_the_fixture_does_leak_without_env_file_dev_null(self):
        """Control: the same layout leaks when `env_file: /dev/null` is removed, so the test above can fail."""
        text = (ROOT / "compose.yml").read_text(encoding="utf-8")
        without = "".join(line for line in text.splitlines(keepends=True) if "env_file: /dev/null" not in line)
        self.assertNotEqual(without, text)
        self.assertEqual(self.leaked_values(without), {"from-dotenv"})


if __name__ == "__main__":
    unittest.main()
