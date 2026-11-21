#!/usr/bin/env python3
"""Local end-to-end smoke of the unified stack (compose.yml + compose.local.yml).

It drives the same flow as LOCAL_E2E.md through HTTP and the CLI:

  up -> migrate -> bootstrap admin -> organization + ML + student -> MLflow token ->
  notebooks 00..04 (headless, against the local gateway) -> SSO into MLflow (only that workspace) ->
  deploy from the API -> create an invoice -> assert its prediction -> down -v

Everything runs in its own compose project (default `invoiceops-e2e`) with its own volumes and
ports, so it never touches a manually started `invoiceops-local` stack and can never reach the
production names:

* every compose call passes `-p <project>` explicitly and the child environment loses
  COMPOSE_PROJECT_NAME / COMPOSE_FILE / COMPOSE_PROFILES (an exported COMPOSE_PROJECT_NAME beats the
  `name:` of the compose file and would silently point `down -v` at another project);
* `--project` must start with `invoiceops-e2e`;
* it prints `docker context show` at start and refuses a non-local docker target (DOCKER_HOST that is
  not a unix socket, or a context other than default / desktop-linux) unless `--allow-remote-docker`;
* if resources labeled with that project already exist the smoke ABORTS without touching them, unless
  `--reset` is passed explicitly (then they are removed with `down -v` before starting);
* it cleans up (`down -v`) on failure and on Ctrl-C, but only a project it owns.

Only stdlib is needed on the host, plus docker, uv or python3.12.

Usage (from invoiceops-stack):  python3 scripts/local_e2e_smoke.py [--skip-build] [--keep] [--reset] [--allow-remote-docker]
"""

from __future__ import annotations

import argparse
import contextlib
import http.cookiejar
import json
import math
import os
import pty
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / "local.example.env"
MODEL_NAME = "invoice-rejected-classifier"  # registered by notebook 04
NOTEBOOKS = ("00_setup", "01_dataset", "02_training_pipeline", "03_quality_gates", "04_mlflow")
# Names pinned by production: the smoke refuses to run if a project could resolve to them.
PRODUCTION_PROJECTS = {"invoiceops"}
# `--project` must start with this prefix: nothing else is ever created or removed by the smoke.
PROJECT_PREFIX = "invoiceops-e2e"
# Compose reads these from the caller's shell and they beat compose.yml / compose.local.yml.
COMPOSE_ENV_VARS = (
    "COMPOSE_PROJECT_NAME",
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "COMPOSE_ENV_FILES",
    "COMPOSE_PATH_SEPARATOR",
)


class SmokeError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def check(condition: object, message: str) -> None:
    if not condition:
        raise SmokeError(message)
    log(f"ok   {message}")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def validate_project(project: str) -> str:
    """Return the project name if the smoke may own it, else raise ``SmokeError``."""
    if project in PRODUCTION_PROJECTS or project == "invoiceops-local":
        raise SmokeError(f"refusing project name {project!r}: pick a name of its own")
    if not project.startswith(PROJECT_PREFIX):
        raise SmokeError(f"--project must start with {PROJECT_PREFIX!r} (got {project!r})")
    return project


LOCAL_DOCKER_CONTEXTS = ("default", "desktop-linux")


def docker_context_name(env: dict[str, str] | None = None) -> str:
    """The active docker context, as `docker context show` reports it (it honors DOCKER_CONTEXT)."""
    result = subprocess.run(
        ["docker", "context", "show"], env=env, text=True, capture_output=True, check=False, timeout=30
    )
    if result.returncode != 0:
        raise SmokeError(f"`docker context show` failed: {result.stderr.strip()[-300:]}")
    return result.stdout.strip()


def check_docker_target(
    context: str, docker_host: str | None, allow_remote: bool, emit=lambda message: None
) -> None:
    """Refuse a docker target that is not the local engine, unless ``allow_remote``.

    The smoke ends with `down -v` and starts a full stack: pointed at a remote engine it would do
    both there. A remote target is a `DOCKER_HOST` that is not a local unix socket, or a context
    other than the local ones.
    """
    emit(f"docker context: {context}" + (f" (DOCKER_HOST={docker_host})" if docker_host else ""))
    problems = []
    if docker_host and not docker_host.startswith("unix://"):
        problems.append(f"DOCKER_HOST={docker_host!r} is not a local unix socket")
    if context not in LOCAL_DOCKER_CONTEXTS:
        problems.append(f"docker context {context!r} is not one of {', '.join(LOCAL_DOCKER_CONTEXTS)}")
    if problems and not allow_remote:
        raise SmokeError(
            "refusing a non-local docker target: " + "; ".join(problems)
            + ". Pass --allow-remote-docker only if you really mean to run (and `down -v`) there."
        )
    if problems:
        emit("WARNING: --allow-remote-docker: running against a non-local docker target")


def prediction_run_matches(version: dict, prediction: dict) -> bool:
    """True when the prediction carries the run of the registered version (both must be non-null).

    A null `runId` on either side proves nothing about the run the model came from, so it fails.
    """
    expected = version.get("runId")
    return bool(expected) and prediction.get("runId") == expected


def version_val_aucs(client, mlflow_local: str, token: str) -> dict:
    """The `val_auc` tag of each registered version of MODEL_NAME, read from MLflow with the student's token."""
    filter_ = urllib.parse.quote(f"name='{MODEL_NAME}'")
    status, body, _ = client.request(
        "GET", f"{mlflow_local}/api/2.0/mlflow/model-versions/search?filter={filter_}",
        headers={"Authorization": f"Bearer {token}"},
    )  # fmt: skip
    if status != 200:
        raise SmokeError(f"MLflow model-versions/search answered HTTP {status}: {body[:300]}")
    aucs = {}
    for item in json.loads(body).get("model_versions", []):
        tags = {tag["key"]: tag["value"] for tag in item.get("tags", [])}
        aucs[str(item["version"])] = tags.get("val_auc")
    return aucs


def pick_version(versions: list, aucs: dict) -> dict:
    """The version notebook 05 verifies: highest `val_auc` tag, ties and missing tags resolved by the
    highest number (a missing or non-finite tag counts as -1)."""

    def auc(version: dict) -> float:
        try:
            value = float(aucs.get(str(version["version"])))
        except (TypeError, ValueError):
            return -1.0
        return value if math.isfinite(value) else -1.0

    return max(versions, key=lambda v: (auc(v), int(v["version"])))


def prior_invoices_sql(invoice_id: str) -> str:
    """Count the priors the prediction worker sends for this invoice (same supplierKey and scope,
    created earlier, not deleted). `supplier_prior_count` of the contract is at least this number when it
    is >= 1, and a NULL supplierKey (no backfill) would make the count 0 and the feature stay at 0.
    """
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", invoice_id):
        raise SmokeError(f"unexpected invoice id {invoice_id!r}")
    return (
        'select count(*) from "Invoice" p join "Invoice" i on i.id = \'' + invoice_id + '\' '
        'where i."supplierKey" is not null and p."supplierKey" = i."supplierKey" '
        'and p."organizationId" = i."organizationId" and p."scopeKey" = i."scopeKey" '
        'and p."deletedAt" is null and p."createdAt" < i."createdAt" and p.id <> i.id;'
    )


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


# ------------------------------------------------------------------------------------------------
# Compose
# ------------------------------------------------------------------------------------------------
class Stack:
    def __init__(self, env_file: Path, env: dict[str, str]):
        self.env_file = env_file
        self.env = env
        self.project = validate_project(env["LOCAL_PROJECT"])
        # `-p` always: it beats COMPOSE_PROJECT_NAME and the `name:` of the compose file.
        self.base = [
            "docker", "compose", "-p", self.project,
            "-f", str(ROOT / "compose.yml"), "-f", str(ROOT / "compose.local.yml"),
            "--env-file", str(env_file),
        ]  # fmt: skip
        # The `ops` profile (migrate, bootstrap) is added only where needed: with it active,
        # `up --wait` would also wait for those one-shot containers, which exit by design.
        self.ops = ["--profile", "ops"]
        # A variable exported in the caller's shell would beat --env-file, and COMPOSE_* variables
        # would beat the project and files chosen above: drop them all from the child environment.
        self.process_env = {
            k: v for k, v in os.environ.items() if k not in env and k not in COMPOSE_ENV_VARS
        }

    def run(self, *args: str, check_exit: bool = True, timeout: int = 900, ops: bool = False, stdin: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [*self.base, *(self.ops if ops else []), *args], cwd=ROOT, env=self.process_env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, input=stdin,
        )  # fmt: skip
        if check_exit and result.returncode != 0:
            raise SmokeError(f"docker compose {' '.join(args)} failed:\n{result.stdout[-3000:]}")
        return result

    def down(self) -> None:
        self.run("down", "-v", "--remove-orphans", "--timeout", "20", check_exit=False, timeout=300, ops=True)

    def leftovers(self) -> list[str]:
        project = self.project
        found: list[str] = []
        for kind, fmt in (("volume", "{{.Name}}"), ("network", "{{.Name}}")):
            out = subprocess.run(
                ["docker", kind, "ls", "--filter", f"label=com.docker.compose.project={project}",
                 "--format", fmt],
                text=True, capture_output=True,
            ).stdout  # fmt: skip
            found += out.split()
        out = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
            text=True, capture_output=True,
        ).stdout  # fmt: skip
        return found + out.split()

    def sql_scalar(self, sql: str) -> str:
        """Run one query in the app database (read-only use) and return its single value."""
        result = self.run(
            "exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1',
            timeout=120, stdin=sql,
        )  # fmt: skip
        return result.stdout.strip()

    def bootstrap_admin(self, name: str, rut: str, password: str) -> None:
        """Drive the interactive bootstrap CLI through a pty (values never reach argv)."""
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [*self.base, *self.ops, "run", "--rm", "bootstrap"], cwd=ROOT, env=self.process_env,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True,
        )  # fmt: skip
        os.close(slave)
        buffer = ""
        script = [("Name: ", name), ("RUT: ", rut),
                  ("Temporary password: ", password), ("Repeat the temporary password: ", password)]  # fmt: skip
        deadline = time.monotonic() + 240
        try:
            while script and time.monotonic() < deadline:
                if select.select([master], [], [], 1)[0]:
                    try:
                        chunk = os.read(master, 4096).decode(errors="replace")
                    except OSError:
                        break
                    buffer += chunk
                if buffer.endswith(script[0][0]) or script[0][0] in buffer[-80:]:
                    os.write(master, (script[0][1] + "\r").encode())
                    script.pop(0)
                    buffer = ""
            if script:
                raise SmokeError(f"bootstrap prompt not seen: {script[0][0]!r}; tail={buffer[-300:]!r}")
            while time.monotonic() < deadline:
                if select.select([master], [], [], 1)[0]:
                    try:
                        data = os.read(master, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    buffer += data.decode(errors="replace")
                if proc.poll() is not None:
                    break
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            os.close(master)
        check(code == 0 and "dministrator" in buffer, "bootstrap created the platform administrator")


# ------------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------------
class HttpError(SmokeError):
    def __init__(self, status: int, body: str, what: str):
        super().__init__(f"{what}: HTTP {status}: {body[:600]}")
        self.status = status
        self.body = body


class Client:
    """A browser-like client: one cookie jar, JSON helpers, Origin header on writes."""

    def __init__(self, origin: str):
        self.origin = origin
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, url: str, body: object | None = None, headers: dict | None = None,
                accept: str = "application/json", timeout: int = 60):  # fmt: skip
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", accept)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if method != "GET":
            request.add_header("Origin", self.origin)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.read().decode(), response.geturl()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(), url

    def api(self, method: str, path: str, body: object | None = None, expect: tuple[int, ...] = (200, 201),
            headers: dict | None = None):  # fmt: skip
        status, text, _ = self.request(method, f"{self.origin}/api/v1{path}", body, headers)
        if status not in expect:
            raise HttpError(status, text, f"{method} {path}")
        return json.loads(text) if text else {}


def wait_for(description: str, probe, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (urllib.error.URLError, OSError, HttpError) as error:
            last = error
        time.sleep(interval)
    raise SmokeError(f"timeout waiting for {description} (last error: {last})")


# ------------------------------------------------------------------------------------------------
# Notebooks
# ------------------------------------------------------------------------------------------------
def prepare_venv(workdir: Path) -> Path:
    venv = workdir / "venv"
    if shutil.which("uv"):
        subprocess.run(["uv", "venv", "--python", "3.12", "--seed", "-q", str(venv)], check=True)
    else:
        python = shutil.which("python3.12")
        if not python:
            raise SmokeError("python 3.12 (or uv) is required to run the notebooks")
        subprocess.run([python, "-m", "venv", str(venv)], check=True)
    python = venv / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "-q", "nbclient", "nbformat", "ipykernel"], check=True)
    return python


def run_notebooks(python: Path, workdir: Path, ml_checkout: Path, tracking_uri: str, token: str) -> None:
    """Execute notebooks 00..04 with nbclient inside the venv (the notebook installs invoiceops-ml)."""
    env = {
        **os.environ,
        "MLFLOW_TRACKING_URI": tracking_uri,
        "MLFLOW_TRACKING_TOKEN": token,
        # Notebook 00 installs the package from here: the local checkout (the release tag may not exist yet).
        "INVOICEOPS_ML_SPEC": str(ml_checkout),
        "INVOICEOPS_WORKDIR": str(workdir / "invoiceops_work"),
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "PYTHONUNBUFFERED": "1",
    }
    # `-I` is not used: the helper below is this very file, run with the venv interpreter.
    result = subprocess.run(
        [str(python), str(Path(__file__).resolve()), "--_notebooks", str(ml_checkout / "notebooks"), str(workdir)],
        env=env, cwd=workdir, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800,
    )  # fmt: skip
    if result.returncode != 0:
        # The output never contains the token: notebooks do not print it.
        raise SmokeError(f"notebooks failed:\n{result.stdout[-4000:]}")
    log(result.stdout.strip().splitlines()[-1])


def _notebooks_main(notebooks_dir: str, workdir: str) -> int:  # runs inside the venv
    import nbformat
    from nbclient import NotebookClient

    for name in NOTEBOOKS:
        notebook = nbformat.read(Path(notebooks_dir) / f"{name}.ipynb", as_version=4)
        started = time.monotonic()
        NotebookClient(
            notebook, kernel_name="python3", timeout=900, resources={"metadata": {"path": workdir}}
        ).execute()
        print(f"notebook {name} ok ({time.monotonic() - started:.0f}s)", flush=True)
    print(f"notebooks {', '.join(NOTEBOOKS)} ok", flush=True)
    return 0


# ------------------------------------------------------------------------------------------------
# The flow
# ------------------------------------------------------------------------------------------------
def flow(args: argparse.Namespace, stack: Stack, env: dict[str, str], workdir: Path) -> None:
    app_origin, mlflow_origin = env["PUBLIC_ORIGIN"], env["MLFLOW_PUBLIC_URL"]
    mlflow_local = f"http://127.0.0.1:{env['LOCAL_MLFLOW_PORT']}"  # notebooks: http is accepted for loopback only
    # The "ml" build context is relative to the Model API checkout; the notebooks use the same checkout.
    ml_checkout = (ROOT / env["INVOICEOPS_MODEL_API_DIR"] / env["INVOICEOPS_ML_CONTEXT"]).resolve()

    if not args.skip_build:
        log("building images (first run takes several minutes)")
        stack.run("build", timeout=3600, ops=True)
    log("starting the database and applying migrations")
    stack.run("up", "-d", "--wait", "--wait-timeout", "180", "db")
    stack.run("run", "--rm", "migrate", timeout=600, ops=True)
    log("bootstrapping the platform administrator (interactive CLI through a pty)")
    stack.bootstrap_admin(env["LOCAL_ADMIN_NAME"], env["LOCAL_ADMIN_RUT"], env["LOCAL_ADMIN_TEMP_PASSWORD"])
    log("starting the whole stack")
    stack.run("up", "-d", "--wait", "--wait-timeout", "600", timeout=900)
    wait_for("the app through the web", lambda: Client(app_origin).request("GET", f"{app_origin}/healthz")[0] == 200, 60)
    wait_for("the gateway", lambda: Client(mlflow_origin).request("GET", f"{mlflow_origin}/_gateway/healthz")[0] == 200, 120)

    # 1. Teacher: log in with the temporary password, set the real one.
    teacher = Client(app_origin)
    teacher.api("POST", "/auth/login", {"rut": env["LOCAL_ADMIN_RUT"], "password": env["LOCAL_ADMIN_TEMP_PASSWORD"]})
    profile = teacher.api("GET", "/auth/profile")["profile"]
    check(profile["mustChangePassword"] and profile["isPlatformAdministrator"], "bootstrap admin must change the temporary password")
    teacher.api("POST", "/auth/change-password", {"currentPassword": env["LOCAL_ADMIN_TEMP_PASSWORD"], "newPassword": env["LOCAL_ADMIN_PASSWORD"]}, expect=(200, 204))
    teacher.api("POST", "/auth/login", {"rut": env["LOCAL_ADMIN_RUT"], "password": env["LOCAL_ADMIN_PASSWORD"]})
    check(not teacher.api("GET", "/auth/profile")["profile"]["mustChangePassword"], "teacher logged in with the new password")

    # 2. Teacher: organization, ML on, student with deployModels.
    org = teacher.api("POST", "/organizations", {"name": "Curso Demo", "slug": "curso-demo", "firstAdminRut": env["LOCAL_ADMIN_RUT"]})["organization"]
    org_id = org["id"]
    teacher.api("PATCH", f"/organizations/{org_id}", {"mlEnabled": True})
    teacher.api("POST", "/admin/users", {
        "name": env["LOCAL_STUDENT_NAME"], "rut": env["LOCAL_STUDENT_RUT"],
        "temporaryPassword": env["LOCAL_STUDENT_TEMP_PASSWORD"],
        "memberships": [{"organizationId": org_id, "role": "USER", "permissions": {
            "readInvoices": True, "createInvoices": True, "deleteOwnInvoices": False,
            "deleteOthersInvoices": False, "deployModels": True}}],
    })  # fmt: skip
    log("organization created, ML enabled, student created with deployModels (verified by steps 3 to 7)")

    # 3. Student: login, password change, MLflow token.
    student = Client(app_origin)
    student.api("POST", "/auth/login", {"rut": env["LOCAL_STUDENT_RUT"], "password": env["LOCAL_STUDENT_TEMP_PASSWORD"]})
    student.api("POST", "/auth/change-password", {"currentPassword": env["LOCAL_STUDENT_TEMP_PASSWORD"], "newPassword": env["LOCAL_STUDENT_PASSWORD"]}, expect=(200, 204))
    student.api("POST", "/auth/login", {"rut": env["LOCAL_STUDENT_RUT"], "password": env["LOCAL_STUDENT_PASSWORD"]})
    expires = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    created = student.api("POST", "/api-tokens", {"name": "local-e2e", "organizationId": org_id, "scopes": ["mlflow:write"], "expiresAt": expires})
    token = created["secret"]
    check(token.startswith("iop_"), "student generated an MLflow token (mlflow:write)")

    # Gateway sanity: anonymous is refused, the token is accepted.
    anonymous = Client(mlflow_origin).request("GET", f"{mlflow_local}/api/2.0/mlflow/experiments/search?max_results=1")[0]
    check(anonymous == 401, "gateway refuses anonymous API calls (401)")

    # 4. Notebooks 00..04 against the local gateway.
    log("running notebooks 00..04 headless (installs invoiceops-ml from the local checkout)")
    python = prepare_venv(workdir)
    run_notebooks(python, workdir, ml_checkout, mlflow_local, token)

    # 5. The registered model is visible from the app; SSO shows only this workspace.
    models = student.api("GET", f"/models?organizationId={org_id}")
    registered = next((m for m in models["models"] if m["name"] == MODEL_NAME), None)
    check(registered and registered["versions"], f"app lists the registered model {MODEL_NAME}")
    aucs = version_val_aucs(Client(mlflow_local), mlflow_local, token)
    version = pick_version(registered["versions"], aucs)
    log(f"deploying version {version['version']} (val_auc={aucs.get(version['version'])}), the one notebook 05 verifies")
    check(models["workspace"] == f"org-{org_id}", "models belong to the student's organization workspace")

    status, body, final_url = student.request("GET", f"{mlflow_origin}/", accept="text/html")
    check(status == 200 and final_url.startswith(mlflow_origin), "SSO handoff app -> gateway lands on the MLflow UI")
    status, body, _ = student.request("GET", f"{mlflow_origin}/api/3.0/mlflow/workspaces")
    workspaces = [w["name"] for w in json.loads(body).get("workspaces", [])]
    check(status == 200 and workspaces == [f"org-{org_id}"], f"MLflow UI session sees only its workspace ({workspaces})")

    # 6. Deploy from the API (what «Modelos» does) and turn scoring on.
    deployment = student.api("POST", "/deployments", {"organizationId": org_id, "registeredModel": MODEL_NAME, "version": version["version"]})["deployment"]
    check(deployment["status"] == "ACTIVE", f"deployment passed the official gate (status={deployment['status']}, gate={deployment.get('gate')})")
    enabled = student.api("PATCH", f"/deployments/{deployment['id']}?organizationId={org_id}", {"inferenceEnabled": True})["deployment"]
    check(enabled["inferenceEnabled"], "scoring of new invoices turned on for the deployment")

    # 7. Invoice -> prediction with the deployed version and run.
    invoice = student.api("POST", "/invoices", {
        "organizationId": org_id, "businessId": f"FAC-E2E-{uuid.uuid4().hex[:6]}", "supplierName": "Proveedor local",
        "amountPesos": 125000, "currency": "CLP", "issuedOn": "2026-11-01", "dueOn": "2026-12-01", "businessStatus": "PENDING",
    }, headers={"Idempotency-Key": f"e2e-{uuid.uuid4().hex}"})["invoice"]  # fmt: skip

    def prediction():
        predictions = student.api("GET", f"/invoices/{invoice['id']}?organizationId={org_id}").get("predictions", [])
        done = [p for p in predictions if p["status"] == "SUCCEEDED"]
        failed = [p for p in predictions if p["status"] in {"FAILED", "SKIPPED"}]
        if failed:
            raise SmokeError(f"prediction did not succeed: {failed}")
        return done[0] if done else None

    result = wait_for("the invoice prediction", prediction, 120, interval=3)
    check(result["modelVersion"] == version["version"] and result["registeredModel"] == MODEL_NAME, "prediction carries the deployed model and version")
    check(prediction_run_matches(version, result), f"prediction carries the registered run id ({version.get('runId')})")
    check(0.0 <= result["probability"] <= 1.0, f"prediction probability is valid ({result['probability']:.3f})")

    # 8. A second invoice of the same supplier (other case and spacing) must see the first as a prior:
    # that is what makes supplier_prior_count >= 1 for the model. Features are not stored, so the check
    # replays the worker's own lookup (supplierKey + scope + createdAt) in the database.
    second = student.api("POST", "/invoices", {
        "organizationId": org_id, "businessId": f"FAC-E2E-{uuid.uuid4().hex[:6]}", "supplierName": "PROVEEDOR   Local",
        "amountPesos": 130000, "currency": "CLP", "issuedOn": "2026-11-02", "dueOn": "2026-12-02", "businessStatus": "PENDING",
    }, headers={"Idempotency-Key": f"e2e-{uuid.uuid4().hex}"})["invoice"]  # fmt: skip
    check(stack.sql_scalar(f'select "supplierKey" from "Invoice" where id = \'{second["id"]}\';') == "proveedor local",
          "the second invoice got the canonical supplierKey (case and spacing normalized)")
    priors = int(stack.sql_scalar(prior_invoices_sql(second["id"])) or 0)
    check(priors >= 1, f"the second invoice of the same supplier has {priors} prior invoice(s), so supplier_prior_count >= 1")

    def second_prediction():
        found = student.api("GET", f"/invoices/{second['id']}?organizationId={org_id}").get("predictions", [])
        if any(p["status"] in {"FAILED", "SKIPPED"} for p in found):
            raise SmokeError(f"second prediction did not succeed: {found}")
        return next((p for p in found if p["status"] == "SUCCEEDED"), None)

    wait_for("the second invoice prediction", second_prediction, 120, interval=3)
    log("ok   the second invoice was scored with the same deployed model")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-build", action="store_true", help="reuse the images already built")
    parser.add_argument("--keep", action="store_true", help="do NOT run down -v at the end (leaves the stack up)")
    parser.add_argument("--project", default=PROJECT_PREFIX, help=f"compose project, must start with `{PROJECT_PREFIX}`")
    parser.add_argument("--reset", action="store_true", help="remove resources that already exist for the project (down -v) instead of aborting")
    parser.add_argument("--allow-remote-docker", action="store_true", help="run even if the docker context or DOCKER_HOST is not local")
    parser.add_argument("--app-port", type=int, default=18100)
    parser.add_argument("--mlflow-port", type=int, default=18101)
    parser.add_argument("--_notebooks", nargs=2, metavar=("DIR", "WORKDIR"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._notebooks:
        return _notebooks_main(*args._notebooks)

    try:
        validate_project(args.project)
    except SmokeError as error:
        raise SystemExit(str(error)) from None
    for tool in ("docker",):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found")
    try:
        check_docker_target(docker_context_name(), os.environ.get("DOCKER_HOST"), args.allow_remote_docker, log)
    except (SmokeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None

    workdir = Path(tempfile.mkdtemp(prefix="invoiceops-e2e-"))
    env = parse_env(ENV_EXAMPLE)
    app_origin = f"http://invoiceops.localhost:{args.app_port}"
    mlflow_origin = f"http://mlflow-invoiceops.localhost:{args.mlflow_port}"
    env.update(
        LOCAL_PROJECT=args.project, LOCAL_APP_PORT=str(args.app_port), LOCAL_MLFLOW_PORT=str(args.mlflow_port),
        PUBLIC_ORIGIN=app_origin, APP_PUBLIC_URL=app_origin, MLFLOW_PUBLIC_URL=mlflow_origin, GATEWAY_PUBLIC_ORIGIN=mlflow_origin,
    )  # fmt: skip
    env_file = workdir / "e2e.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    env_file.chmod(0o600)
    stack = Stack(env_file, env)

    exit_code = 1
    owns_project = False  # set only once the project is known to be empty (or --reset removed it)
    try:
        for port in (args.app_port, args.mlflow_port):
            if not port_is_free(port):
                raise SmokeError(f"port {port} is already in use (another stack up? use --app-port/--mlflow-port)")
        existing = stack.leftovers()
        if existing and not args.reset:
            raise SmokeError(
                f"resources labeled with project {args.project!r} already exist: {existing}. "
                "Nothing was touched. Pick another --project, or pass --reset to remove them with down -v."
            )
        if existing:
            log(f"project {args.project}: --reset, removing {len(existing)} existing resource(s)")
            stack.down()
        owns_project = True
        flow(args, stack, env, workdir)
        exit_code = 0
        log("SMOKE OK")
    except (SmokeError, subprocess.SubprocessError) as error:
        log(f"FAILED: {error}")
        with contextlib.suppress(Exception):
            if not owns_project:
                raise SmokeError("not our project: no logs")
            tail = stack.run("logs", "--no-color", "--tail", "25", check_exit=False, timeout=120).stdout
            print(tail[-6000:], file=sys.stderr)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        if not owns_project:
            log("not cleaning up: this run never took ownership of the project")
        elif args.keep and exit_code == 0:
            log(f"--keep: stack left running (project {args.project}); remove it with down -v")
        else:
            log("cleaning up: down -v")
            stack.down()
            left = stack.leftovers()
            if left:
                log(f"WARNING: resources left behind: {left}")
                exit_code = exit_code or 1
            else:
                log("clean: no containers, volumes or networks left")
        shutil.rmtree(workdir, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
