"""Opt-in, local-only STACK-03 App smoke; importing or checking never contacts Docker.

Runtime execution creates a new labelled Compose project, uses only the Unix Docker
socket, generates all credentials in memory, and writes one sanitized report.  It
never reads env files, Docker contexts, credential helpers, cookies, or raw bodies.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from http.cookiejar import CookieJar
from http.client import HTTPException


ROOT = Path(__file__).resolve().parents[1]
MLFLOW = ROOT.parent / "invoiceops-mlflow"
APP = ROOT.parent / "invoiceops-app"
FILES = (MLFLOW / "compose.yml", ROOT / "compose.yml", ROOT / "compose.model-api.yml",
         APP / "docker-compose.yml", ROOT / "compose.invoiceops-app.yml")
SOCKET = "unix:///var/run/docker.sock"
SERVICES = frozenset({"postgres", "minio", "minio-init", "mlflow", "model-api", "db", "backend", "frontend"})
VOLUMES = frozenset({"postgres_data", "minio_data", "postgres_test_data", "app_postgres_data"})
SECRET_KEYS = frozenset({"STACK_POSTGRES_PASSWORD", "STACK_MINIO_ROOT_PASSWORD",
                         "MLFLOW_AUTH_ADMIN_PASSWORD", "MLFLOW_FLASK_SERVER_SECRET_KEY",
                          "STACK_MODEL_API_MLFLOW_PASSWORD", "MODEL_API_ADMIN_TOKEN"})
RECEIPT_SCHEMA = "STACK03-receipt-v3"
RECEIPT_BYTES_MAX = 16384
RESPONSE_BYTES_MAX = 65536
RECEIPT_ROOT = ROOT / "evidence" / "stack03"
SOURCE_INPUTS = (*FILES, Path(__file__))
BUILD_CONTEXTS = {
    "invoiceops-app": APP,
    "invoiceops-mlflow": MLFLOW,
    "invoiceops-model-api": ROOT.parent / "invoiceops-model-api",
}


class Failure(Exception):
    """A fixed, secret-free failure category."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def command_failure_code(returncode, operation):
    """Classify failures from fixed exit semantics without retaining child output."""
    return f"{operation}_exit_{returncode}_output_suppressed"


def build_failure_code(stderr):
    """Return a fixed, secret-free build stage code from transient child stderr."""
    value = stderr.lower()
    for marker, code in (
        ("pnpm --filter @invoiceops/backend build", "backend_compile_failed"),
        ("pnpm --filter @invoiceops/frontend build", "frontend_compile_failed"),
        ("pnpm install --frozen-lockfile", "dependency_install_failed"),
        ("prisma generate", "prisma_generation_failed"),
        ("corepack", "corepack_failed"),
        ("failed to resolve source metadata", "base_image_unavailable"),
        ("pull access denied", "base_image_unavailable"),
    ):
        if marker in value:
            return code
    return "unspecified_build_failed"


def compose_up_failure_code(output):
    """Return a fixed, secret-free start failure code from transient child output."""
    value = output.lower()
    for marker, code in (
        ("port is already allocated", "port_conflict"),
        ("dependency failed to start", "dependency_health_failed"),
        ("timed out", "startup_wait_timed_out"),
        ("no such service", "unknown_service"),
        ("unknown flag", "unsupported_compose_flag"),
    ):
        if marker in value:
            return code
    return "unspecified_compose_up_failed"


def app_bootstrap_failure_code(output, returncode=1):
    """Classify the migration/fixture runner without retaining its output."""
    value = output.lower()
    prisma = re.search(r"\b(p[1-9][0-9]{3})\b", value)
    if prisma:
        return f"migration_prisma_{prisma.group(1)}"
    if returncode == 41:
        return "migration_failed"
    if returncode == 42:
        return "fixture_failed"
    if "stack03_app_bootstrap=migration_failed" in value:
        return "migration_failed"
    if "stack03_app_bootstrap=fixture_failed" in value:
        return "fixture_failed"
    if "stack03_app_migration_done" in value:
        return "fixture_failed"
    for marker, code in (
        ("stack03_app_bootstrap=prisma_config_dependency_missing", "prisma_config_dependency_missing"),
        ("stack03_app_bootstrap=prisma_config_load_failed", "prisma_config_load_failed"),
        ("cannot find package 'dotenv'", "prisma_config_dependency_missing"),
        ('cannot find package "dotenv"', "prisma_config_dependency_missing"),
        ("failed to load config file", "prisma_config_load_failed"),
        ("error: prisma config", "prisma_config_load_failed"),
        ("p1001", "database_unreachable"),
        ("p1010", "database_permission_failed"),
        ("prisma migrate deploy", "migration_failed"),
        ("local-demonstration", "fixture_failed"),
        ("command not found", "runner_command_unavailable"),
        ("cannot find module", "runner_module_missing"),
        ("no such image", "runner_image_missing"),
        ("pull access denied", "runner_image_missing"),
        ("manifest for", "runner_image_missing"),
        ("no such service", "unknown_service"),
        ("unknown flag", "unsupported_compose_flag"),
    ):
        if marker in value:
            return code
    return "unspecified_app_bootstrap_failed"


def prisma_migration_diagnostic(output):
    """Reduce one transient Prisma stderr capture to allowlisted diagnostic fields."""
    category = next((match.group(1).upper() for match in re.finditer(r"\b(P[1-9][0-9]{3})\b", output, re.IGNORECASE)),
                    "UNKNOWN")
    migrations = {path.parent.name for path in (APP / "apps/backend/prisma/migrations").glob("*/migration.sql")}
    migration = next((match.group(1) for match in re.finditer(r"\b(20[0-9]{12}_[a-z0-9_]+)\b", output)
                      if match.group(1) in migrations), None)
    return {"prisma_category": category, **({"migration": migration} if migration else {})}


def model_sdk_failure_code(module, action, output):
    """Reduce STACK-02's bounded diagnostic markers to a fixed failure code."""
    exception = next((code for marker, code in (
        ("connectionerror", "connection_error"),
        ("max retries exceeded", "connection_error"),
        ("invalidschema", "invalid_schema"),
        ("keyerror", "missing_environment"),
        ("attributeerror", "attribute_error"),
        ("valueerror", "value_error"),
        ("runtimeerror", "runtime_error"),
        ("importerror", "import_error"),
        ("oserror", "os_error"),
    ) if marker in output.lower()), "unknown")
    exception_type = next((match.group(1) for match in re.finditer(
        r"^STACK03_EXCEPTION_TYPE=([A-Za-z][A-Za-z0-9_]{0,63})$", output, re.MULTILINE
    )), "unknown")
    diagnostic = module.reader_diagnostic(output)
    if diagnostic is not None:
        suffix = f"_line{diagnostic['source_line']}" if "source_line" in diagnostic else ""
        return "model_sdk_{}_{}_{}_{}{}".format(
            action, diagnostic["stage"], diagnostic["exception"], diagnostic["http_status"], suffix
        ) + (f"_{exception_type}" if diagnostic["exception"] == "other" and exception_type != "unknown" else "")
    progress = module.sdk_progress(output)
    return f"model_sdk_{action}_{progress or 'unknown'}_{exception}_failed"


def model_sdk_target(action):
    """Choose the container that owns each STACK-02 fixture operation."""
    if action == "bootstrap":
        return "mlflow", "http://127.0.0.1:5000"
    if action == "fixture":
        return "model-api", "http://mlflow:5000"
    raise Failure("invalid_model_sdk_action")


def authenticated_invoice_context(profile):
    """Derive the App's authorized user context from the authenticated profile."""
    require(isinstance(profile, dict) and isinstance(profile.get("id"), str), "profile_context_missing")
    memberships = profile.get("memberships")
    require(isinstance(memberships, list), "profile_context_missing")
    organization = next((item.get("organization") for item in memberships if isinstance(item, dict)
                         and isinstance(item.get("organization"), dict)
                         and isinstance(item["organization"].get("id"), str)), None)
    require(isinstance(organization, dict), "profile_context_missing")
    return {"organizationId": organization["id"], "ownerType": "user", "ownerId": profile["id"]}


def wrapped_model_sdk(source):
    """Expose only an exception class marker while preserving STACK-02's fixture code."""
    body = "\n".join("    " + line for line in source.splitlines())
    return "try:\n" + body + "\nexcept Exception as error:\n    print('STACK03_EXCEPTION_TYPE=' + type(error).__name__, flush=True)\n    raise\n"


def validate_inputs(project, backend_port, frontend_port, report):
    require(bool(re.fullmatch(r"stack03-proof-[a-z0-9][a-z0-9-]{5,39}", project)), "invalid_project")
    require(all(type(port) is int and 1024 <= port <= 63535 for port in (backend_port, frontend_port))
            and backend_port != frontend_port and backend_port + 1000 != frontend_port
            and backend_port + 2000 != frontend_port, "invalid_ports")
    parent = report.parent.resolve()
    require(report.is_absolute() and parent.is_dir() and not report.exists()
             and not report.is_symlink() and parent.stat().st_uid == os.getuid(), "unsafe_report_path")


def validate_receipt_path(report):
    """Keep durable runtime evidence in the Stack-owned tracked receipt directory."""
    require(report.resolve().parent == RECEIPT_ROOT.resolve(), "receipt_path_not_stack_owned")


def fingerprint_paths(paths, relative_to):
    """Hash fixed non-secret inputs without recording their contents."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(paths):
        relative = path.relative_to(relative_to).as_posix()
        data = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        count += 1
    return {"sha256": digest.hexdigest(), "files": count}


def is_build_context_input(component, path):
    """Mirror the local Docker ignore contracts without opening environment files."""
    relative = path.relative_to(BUILD_CONTEXTS[component])
    parts, name = relative.parts, relative.name
    if component == "invoiceops-app":
        return not (".git" in parts or "node_modules" in parts or "dist" in parts or name.startswith(".env"))
    if component == "invoiceops-model-api":
        return not (".git" in parts or ".venv" in parts or "__pycache__" in parts or "tests" in parts
                    or ".pytest_cache" in parts or ".ruff_cache" in parts or "dist" in parts
                    or name.startswith(".env") or name.endswith(".egg-info"))
    return relative.as_posix() in {"Dockerfile.mlflow", ".dockerignore", "docker/mlflow-entrypoint.sh",
                                   "docker/mlflow-healthcheck.py", "docker/mlflow-migrate.py"}


def build_context_fingerprint(component):
    """Fingerprint every source byte Docker can consume for one built image."""
    root = BUILD_CONTEXTS[component]
    require(root.is_dir(), "build_context_missing")
    paths = [path for path in root.rglob("*") if path.is_file() and is_build_context_input(component, path)]
    require(paths, "build_context_empty")
    return fingerprint_paths(paths, root)


def git_revision(root=ROOT):
    """Return a bounded non-secret revision identifier without shelling out."""
    head = root / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            value = (root / ".git" / value.removeprefix("ref: ")).read_text(encoding="utf-8").strip()
    except OSError:
        return "UNAVAILABLE"
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else "UNAVAILABLE"


def source_provenance():
    """Bind the receipt to every Compose/harness input and every built context."""
    components = {
        "invoiceops-stack": {"revision": git_revision(ROOT), **fingerprint_paths(SOURCE_INPUTS, ROOT.parent)},
    }
    for name, root in BUILD_CONTEXTS.items():
        components[name] = {"revision": git_revision(root), **build_context_fingerprint(name)}
    encoded = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"revision": components["invoiceops-stack"]["revision"],
            "sha256": hashlib.sha256(encoded).hexdigest(), "components": components}


def validate_source_provenance(provenance):
    """Reject missing or altered provenance before a receipt becomes durable evidence."""
    require(isinstance(provenance, dict), "invalid_source_provenance")
    components = provenance.get("components")
    require(isinstance(components, dict) and set(components) == {"invoiceops-stack", *BUILD_CONTEXTS},
            "missing_source_provenance")
    for name, value in components.items():
        require(isinstance(value, dict) and set(value) == {"revision", "sha256", "files"}
                and isinstance(value["files"], int) and value["files"] > 0
                and value["revision"] == git_revision(BUILD_CONTEXTS.get(name, ROOT))
                and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "mismatched_source_provenance")
    require(provenance == source_provenance(), "mismatched_source_provenance")


def static_contract():
    """Daemon-free checks intentionally limited to tracked topology inputs."""
    require(all(path.is_file() for path in FILES), "required_compose_file_missing")
    overlay = FILES[-1].read_text(encoding="utf-8")
    require("127.0.0.1:${STACK_APP_BACKEND_PORT" in overlay
            and "127.0.0.1:${STACK_APP_FRONTEND_PORT" in overlay, "loopback_contract_missing")
    require("MODEL_API_URL" in overlay and "MODEL_API_MODEL_ID" in overlay, "model_api_contract_missing")
    require("MLFLOW_READ_PASSWORD" not in overlay and "MLFLOW_AUTH_ADMIN_PASSWORD" not in overlay,
            "mlflow_leak_in_app_overlay")


def safe_environment(directory, values):
    """Pass a minimal environment; generated values stay in process memory only."""
    plugin = Path("/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose")
    config = {"auths": {"https://index.docker.io/v1/": {}}, "credsStore": "", "credHelpers": {}}
    if plugin.is_file() and os.access(plugin, os.X_OK):
        config["cliPluginsExtraDirs"] = [str(plugin.parent)]
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    environment = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(directory),
                    "DOCKER_CONFIG": str(directory), "COMPOSE_DISABLE_ENV_FILE": "true",
                    "NO_PROXY": "*", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    environment.update(values)
    return environment


class Harness:
    def __init__(self, project, backend_port, frontend_port, report, docker="docker"):
        self.project, self.ports, self.report_path, self.docker = project, (backend_port, frontend_port), report, docker
        self.env = {}
        self.started = False
        self.owned_ids = None
        self.model_fixture = None
        self.cleanup_counts = {"containers": 0, "networks": 0, "volumes": 0, "images": 0}
        self.phase = "preflight"
        self.report = {"project": project, "status": "FAIL", "phases": [], "assertions": {},
                       "shutdown": "NOT_STARTED"}

    def command(self, args, *, timeout=120, input=None, operation="child", failure_code=None, environment=None):
        try:
            result = subprocess.run(args, cwd=ROOT, env=environment or self.env, input=input, text=True,
                                    capture_output=True, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("command_unavailable_or_timed_out") from None
        if result.returncode != 0:
            output = result.stdout + result.stderr
            if failure_code is not None:
                code = failure_code(output)
            elif operation == "build":
                code = build_failure_code(output)
            elif operation == "compose_up":
                code = compose_up_failure_code(output)
            elif operation == "app_bootstrap":
                code = app_bootstrap_failure_code(output, result.returncode)
            else:
                code = command_failure_code(result.returncode, operation)
            raise Failure(code)
        return result.stdout

    def suppressed_command(self, args, *, timeout, operation):
        """Run a diagnostic command without collecting its stdout or stderr."""
        try:
            result = subprocess.run(args, cwd=ROOT, env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure(f"{operation}_unavailable_or_timed_out") from None
        require(result.returncode == 0, command_failure_code(result.returncode, operation))

    def diagnostic_preflight(self):
        """Check Docker/Compose viability without retaining child output or Docker inventory."""
        static_contract()
        self.env.update({key: secrets.token_urlsafe(32) for key in SECRET_KEYS})
        self.env["STACK_APP_POSTGRES_PASSWORD"] = secrets.token_urlsafe(32)
        self.env.update({"STACK_MINIO_ROOT_USER": "s" + secrets.token_hex(8),
                         "MLFLOW_AUTH_ADMIN_USERNAME": "a" + secrets.token_hex(8),
                         "STACK_MODEL_API_MLFLOW_USERNAME": "r" + secrets.token_hex(8),
                         "STACK_MLFLOW_PORT": str(self.ports[0] + 1000),
                         "STACK_MODEL_API_PORT": str(self.ports[0] + 2000),
                         "STACK_APP_BACKEND_PORT": str(self.ports[0]),
                         "STACK_APP_FRONTEND_PORT": str(self.ports[1]),
                         "MODEL_API_URL": "http://model-api:8000", "MODEL_API_MODEL_ID": "invoice-review"})
        self.suppressed_command([self.docker, "--host", SOCKET, "version", "--format", "{{.Server.Version}}"],
                                timeout=30, operation="docker_version")
        command = [self.docker, "--host", SOCKET, "compose", "-p", self.project,
                   "--project-directory", str(MLFLOW), "--env-file", "/dev/null"]
        for path in FILES:
            command.extend(("-f", str(path)))
        self.suppressed_command([*command, "config", "--quiet"], timeout=60, operation="compose_config")

    def docker_command(self, *args, timeout=60):
        return self.command([self.docker, "--host", SOCKET, *args], timeout=timeout)

    def compose(self, *args, timeout=180):
        command = [self.docker, "--host", SOCKET, "compose", "-p", self.project,
                   "--project-directory", str(MLFLOW), "--env-file", "/dev/null"]
        for path in FILES:
            command.extend(("-f", str(path)))
        if args and args[0] == "run":
            command.extend(("--profile", "stack03-bootstrap"))
        operation = {"build": "build", "up": "compose_up", "run": "app_bootstrap"}.get(args[0], "child") if args else "child"
        return self.command([*command, *args], timeout=timeout, operation=operation)

    def inventory(self):
        """Discover only exact project candidates; no global cleanup or prune."""
        found = {"containers": {}, "networks": {}, "volumes": {}, "images": {}}
        selectors = (("containers", ("ps", "-a"), "Names"), ("networks", ("network", "ls"), "Name"),
                     ("volumes", ("volume", "ls"), "Name"))
        for kind, command, name_key in selectors:
            output = self.docker_command(*command, "--filter", f"label=com.docker.compose.project={self.project}",
                                         "--format", "{{json .}}")
            for line in output.splitlines():
                if not line:
                    continue
                row = json.loads(line)
                name = row.get(name_key, "").lstrip("/")
                require(name.startswith((self.project + "-", self.project + "_")), "ambiguous_resource")
                found[kind][name] = row.get("ID", name)
        output = self.docker_command("image", "ls", "--filter", f"label=com.docker.compose.project={self.project}",
                                     "--format", "{{json .}}")
        for line in output.splitlines():
            if not line:
                continue
            row = json.loads(line)
            identity = row.get("ID", "")
            # `docker image ls` emits either a short 12-character ID or a full digest.
            require(bool(re.fullmatch(r"(?:sha256:)?[0-9a-f]{12,64}", identity)), "ambiguous_owned_image")
            found["images"][identity] = identity
        return found

    def verify_owned(self, expected=None):
        found = self.inventory()
        for name in found["containers"]:
            service = name.removeprefix(self.project + "-").removesuffix("-1")
            require(service in SERVICES, "unexpected_owned_container")
        require(all(name in {f"{self.project}_default"} for name in found["networks"]), "unexpected_owned_network")
        require(all(name.removeprefix(self.project + "_") in VOLUMES for name in found["volumes"]),
                "unexpected_owned_volume")
        for identity in found["images"].values():
            label = self.docker_command("image", "inspect", "--format",
                                        "{{ index .Config.Labels \"com.docker.compose.project\" }}", identity).strip()
            require(label == self.project, "unexpected_owned_image")
        if expected is not None:
            require(found == expected, "ownership_changed")
        return found

    def preflight(self):
        static_contract()
        require(not any(self.inventory().values()), "preexisting_resource")
        self.env.update({key: secrets.token_urlsafe(32) for key in SECRET_KEYS})
        self.env["STACK_APP_POSTGRES_PASSWORD"] = secrets.token_urlsafe(32)
        self.env.update({"STACK_MINIO_ROOT_USER": "s" + secrets.token_hex(8),
                         "MLFLOW_AUTH_ADMIN_USERNAME": "a" + secrets.token_hex(8),
                         "STACK_MODEL_API_MLFLOW_USERNAME": "r" + secrets.token_hex(8),
                         "STACK_MLFLOW_PORT": str(self.ports[0] + 1000),
                         "STACK_MODEL_API_PORT": str(self.ports[0] + 2000),
                         "STACK_APP_BACKEND_PORT": str(self.ports[0]),
                         "STACK_APP_FRONTEND_PORT": str(self.ports[1]),
                         "MODEL_API_URL": "http://model-api:8000", "MODEL_API_MODEL_ID": "invoice-review"})
        config = json.loads(self.compose("config", "--format", "json", timeout=60))
        require(set(config["services"]) == SERVICES, "unexpected_services")
        for name, target, port in (("backend", 3000, self.ports[0]), ("frontend", 80, self.ports[1])):
            ports = config["services"][name].get("ports", [])
            require(ports == [{"mode": "ingress", "host_ip": "127.0.0.1", "target": target,
                               "published": str(port), "protocol": "tcp"}], "runtime_ports")
        backend_env = config["services"]["backend"].get("environment", {})
        require(set(backend_env).isdisjoint({"MLFLOW_READ_URL", "MLFLOW_UI_URL", "MLFLOW_READ_USERNAME", "MLFLOW_READ_PASSWORD"})
                or not any(backend_env.get(key) for key in ("MLFLOW_READ_URL", "MLFLOW_UI_URL", "MLFLOW_READ_USERNAME", "MLFLOW_READ_PASSWORD")),
                 "app_mlflow_leak")
        require(config["services"]["db"]["environment"].get("POSTGRES_PASSWORD") == self.env["STACK_APP_POSTGRES_PASSWORD"],
                "app_database_secret_missing")

    def model_http(self, method, path, body=None, token=None, timeout=15):
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        if token is not None:
            headers["X-Model-Admin-Token"] = token
        request = Request(f"http://127.0.0.1:{self.env['STACK_MODEL_API_PORT']}{path}", data=payload, method=method, headers=headers)
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=timeout)
            status, data = response.status, response.read(RESPONSE_BYTES_MAX + 1)
        except HTTPError as error:
            status, data = error.code, error.read(RESPONSE_BYTES_MAX + 1)
        except (URLError, TimeoutError, OSError, HTTPException):
            raise Failure("model_api_unavailable") from None
        require(len(data) <= RESPONSE_BYTES_MAX, "response_too_large")
        try:
            value = json.loads(data or b"{}")
        except json.JSONDecodeError:
            raise Failure("invalid_http_json") from None
        require(isinstance(value, dict), "invalid_http_json")
        return status, value

    def model_sdk(self, action, data):
        """Run STACK-02's synthetic MLflow fixture only inside this exact project."""
        source = ROOT / "scripts" / "stack_model_api_smoke.py"
        spec = __import__("importlib.util").util.spec_from_file_location("stack03_model_sdk", source)
        module = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(module)
        service, tracking_uri = model_sdk_target(action)
        identity = self.owned_ids["containers"].get(f"{self.project}-{service}-1")
        require(isinstance(identity, str), "model_sdk_container_identity_missing")
        environment = dict(self.env)
        environment.update({"MLFLOW_TRACKING_URI": tracking_uri,
                            "MLFLOW_TRACKING_USERNAME": self.env["MLFLOW_AUTH_ADMIN_USERNAME"],
                            "MLFLOW_TRACKING_PASSWORD": self.env["MLFLOW_AUTH_ADMIN_PASSWORD"],
                            "MLFLOW_HTTP_REQUEST_TIMEOUT": "10", "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
                            "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "0", "MLFLOW_DISABLE_TELEMETRY": "true"})
        keys = [key for key in environment if key.startswith("MLFLOW_TRACKING_") or key.startswith("MLFLOW_HTTP_")]
        keys += ["MLFLOW_DISABLE_TELEMETRY", "STACK_MODEL_API_MLFLOW_USERNAME", "STACK_MODEL_API_MLFLOW_PASSWORD"]
        output = self.command([self.docker, "--host", SOCKET, "exec", "-i", *sum((["-e", key] for key in keys), []),
                               identity, "python", "-B", "-c", wrapped_model_sdk(module.SDK), action], timeout=150,
                              input=json.dumps(data), failure_code=lambda value: model_sdk_failure_code(module, action, value),
                              environment=environment)
        return module.receipt(output)

    def bootstrap_model_and_app_database(self):
        self.phase = "model_fixture"; self.report["phases"].append(self.phase)
        bootstrap = self.model_sdk("bootstrap", {"project": self.project})
        require(isinstance(bootstrap.get("experiment"), str) and bootstrap.get("reader_nonadmin") is True
                and bootstrap.get("exact_read_role") is True, "model_bootstrap_failed")
        fixture = self.model_sdk("fixture", {"project": self.project, "experiment": bootstrap["experiment"]})
        require(isinstance(fixture.get("run_id"), str) and isinstance(fixture.get("version"), str), "model_fixture_failed")
        status, _ = self.model_http("POST", "/admin/models/invoice-review/refresh",
                                    token=self.env["MODEL_API_ADMIN_TOKEN"], timeout=130)
        require(status == 200, "model_refresh_failed")
        status, health = self.model_http("GET", "/models/invoice-review/health")
        require(status == 200 and health.get("model_id") == "invoice-review" and health.get("run_id") == fixture["run_id"]
                and health.get("model_version") == fixture["version"], "model_health_metadata_missing")
        self.model_fixture = fixture
        self.report["assertions"]["model_fixture_health"] = "PASS"

        self.phase = "app_schema_fixture"; self.report["phases"].append(self.phase)
        before = self.verify_owned(self.owned_ids)
        require(f"{self.project}-db-1" in before["containers"], "app_db_identity_missing")
        self.compose("run", "--rm", "--no-deps", "app-bootstrap", timeout=180)
        after = self.verify_owned(self.owned_ids)
        require(after["containers"][f"{self.project}-db-1"] == before["containers"][f"{self.project}-db-1"]
                and after["volumes"] == before["volumes"], "app_db_ownership_changed")
        self.report["assertions"]["owned_db_migration_and_synthetic_fixture"] = "PASS"

    def diagnose_app_migration(self):
        """Capture the owned migration stderr once and retain only allowlisted facts."""
        self.phase = "app_schema_fixture"; self.report["phases"].append(self.phase)
        command = [self.docker, "--host", SOCKET, "compose", "-p", self.project,
                   "--project-directory", str(MLFLOW), "--env-file", "/dev/null"]
        for path in FILES:
            command.extend(("-f", str(path)))
        # Reuse the service command exactly.  Overriding it skips the generated Prisma
        # config and can turn a valid migration diagnosis into an unrelated config error.
        command.extend(("--profile", "stack03-bootstrap", "run", "--rm", "--no-deps", "app-bootstrap"))
        try:
            result = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True,
                                    check=False, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("migration_diagnostic_unavailable_or_timed_out") from None
        output = result.stdout + "\n" + result.stderr
        try:
            self.report["migration_diagnostic"] = prisma_migration_diagnostic(output)
            if result.returncode:
                raise Failure(app_bootstrap_failure_code(output, result.returncode))
        finally:
            output = ""
            result = None

    def http(self, method, path, body=None, opener=None):
        payload = json.dumps(body).encode() if body is not None else None
        origin = f"http://127.0.0.1:{self.ports[0]}"
        headers = {"Content-Type": "application/json", "Origin": origin} if payload else {}
        request = Request(origin + path, data=payload, method=method, headers=headers)
        try:
            response = (opener or build_opener()).open(request, timeout=8)
            status, data = response.status, response.read(RESPONSE_BYTES_MAX + 1)
        except HTTPError as error:
            status, data = error.code, error.read(RESPONSE_BYTES_MAX + 1)
        except (URLError, TimeoutError):
            raise Failure("http_unavailable") from None
        require(len(data) <= RESPONSE_BYTES_MAX, "response_too_large")
        try:
            value = json.loads(data or b"{}")
        except json.JSONDecodeError:
            raise Failure("invalid_http_json") from None
        require(isinstance(value, dict), "invalid_http_json")
        return status, value

    def app_assertions(self):
        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        status, login = self.http("POST", "/auth/login", {"rut": "111111111", "password": "app-07-local-demonstration"}, opener)
        require(status == 200 and isinstance(login.get("user"), dict) and len(jar) == 1, "login_failed")
        status, profile_response = self.http("GET", "/profile", opener=opener)
        require(status == 200, "profile_context_missing")
        context = authenticated_invoice_context(profile_response.get("profile"))
        query = "?" + "&".join(f"{key}={context[key]}" for key in ("organizationId", "ownerType", "ownerId"))
        status, page = self.http("GET", "/invoices" + query, opener=opener)
        require(status == 200 and isinstance(page.get("invoices"), list) and len(page["invoices"]) >= 2, "invoice_list_failed")
        invoice = page["invoices"][0].get("invoiceId")
        require(isinstance(invoice, str), "invoice_identifier_missing")
        status, decision = self.http("POST", f"/invoices/{invoice}/decision{query}",
                                     {"mode": "PROBABILITY_POLICY", "policyVersion": "ml-policy-v1"}, opener)
        audit = decision.get("auditEvent")
        require(status == 200 and isinstance(audit, dict) and audit.get("policyProbabilitySource") == "MODEL_API"
                and audit.get("modelId") == "invoice-review" and audit.get("modelVersion") == self.model_fixture["version"]
                and audit.get("modelRunId") == self.model_fixture["run_id"],
                "model_metadata_missing")
        self.report["assertions"]["login_invoices_model_metadata"] = "PASS"
        self.docker_command("stop", self.owned_ids["containers"][f"{self.project}-model-api-1"], timeout=30)
        self.verify_owned(self.owned_ids)
        invoice = page["invoices"][1].get("invoiceId")
        require(isinstance(invoice, str), "invoice_identifier_missing")
        status, fallback = self.http("POST", f"/invoices/{invoice}/decision{query}",
                                     {"mode": "PROBABILITY_POLICY", "policyVersion": "ml-policy-v1"}, opener)
        fallback_audit = fallback.get("auditEvent")
        require(status == 200 and isinstance(fallback_audit, dict) and fallback_audit.get("policyProbabilitySource") == "MODEL_API_FALLBACK"
                and all(fallback_audit.get(key) is None for key in ("modelId", "modelVersion", "modelRunId")),
                "model_api_fallback_missing")
        self.report["assertions"]["controlled_model_api_unavailable_fallback"] = "PASS"

    def build(self):
        """Build the profile-gated migration runner explicitly without pulling."""
        self.compose("build", timeout=900)
        self.compose("build", "app-bootstrap", timeout=900)

    def start_model_api(self):
        """Start the unseeded Model API without waiting for its intentionally 503 health endpoint."""
        self.compose("up", "-d", "--no-build", "--no-deps", "model-api", timeout=60)
        self.wait_for_model_api_http()

    def wait_for_model_api_http(self):
        """Wait only for HTTP reachability; an unseeded model correctly returns 503."""
        deadline = time.monotonic() + 60
        while True:
            try:
                status, _ = self.model_http("GET", "/models/invoice-review/health")
                require(status in (200, 503), "model_api_invalid_readiness_response")
                return
            except Failure as error:
                if str(error) != "model_api_unavailable" or time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def execute(self):
        self.phase = "preflight"; self.report["phases"].append(self.phase); self.preflight()
        self.phase = "build_start"; self.report["phases"].append(self.phase)
        self.started = True
        self.build()
        self.phase = "base_start"; self.report["phases"].append(self.phase)
        self.compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "360",
                     "postgres", "minio", "minio-init", "mlflow", "db", timeout=390)
        self.owned_ids = self.verify_owned()
        self.phase = "model_api_start"; self.report["phases"].append(self.phase)
        self.start_model_api()
        self.owned_ids = self.verify_owned()
        self.bootstrap_model_and_app_database()
        self.phase = "app_start"; self.report["phases"].append(self.phase)
        self.compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "backend", "frontend", timeout=150)
        self.owned_ids = self.verify_owned()
        self.phase = "app_assertions"; self.report["phases"].append(self.phase)
        self.app_assertions()

    def execute_migration_diagnostic(self):
        self.phase = "preflight"; self.report["phases"].append(self.phase); self.preflight()
        self.phase = "build_start"; self.report["phases"].append(self.phase)
        self.started = True
        self.build()
        self.phase = "base_start"; self.report["phases"].append(self.phase)
        self.compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "360",
                     "postgres", "minio", "minio-init", "mlflow", "db", timeout=390)
        self.owned_ids = self.verify_owned()
        self.diagnose_app_migration()

    def cleanup_command(self, *args, timeout=120):
        """Remove one already-audited Docker identity without retaining command output."""
        try:
            result = subprocess.run([self.docker, "--host", SOCKET, *args], cwd=ROOT, env=self.env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def remove_owned_resources(self):
        """Delete only fresh, label-verified identities, including named Compose volumes."""
        failures = []
        for kind, command in (("containers", ("rm", "-f")), ("networks", ("network", "rm")),
                              ("volumes", ("volume", "rm")), ("images", ("image", "rm"))):
            owned = self.verify_owned()[kind]
            self.cleanup_counts[kind] += len(owned)
            for name, identity in owned.items():
                current = self.verify_owned()[kind]
                require(current.get(name) == identity, "ownership_changed_during_cleanup")
                target = identity if kind != "volumes" else name
                if not self.cleanup_command(*command, target):
                    failures.append(kind)
        after = self.verify_owned()
        require(not any(after.values()), "owned_resources_remain_after_cleanup")
        require(not failures, "owned_cleanup_command_failed")

    def shutdown(self):
        if not self.started:
            self.report["shutdown"] = "NOT_NEEDED"; return
        self.remove_owned_resources()
        self.report["shutdown"] = "PASS_ALL_OWNED_RESOURCES_REMOVED"

    def safe_receipt(self):
        """Project only bounded, reviewable facts; never serialize runtime output or values."""
        failure = self.report.get("failure")
        phases = []
        for phase in self.report["phases"]:
            phases.append({"name": phase, "status": "FAIL" if failure and failure["phase"] == phase else "PASS"})
        provenance = source_provenance()
        validate_source_provenance(provenance)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "project": self.project,
            "input_source": provenance,
            "status": self.report["status"],
            "phases": phases,
            "assertions": dict(sorted(self.report["assertions"].items())),
            "cleanup": {"status": self.report["shutdown"], "removed": self.cleanup_counts},
            "output_bounds": {"http_response_bytes_max": RESPONSE_BYTES_MAX,
                              "receipt_bytes_max": RECEIPT_BYTES_MAX,
                              "child_output": "NOT_RETAINED"},
        }
        if failure:
            receipt["failure"] = {"phase": failure["phase"], "code": failure["code"]}
        return receipt

    def write_receipt(self):
        receipt = self.safe_receipt()
        encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        require(len(encoded) <= RECEIPT_BYTES_MAX, "receipt_exceeds_output_bound")
        self.report_path.write_bytes(encoded)
        self.report_path.chmod(0o600)

    def finish(self, migration_diagnostic=False):
        try:
            (self.execute_migration_diagnostic if migration_diagnostic else self.execute)()
        except Failure as error:
            self.report["failure"] = {"phase": self.phase, "code": str(error)}
        except Exception:
            self.report["failure"] = {"phase": self.phase, "code": "unexpected_failure_output_suppressed"}
        finally:
            try:
                self.shutdown()
            except Exception:
                self.report["shutdown"] = "UNKNOWN_REQUIRES_REVIEW"
            self.report["status"] = "PASS" if not self.report.get("failure") and self.report["shutdown"].startswith("PASS") else "FAIL"
            self.write_receipt()
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="requires the separately authorized local operation")
    parser.add_argument("--project", required=True)
    parser.add_argument("--backend-port", required=True, type=int)
    parser.add_argument("--frontend-port", required=True, type=int)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="daemon-free static contract validation")
    parser.add_argument("--diagnose-preflight", action="store_true", help="runs one output-suppressed Docker/Compose preflight")
    parser.add_argument("--diagnose-bootstrap", action="store_true", help="captures one owned migration stderr and retains only allowlisted facts")
    args = parser.parse_args(argv)
    validate_inputs(args.project, args.backend_port, args.frontend_port, args.report)
    static_contract()
    if args.check:
        return 0
    require(args.execute, "execution_not_authorized_by_flag")
    validate_receipt_path(args.report)
    with tempfile.TemporaryDirectory(prefix="stack03-smoke-") as temporary:
        harness = Harness(args.project, args.backend_port, args.frontend_port, args.report)
        harness.env = safe_environment(Path(temporary), {})
        if args.diagnose_preflight:
            try:
                harness.diagnostic_preflight()
            except Failure as error:
                harness.report["failure"] = {"phase": "diagnostic_preflight", "code": str(error)}
            harness.report["shutdown"] = "NOT_NEEDED"
            harness.report["status"] = "PASS" if not harness.report.get("failure") else "FAIL"
            harness.write_receipt()
            return 0 if harness.report["status"] == "PASS" else 1
        return 0 if harness.finish(migration_diagnostic=args.diagnose_bootstrap)["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        raise SystemExit(str(error))
