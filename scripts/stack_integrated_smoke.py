#!/usr/bin/env python3
"""Offline-first, opt-in STACK-08 local integration smoke.

The harness creates only an isolated ``stack08-proof-*`` Compose project.  The
default path never reads an environment file, pulls images, logs into a registry,
or accepts operator credentials. ``--execute`` is deliberately required for
every Docker mutation; importing this module is daemon-free.
``--allow-network-build`` permits the explicitly authorized local build to fetch
missing build inputs. It does not enable registry authentication, but it does
not technically enforce a destination allowlist.
"""

import argparse
from http.cookiejar import CookieJar
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
MLFLOW = ROOT.parent / "invoiceops-mlflow"
NOTEBOOKS = ROOT.parent / "invoiceops-ml"
MODEL_API = ROOT.parent / "invoiceops-model-api"
APP = ROOT.parent / "invoiceops-app"
CONTRACTS = ROOT.parent / "invoiceops-contracts"
DEPLOY = ROOT / "scripts" / "stack_anvil_contract_deploy.py"
CONTRACT_PROOF = CONTRACTS / "stack04-proof-invoiceops-contracts"
FILES = (MLFLOW / "compose.yml", ROOT / "compose.yml", ROOT / "compose.model-api.yml",
         ROOT / "compose.anvil.yml", APP / "docker-compose.yml", ROOT / "compose.invoiceops-app.yml")
SOCKET = "unix:///var/run/docker.sock"
PROJECT_LABEL = "com.docker.compose.project"
PROJECT_PREFIX = "stack08-proof-"
PROJECT_PATTERN = re.compile(r"stack08-proof-[a-z0-9][a-z0-9-]{5,39}\Z")
RECEIPT_SCHEMA = "STACK08-receipt-v2"
RECEIPT_BYTES_MAX = 4096
HTTP_BYTES_MAX = 16384
BUILD_SERVICES = ("mlflow", "model-api", "backend", "frontend", "app-bootstrap")
BASE_SERVICES = ("postgres", "minio", "minio-init", "mlflow", "db")
APP_SERVICES = ("backend", "frontend")
ANVIL_SERVICE = "anvil"
ANVIL_DEPLOYER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
# This is Anvil's documented deterministic development key, never an operator key.
ANVIL_DEVELOPMENT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DOCKER_EXECUTABLE_DIRECTORIES = ("/usr/local/bin", "/usr/bin", "/bin")
FOUNDRY_EXECUTABLE_DIRECTORIES = (str(Path.home() / ".foundry" / "bin"),)
RESTRICTED_PATH_DIRECTORIES = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
NOTEBOOK_PYTHON = NOTEBOOKS / ".venv" / "bin" / "python"
NOTEBOOK_HGB = NOTEBOOKS / "notebooks" / "05_hist_gradient_boosting.ipynb"
CHAMPION_MODEL = "invoice-review-production"


NOTEBOOK_RUN_RECEIPT = r'''import json
import os
import tempfile
from pathlib import Path

import mlflow
import nbformat
from nbclient import NotebookClient
from invoiceops_ml.data import generate_synthetic_dataset
from invoiceops_ml.ownership import OwnershipContext, owner_experiment_name


root = Path(os.environ["STACK_NOTEBOOK_ROOT"])
notebook = Path(os.environ["STACK_NOTEBOOK_HGB"])
with tempfile.TemporaryDirectory() as workspace:
    dataset = generate_synthetic_dataset(seed=202605, rows=12000, output_root=Path(workspace) / "data")
    os.environ["INVOICEOPS_DATASET_DIR"] = str(dataset)
    document = nbformat.read(notebook, as_version=4)
    NotebookClient(document, timeout=150, kernel_name="python3").execute(cwd=root)

context = OwnershipContext(
    organization_slug=os.environ["INVOICEOPS_ORGANIZATION_SLUG"],
    owner_type=os.environ["INVOICEOPS_OWNER_TYPE"],
    owner_id=os.environ["INVOICEOPS_OWNER_ID"],
    created_by_rut=os.environ["INVOICEOPS_CREATED_BY_RUT"],
)
client = mlflow.MlflowClient(tracking_uri=os.environ["MLFLOW_TRACKING_URI"])
experiment = client.get_experiment_by_name(owner_experiment_name(context))
if experiment is None:
    raise RuntimeError("owner experiment missing")
runs = client.search_runs(
    [experiment.experiment_id],
    filter_string="tags.`mlflow.runName` = 'hist-gradient-boosting'",
    max_results=2,
    order_by=["attributes.start_time DESC"],
)
if len(runs) != 1:
    raise RuntimeError("expected exactly one notebook run")
print(json.dumps({"run_id": runs[0].info.run_id}, sort_keys=True))
'''

NOTEBOOK_READER_RECEIPT = r'''import json
import os

import mlflow
from mlflow import MlflowClient
from mlflow.server import get_app_client


mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
client = MlflowClient()
experiment = client.get_experiment_by_name(os.environ["STACK_NOTEBOOK_EXPERIMENT"])
if experiment is None:
    raise RuntimeError("notebook experiment missing")
auth = get_app_client("basic-auth", tracking_uri=mlflow.get_tracking_uri())
username = os.environ["STACK_MODEL_API_MLFLOW_USERNAME"]
user = auth.create_user(username, os.environ["STACK_MODEL_API_MLFLOW_PASSWORD"])
if user.is_admin:
    raise RuntimeError("reader must not be admin")
role = auth.create_role(workspace="default", name="stack08-notebook-reader")
expected = {("experiment", experiment.experiment_id, "READ"),
            ("registered_model", "invoice-review-production", "READ")}
for resource_type, resource_pattern, permission in sorted(expected):
    auth.add_role_permission(role.id, resource_type, resource_pattern, permission)
auth.assign_role(user.username, role.id)
if {(permission.resource_type, permission.resource_pattern, permission.permission)
        for permission in auth.list_role_permissions(role.id)} != expected:
    raise RuntimeError("reader permissions are not exact")
if [assigned.id for assigned in auth.list_user_roles(user.username)] != [role.id]:
    raise RuntimeError("reader role assignment is not exact")
if auth.get_user(user.username).is_admin:
    raise RuntimeError("reader must not be admin")
print(json.dumps({"experiment": experiment.name, "reader_nonadmin": True,
                  "exact_read_role": True}, sort_keys=True))
'''


class Failure(ValueError):
    """A fixed, receipt-safe failure code."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def validate_project(project):
    require(isinstance(project, str) and PROJECT_PATTERN.fullmatch(project), "invalid_project")


def validate_receipt_path(path):
    require(path is not None and not path.exists(), "receipt_path_must_be_new")
    require(path.parent.is_dir(), "receipt_parent_missing")


def docker_command(*arguments):
    return ["docker", "--host", SOCKET, *arguments]


def compose_command(project, *arguments):
    command = ["docker", "--host", SOCKET, "compose", "--project-name", project,
               "--project-directory", str(MLFLOW), "--env-file", "/dev/null"]
    for path in FILES:
        command.extend(("-f", str(path)))
    return [*command, *arguments]


def resolve_docker_directory(directories=DOCKER_EXECUTABLE_DIRECTORIES):
    """Return a trusted directory containing an executable Docker CLI."""
    for directory in directories:
        candidate = os.path.join(directory, "docker")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return directory
    raise Failure("docker_cli_not_found")


def resolve_foundry_directory(directories=FOUNDRY_EXECUTABLE_DIRECTORIES):
    """Return a trusted directory containing both required Foundry executables."""
    for directory in directories:
        cast = os.path.join(directory, "cast")
        forge = os.path.join(directory, "forge")
        if all(os.path.isfile(candidate) and os.access(candidate, os.X_OK) for candidate in (cast, forge)):
            return directory
    raise Failure("foundry_cli_not_found")


def suppressed(command, environment, timeout=120):
    """Run a mutation without retaining child stdout, stderr, or stdin."""
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("child_unavailable_or_timed_out") from None
    require(result.returncode == 0, "child_failed_output_suppressed")


def local_image_available(reference, environment):
    """Check a local image ID only; Docker is never allowed to pull here."""
    try:
        result = subprocess.run(docker_command("image", "inspect", reference), env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("image_preflight_unavailable") from None
    return result.returncode == 0


def dockerfile_bases(path):
    """Return literal FROM references; dynamic base references cannot be proven offline."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        raise Failure("build_prerequisite_missing") from None
    bases = []
    for line in source.splitlines():
        match = re.match(r"\s*FROM\s+(?:--platform=[^ ]+\s+)?([^ $]+)", line, re.I)
        if match:
            reference = match.group(1)
            require("${" not in reference and "$" not in reference, "dynamic_build_base_unsupported_offline")
            bases.append(reference)
    require(bases, "build_base_missing")
    return bases


def offline_build_prerequisites(environment):
    """Fail before build if any declared base image is absent from the local cache."""
    dockerfiles = (MLFLOW / "Dockerfile.mlflow", MODEL_API / "Dockerfile",
                   APP / "apps/backend/Dockerfile", APP / "apps/frontend/Dockerfile")
    for dockerfile in dockerfiles:
        for reference in dockerfile_bases(dockerfile):
            require(local_image_available(reference, environment), "offline_build_cache_miss")


def label_filter(project):
    return f"label={PROJECT_LABEL}={project}"


def owned_label(kind, identity, environment):
    """Read one bounded label value from an exact Docker identity."""
    resource_type = {"containers": "container", "networks": "network", "volumes": "volume"}[kind]
    label_path = ".Config.Labels" if kind == "containers" else ".Labels"
    template = f'{{{{ index {label_path} "{PROJECT_LABEL}" }}}}'
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as output:
        try:
            result = subprocess.run(docker_command("inspect", "--type", resource_type, "--format", template, identity), env=environment,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                    check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("ownership_inspection_unavailable") from None
        require(result.returncode == 0, "ownership_inspection_failed")
        output.seek(0)
        value = output.read(128)
    require(len(value) < 128, "ownership_label_too_large")
    return value.decode("utf-8", errors="strict").strip()


def verify_owned(project, expected, environment):
    validate_project(project)
    require(isinstance(expected, dict) and expected, "missing_owned_resources")
    for kind, identities in expected.items():
        require(kind in {"containers", "networks", "volumes"}, "invalid_owned_resource_kind")
        require(isinstance(identities, list) and identities, "invalid_owned_resource_set")
        for identity in identities:
            require(isinstance(identity, str) and identity and owned_label(kind, identity, environment) == project,
                    "ownership_changed")
    return expected


def remove_owned_resources(project, expected, environment):
    """Remove exact label-verified runtime resources; no down, prune, or sweep."""
    commands = {"containers": ("rm", "-f"), "networks": ("network", "rm"), "volumes": ("volume", "rm")}
    for kind, identities in verify_owned(project, expected, environment).items():
        for identity in identities:
            verify_owned(project, {kind: [identity]}, environment)
            suppressed(docker_command(*commands[kind], identity), environment)
            verify_removed_resource(kind, identity)


def docker_resource_status(kind, identity):
    """Return the exact Docker API status for one resource without child output."""
    paths = {"containers": "/containers/{}/json", "networks": "/networks/{}", "volumes": "/volumes/{}"}
    require(kind in paths and isinstance(identity, str) and identity, "invalid_owned_resource_kind")
    require(SOCKET.startswith("unix://"), "post_removal_inspection_unavailable")
    request = f"GET {paths[kind].format(quote(identity, safe=''))} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(SOCKET.removeprefix("unix://"))
            client.sendall(request.encode("ascii"))
            response = bytearray()
            while b"\r\n" not in response and len(response) <= 512:
                chunk = client.recv(128)
                require(chunk, "post_removal_inspection_ambiguous")
                response.extend(chunk)
    except (OSError, TimeoutError):
        raise Failure("post_removal_inspection_unavailable") from None
    line, separator, _ = bytes(response).partition(b"\r\n")
    require(separator and len(response) <= 512, "post_removal_inspection_ambiguous")
    match = re.fullmatch(rb"HTTP/\d\.\d ([0-9]{3}) [^\r\n]+", line)
    require(match is not None, "post_removal_inspection_ambiguous")
    return int(match.group(1))


def verify_removed_resource(kind, identity):
    """Succeed only when Docker confirms the exact resource no longer exists."""
    require(docker_resource_status(kind, identity) == 404, "post_removal_inspection_failed_closed")


def bounded_docker_output(command, environment, limit=512):
    """Read a deliberately formatted Docker fact under a fixed byte bound."""
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as output:
        try:
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.DEVNULL, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("ownership_inspection_unavailable") from None
        require(result.returncode == 0, "ownership_inspection_failed")
        output.seek(0)
        value = output.read(limit + 1)
    require(len(value) <= limit, "ownership_inspection_too_large")
    return value.decode("ascii", errors="strict")


def remove_derived_images(project, environment):
    """Remove exact Compose-derived tags only after their immutable IDs have no containers."""
    validate_project(project)
    for service in BUILD_SERVICES:
        reference = f"{project}-{service}:latest"
        try:
            identity = bounded_docker_output(docker_command("image", "inspect", "--format", "{{.Id}}", reference), environment).strip()
        except Failure as error:
            if str(error) == "ownership_inspection_failed":
                continue
            raise
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", identity), "invalid_owned_image_id")
        references = bounded_docker_output(docker_command("ps", "-a", "--no-trunc", "--filter",
                                                          f"ancestor={identity}", "--format", "{{.ID}}"), environment)
        require(not references.strip(), "owned_image_still_referenced")
        suppressed(docker_command("image", "rm", reference), environment)


def generated_environment(mlflow_port, api_port, backend_port, frontend_port, anvil_port):
    """Create isolated, in-memory values; no ambient environment or .env is used."""
    docker_directory = resolve_docker_directory()
    foundry_directory = resolve_foundry_directory()
    path_directories = (docker_directory, foundry_directory, *RESTRICTED_PATH_DIRECTORIES)
    environment = {"PATH": ":".join(dict.fromkeys(path_directories)), "NO_PROXY": "*",
                   "STACK_MLFLOW_PORT": str(mlflow_port), "STACK_MODEL_API_PORT": str(api_port),
                    "STACK_APP_BACKEND_PORT": str(backend_port), "STACK_APP_FRONTEND_PORT": str(frontend_port),
                    "STACK_APP_TEST_DB_PORT": str(backend_port + 1000), "STACK_ANVIL_PORT": str(anvil_port),
                    "MODEL_API_URL": "http://model-api:8000", "MODEL_API_MODEL_ID": "invoice-review"}
    for key in ("STACK_POSTGRES_PASSWORD", "STACK_MINIO_ROOT_PASSWORD", "MLFLOW_AUTH_ADMIN_PASSWORD",
                "MLFLOW_FLASK_SERVER_SECRET_KEY", "STACK_MODEL_API_MLFLOW_PASSWORD", "MODEL_API_ADMIN_TOKEN",
                "STACK_APP_POSTGRES_PASSWORD"):
        environment[key] = secrets.token_urlsafe(32)
    environment.update(STACK_MINIO_ROOT_USER="s" + secrets.token_hex(8),
                       MLFLOW_AUTH_ADMIN_USERNAME="a" + secrets.token_hex(8),
                       STACK_MODEL_API_MLFLOW_USERNAME="r" + secrets.token_hex(8))
    return environment


def http_json(url, method="GET", payload=None, opener=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Origin": url.split("/", 3)[0] + "//" + url.split("/", 3)[2]}
    if data:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, method=method, headers=headers)
    try:
        response = (opener or build_opener(ProxyHandler({}))).open(request, timeout=10)
        status, body = response.status, response.read(HTTP_BYTES_MAX + 1)
    except HTTPError as error:
        status, body = error.code, error.read(HTTP_BYTES_MAX + 1)
    except (URLError, TimeoutError, OSError):
        raise Failure("local_http_unavailable") from None
    require(len(body) <= HTTP_BYTES_MAX, "http_response_too_large")
    try:
        value = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise Failure("invalid_http_json") from None
    require(isinstance(value, dict), "invalid_http_json")
    return status, value


def model_smoke_module():
    """Load the already-reviewed STACK-02 fixture protocol without executing it."""
    source = ROOT / "scripts" / "stack_model_api_smoke.py"
    spec = importlib.util.spec_from_file_location("stack08_model_fixture", source)
    require(spec is not None and spec.loader is not None, "model_fixture_protocol_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bounded_child_receipt(command, environment, payload, parser, diagnostic=None):
    """Read one bounded protocol receipt; only validated diagnostic stages reach evidence."""
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as output:
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, input=json.dumps(payload).encode("utf-8"),
                                    stdout=output, stderr=subprocess.DEVNULL, check=False, timeout=150)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("fixture_child_unavailable_or_timed_out") from None
        output.seek(0)
        raw = output.read(65537)
    require(len(raw) <= 65536, "fixture_receipt_too_large")
    if result.returncode != 0:
        detail = diagnostic(raw.decode("utf-8", errors="replace")) if diagnostic is not None else None
        if isinstance(detail, dict) and isinstance(detail.get("stage"), str):
            raise Failure(f"fixture_child_failed_at_{detail['stage']}")
        raise Failure("fixture_child_failed_output_suppressed")
    try:
        return parser(raw.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError):
        raise Failure("fixture_receipt_invalid") from None


def anchor_diagnostic(raw):
    """Accept only the fixed, secret-free failure diagnostic emitted by the App CLI."""
    match = re.fullmatch(
        r"EVIDENCE_ANCHOR_DIAGNOSTIC phase=(input|database|execute|cleanup) "
        r"operation=(anchor|reconcile|command) "
        r"reason=(usage|authentication|configuration|database|data|operation|cleanup) outcome=failed\n?",
        raw,
    )
    return "_".join(match.groups()) if match else None


def darwin_process_group_member_states(process_group):
    """Read only one Darwin process group's member states through libproc."""
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        raise Failure("local_child_cleanup_failed_closed") from None
    list_pids = library.proc_listpids
    list_pids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
    list_pids.restype = ctypes.c_int
    pid_info = library.proc_pidinfo
    pid_info.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    pid_info.restype = ctypes.c_int
    buffer_size = 4096
    buffer = (ctypes.c_int * (buffer_size // ctypes.sizeof(ctypes.c_int)))()
    ctypes.set_errno(0)
    count = list_pids(2, process_group, buffer, buffer_size)  # PROC_PGRP_ONLY
    if count < 0 or count == buffer_size or (count == 0 and ctypes.get_errno() != 0):
        raise Failure("local_child_cleanup_failed_closed")
    states = []
    for index in range(count // ctypes.sizeof(ctypes.c_int)):
        info = ctypes.create_string_buffer(1024)
        ctypes.set_errno(0)
        size = pid_info(buffer[index], 3, 0, info, len(info))  # PROC_PIDTBSDINFO
        if size == 0:
            if ctypes.get_errno() in {3, 2}:  # ESRCH, ENOENT: member exited after listing.
                continue
            raise Failure("local_child_cleanup_failed_closed")
        if size < 8:
            raise Failure("local_child_cleanup_failed_closed")
        status = ctypes.c_uint32.from_buffer(info, 4).value  # proc_bsdinfo.pbi_status
        states.append("zombie" if status == 5 else "running")  # SZOMB
    return tuple(states)


def linux_process_group_member_states(process_group):
    """Read bounded `/proc` state records and retain only the exact process group."""
    states = []
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        raise Failure("local_child_cleanup_failed_closed") from None
    if len(entries) > 65536:
        raise Failure("local_child_cleanup_failed_closed")
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            stat = (Path("/proc") / entry.name / "stat").read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            raise Failure("local_child_cleanup_failed_closed") from None
        try:
            fields = stat.rsplit(")", 1)[1].split()
            state, group = fields[0], int(fields[2])
        except (IndexError, ValueError):
            raise Failure("local_child_cleanup_failed_closed") from None
        if group == process_group:
            states.append("zombie" if state == "Z" else "running")
    return tuple(states)


def process_group_member_states(process_group):
    """Classify only members of the process group's exact PGID; unknown platforms fail closed."""
    if platform.system() == "Darwin":
        return darwin_process_group_member_states(process_group)
    if platform.system() == "Linux":
        return linux_process_group_member_states(process_group)
    raise Failure("local_child_cleanup_failed_closed")


def process_group_is_clean(process_group):
    """Accept an absent group or a group whose only remaining members are zombies."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        raise Failure("local_child_cleanup_failed_closed") from None
    return all(state == "zombie" for state in process_group_member_states(process_group))


def wait_for_process_group_removal(process_group, timeout):
    deadline = time.monotonic() + timeout
    while not process_group_is_clean(process_group):
        if time.monotonic() >= deadline:
            raise Failure("local_child_cleanup_failed_closed")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def terminate_child_process_group(process):
    """Terminate, verify, and reap only the isolated process group for one child."""
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise Failure("local_child_cleanup_failed_closed") from None
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if process_group_is_clean(process_group):
        return
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise Failure("local_child_cleanup_failed_closed") from None
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("local_child_cleanup_failed_closed") from None
    wait_for_process_group_removal(process_group, timeout=5)


def bounded_json_child(command, environment, diagnostic=None, limit=4096, timeout=150):
    """Execute a local child and accept exactly one bounded JSON object."""
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as output, \
         tempfile.NamedTemporaryFile(mode="w+b", delete=True) as error_output:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=error_output, start_new_session=True)
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_child_process_group(process)
            raise Failure("local_child_unavailable_or_timed_out") from None
        except OSError:
            raise Failure("local_child_unavailable_or_timed_out") from None
        error_output.seek(0)
        error_raw = error_output.read(513)
        output.seek(0)
        raw = output.read(limit + 1)
    if result != 0:
        detail = (diagnostic(error_raw.decode("utf-8", errors="replace"))
                  if len(error_raw) <= 512 and diagnostic is not None else None)
        if isinstance(detail, str) and re.fullmatch(r"[a-z_]{1,72}", detail):
            raise Failure(f"local_child_failed_at_{detail}")
        raise Failure("local_child_failed_output_suppressed")
    require(len(raw) <= limit, "local_child_output_too_large")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError):
        raise Failure("local_child_invalid_json") from None
    require(isinstance(value, dict), "local_child_invalid_json")
    return value


def deployment_manifest():
    """Read only the Stack-04 manifest fields needed by the App anchor command."""
    path = CONTRACT_PROOF / "deployments" / "local.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise Failure("contract_deployment_manifest_missing") from None
    address = value.get("address") if isinstance(value, dict) else None
    require(isinstance(address, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", address),
            "contract_deployment_manifest_invalid")
    return address.lower()


def remove_contract_proof():
    """Remove only the exact Stack-04 generated proof root after its owner check."""
    if not CONTRACT_PROOF.exists():
        return
    marker = CONTRACT_PROOF / ".stack04-proof-owner"
    require(CONTRACT_PROOF.is_dir() and not CONTRACT_PROOF.is_symlink() and marker.is_file()
            and not marker.is_symlink() and marker.read_text(encoding="utf-8") == "stack04-proof-invoiceops-contracts\n",
            "contract_proof_ownership_changed")
    shutil.rmtree(CONTRACT_PROOF)
    require(not CONTRACT_PROOF.exists(), "contract_proof_cleanup_failed")


def listed_owned_resources(project, environment):
    """Discover only exact Compose-labelled resource identities for fail-closed cleanup."""
    validate_project(project)
    commands = {
        "containers": docker_command("container", "ls", "-aq", "--filter", label_filter(project)),
        "networks": docker_command("network", "ls", "-q", "--filter", label_filter(project)),
        "volumes": docker_command("volume", "ls", "-q", "--filter", label_filter(project)),
    }
    resources = {}
    for kind, command in commands.items():
        identities = bounded_docker_output(command, environment).splitlines()
        require(len(identities) == len(set(identities)), "duplicate_owned_resource")
        require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity) for identity in identities),
                "invalid_owned_resource_identity")
        if identities:
            resources[kind] = identities
    return resources


def safe_receipt(project, status, phases, failure=None):
    validate_project(project)
    receipt = {"schema": RECEIPT_SCHEMA, "project": project, "status": status,
               "phases": phases, "output_bounds": {"http_response_bytes_max": HTTP_BYTES_MAX,
               "receipt_bytes_max": RECEIPT_BYTES_MAX, "child_output": "NOT_RETAINED"}}
    if failure is not None:
        require(re.fullmatch(r"[a-z0-9_]{1,80}", failure), "invalid_failure_code")
        receipt["failure"] = failure
    require(len(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()) <= RECEIPT_BYTES_MAX,
            "receipt_exceeds_output_bound")
    return receipt


def write_receipt(path, receipt):
    encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(len(encoded) <= RECEIPT_BYTES_MAX, "receipt_exceeds_output_bound")
    path.write_bytes(encoded)
    path.chmod(0o600)


class Harness:
    def __init__(self, project, ports, receipt_path, allow_network_build=False, notebook_champion=False):
        self.project, self.ports, self.receipt_path = project, ports, receipt_path
        self.allow_network_build, self.notebook_champion = allow_network_build, notebook_champion
        self.environment = generated_environment(*ports)
        if self.notebook_champion:
            self.environment["MODEL_API_ARTIFACT_DOWNLOAD_MODE"] = "proxy-http"
        self.started = False
        self.phases = []
        self.owned = {}
        self.context = None
        self.contract_address = None

    def phase(self, name):
        self.phases.append(name)

    def preflight(self):
        if not self.allow_network_build:
            offline_build_prerequisites(self.environment)
        for service in BUILD_SERVICES:
            require(not local_image_available(f"{self.project}-{service}:latest", self.environment), "preexisting_project_image")

    def build(self):
        # Default Compose behaviour does not pull; cache preflight above is the guard.
        suppressed(compose_command(self.project, "build", *BUILD_SERVICES), self.environment, timeout=900)

    def up(self, *services, wait=None):
        command = ["up", "-d", "--no-build", "--pull", "never"]
        if wait is not None:
            command.extend(("--wait", "--wait-timeout", str(wait)))
        suppressed(compose_command(self.project, *command, *services), self.environment,
                   timeout=(wait + 30 if wait is not None else 90))

    def model_fixture(self):
        module = model_smoke_module()
        common = dict(self.environment)
        common.update(MLFLOW_HTTP_REQUEST_TIMEOUT="10", MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
                      MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR="0", MLFLOW_DISABLE_TELEMETRY="true")

        def action(service, tracking_uri, name, payload):
            env = dict(common, MLFLOW_TRACKING_URI=tracking_uri,
                       MLFLOW_TRACKING_USERNAME=self.environment["MLFLOW_AUTH_ADMIN_USERNAME"],
                       MLFLOW_TRACKING_PASSWORD=self.environment["MLFLOW_AUTH_ADMIN_PASSWORD"])
            keys = [key for key in env if key.startswith("MLFLOW_TRACKING_") or key.startswith("MLFLOW_HTTP_")]
            keys.append("MLFLOW_DISABLE_TELEMETRY")
            if name == "bootstrap":
                keys.extend(("STACK_MODEL_API_MLFLOW_USERNAME", "STACK_MODEL_API_MLFLOW_PASSWORD"))
            command = docker_command("exec", "-i", *sum((["-e", key] for key in keys), []),
                                     f"{self.project}-{service}-1", "python", "-B", "-c", module.SDK, name)
            return bounded_child_receipt(command, env, payload, module.receipt,
                                         getattr(module, "reader_diagnostic", None))

        bootstrap = action("mlflow", "http://127.0.0.1:5000", "bootstrap", {"project": self.project})
        require(isinstance(bootstrap.get("experiment"), str), "model_bootstrap_failed")
        fixture_environment = dict(common,
                                   MLFLOW_TRACKING_USERNAME=self.environment["MLFLOW_AUTH_ADMIN_USERNAME"],
                                   MLFLOW_TRACKING_PASSWORD=self.environment["MLFLOW_AUTH_ADMIN_PASSWORD"])
        fixture = bounded_child_receipt(
            compose_command(self.project, "run", "--rm", "--no-deps", "--entrypoint", "python",
                            "-e", "MLFLOW_TRACKING_USERNAME", "-e", "MLFLOW_TRACKING_PASSWORD",
                            "model-api", "-B", "-c", module.SDK, "fixture"),
            fixture_environment, {"project": self.project, "experiment": bootstrap["experiment"]}, module.receipt,
            getattr(module, "reader_diagnostic", None),
        )
        require(isinstance(fixture.get("run_id"), str) and isinstance(fixture.get("version"), str),
                "model_fixture_failed")
        return module.Harness.predict_payload()

    def notebook_run_champion(self):
        """Run the canonical HGB notebook and promote only its structured MLflow run."""
        require(NOTEBOOK_PYTHON.is_file() and os.access(NOTEBOOK_PYTHON, os.X_OK), "notebook_python_missing")
        require(NOTEBOOK_HGB.is_file(), "notebook_hgb_missing")
        notebook_environment = dict(self.environment)
        notebook_environment.update(
            MLFLOW_TRACKING_URI=f"http://127.0.0.1:{self.ports[0]}",
            MLFLOW_REGISTRY_URI=f"http://127.0.0.1:{self.ports[0]}",
            MLFLOW_TRACKING_USERNAME=self.environment["MLFLOW_AUTH_ADMIN_USERNAME"],
            MLFLOW_TRACKING_PASSWORD=self.environment["MLFLOW_AUTH_ADMIN_PASSWORD"],
            INVOICEOPS_ORGANIZATION_SLUG="app-07-local-demonstration",
            INVOICEOPS_OWNER_TYPE="user",
            INVOICEOPS_OWNER_ID="ef14197c-8f5b-4aef-8fa7-310e4da998b7",
            INVOICEOPS_CREATED_BY_RUT="111111111",
            STACK_NOTEBOOK_ROOT=str(NOTEBOOKS),
            STACK_NOTEBOOK_HGB=str(NOTEBOOK_HGB),
        )
        notebook = bounded_json_child([str(NOTEBOOK_PYTHON), "-B", "-c", NOTEBOOK_RUN_RECEIPT],
                                      notebook_environment, limit=512)
        run_id = notebook.get("run_id")
        require(isinstance(run_id, str) and run_id.strip(), "notebook_run_missing")
        promotion = bounded_json_child(
            [str(NOTEBOOK_PYTHON), "-B", "-m", "invoiceops_ml.local_promotion",
             "--candidate-run-id", run_id],
            notebook_environment,
            limit=1024,
        )
        require(promotion.get("run_id") == run_id and promotion.get("model") == CHAMPION_MODEL
                and promotion.get("alias") == "champion", "champion_promotion_invalid")
        version = promotion.get("version")
        require(isinstance(version, str) and version.strip(), "champion_promotion_invalid")
        require(isinstance(promotion.get("gate_metrics"), dict), "notebook_gate_not_pass")
        return {"run_id": run_id, "version": version, "gate": "PASS"}

    def provision_notebook_reader(self):
        """Create and validate the Model API reader for the notebook's exact experiment."""
        experiment = "student/111111111/invoice-risk"
        environment = dict(self.environment,
                           MLFLOW_TRACKING_URI="http://127.0.0.1:5000",
                           MLFLOW_TRACKING_USERNAME=self.environment["MLFLOW_AUTH_ADMIN_USERNAME"],
                           MLFLOW_TRACKING_PASSWORD=self.environment["MLFLOW_AUTH_ADMIN_PASSWORD"],
                           STACK_NOTEBOOK_EXPERIMENT=experiment)
        keys = ("MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD",
                "STACK_MODEL_API_MLFLOW_USERNAME", "STACK_MODEL_API_MLFLOW_PASSWORD",
                "STACK_NOTEBOOK_EXPERIMENT")
        command = docker_command("exec", "-i", *sum((["-e", key] for key in keys), []),
                                 f"{self.project}-mlflow-1", "python", "-B", "-c", NOTEBOOK_READER_RECEIPT)
        receipt = bounded_json_child(command, environment, limit=512)
        require(receipt == {"experiment": experiment, "reader_nonadmin": True, "exact_read_role": True},
                "notebook_reader_invalid")
        return receipt

    @staticmethod
    def validated_notebook_champion(candidate):
        require(isinstance(candidate, dict), "notebook_champion_invalid")
        run_id, version, gate = candidate.get("run_id"), candidate.get("version"), candidate.get("gate")
        require(isinstance(run_id, str) and run_id.strip(), "notebook_run_missing")
        require(gate == "PASS", "notebook_gate_not_pass")
        require(isinstance(version, str) and version.strip(), "champion_promotion_invalid")
        return run_id, version

    def deploy_contract(self):
        require(not CONTRACT_PROOF.exists(), "preexisting_contract_proof")
        rpc_url = f"http://127.0.0.1:{self.ports[4]}"
        command = ["python3", "-B", str(DEPLOY), "--execute", "--rpc-url", rpc_url,
                   "--deployer", ANVIL_DEPLOYER, "--signer", ANVIL_DEPLOYER]
        suppressed(command, self.environment, timeout=150)
        self.contract_address = deployment_manifest()

    def bootstrap_app(self):
        suppressed(compose_command(self.project, "--profile", "stack03-bootstrap", "run", "--rm", "--no-deps",
                                  "app-bootstrap"), self.environment, timeout=180)

    def prediction_context(self, champion=None):
        """Create and validate one model-backed decision before evidence is created."""
        origin = f"http://127.0.0.1:{self.ports[2]}"
        jar = CookieJar()
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
        status, login = http_json(origin + "/auth/login", "POST",
                                  {"rut": "111111111", "password": "app-07-local-demonstration"}, opener)
        require(status == 200 and isinstance(login.get("user"), dict) and len(jar) == 1, "app_login_failed")
        status, profile = http_json(origin + "/profile", opener=opener)
        memberships = profile.get("profile", {}).get("memberships") if isinstance(profile.get("profile"), dict) else None
        organization = memberships[0].get("organization") if isinstance(memberships, list) and memberships else None
        user = profile.get("profile") if isinstance(profile.get("profile"), dict) else None
        require(status == 200 and isinstance(organization, dict) and isinstance(organization.get("id"), str)
                and isinstance(user, dict) and isinstance(user.get("id"), str), "app_context_missing")
        self.context = {"organizationId": organization["id"], "ownerType": "user", "ownerId": user["id"]}
        query = "?" + "&".join(f"{key}={value}" for key, value in self.context.items())
        status, page = http_json(origin + "/invoices" + query, opener=opener)
        invoices = page.get("invoices")
        invoice = invoices[0].get("invoiceId") if isinstance(invoices, list) and invoices and isinstance(invoices[0], dict) else None
        require(status == 200 and isinstance(invoice, str), "app_invoice_missing")
        status, decision = http_json(origin + f"/invoices/{invoice}/decision" + query, "POST",
                                     {"mode": "PROBABILITY_POLICY", "policyVersion": "ml-policy-v1"}, opener)
        event = decision.get("auditEvent")
        require(status == 200 and isinstance(event, dict) and isinstance(event.get("correlationId"), str),
                "prediction_evidence_missing")
        model_api_lineage = (event.get("policyProbabilitySource") == "MODEL_API"
                             and event.get("modelId") == "invoice-review"
                             and all(isinstance(event.get(key), str)
                                     for key in ("modelVersion", "modelRunId")))
        if champion is not None:
            run_id, version = champion
            require(event.get("modelRunId") == run_id and event.get("modelVersion") == version,
                    "prediction_champion_lineage_mismatch")
        require(model_api_lineage, "prediction_model_lineage_missing")
        token = next(iter(jar)).value
        self.environment.update(APP_SESSION_TOKEN=token, STACK08_CORRELATION_ID=event["correlationId"],
                                 STACK08_ORGANIZATION_ID=self.context["organizationId"], STACK08_OWNER_ID=self.context["ownerId"])
        return event

    def prediction_evidence(self, champion=None, context_ready=False):
        """Create one validated model-backed decision and its evidence batch through App ownership."""
        if not context_ready:
            self.prediction_context(champion)
        source = """const {createDatabase}=require('./dist/database.js'); const {createAuthService}=require('./dist/auth.js'); const {createEvidencePersistence}=require('./dist/evidence-persistence.js'); (async()=>{const d=createDatabase(process.env.DATABASE_URL);try{const a=await createAuthService(d.prisma,'open').getProfile(process.env.APP_SESSION_TOKEN);const r=await d.prisma.evidenceRecord.findFirst({where:{decisionEvent:{correlationId:process.env.STACK08_CORRELATION_ID}},select:{id:true}});if(!r)throw Error('record');const c={organizationId:process.env.STACK08_ORGANIZATION_ID,ownerType:'user',ownerId:process.env.STACK08_OWNER_ID};const b=await createEvidencePersistence(d.prisma).createBatch(a,c,[r.id]);console.log(JSON.stringify({batchId:b.id,rootHash:b.rootHash}));}finally{await d.prisma.$disconnect();}})().catch(()=>process.exitCode=1);"""
        command = docker_command("exec", "-i", "-e", "APP_SESSION_TOKEN", "-e", "STACK08_CORRELATION_ID",
                                 "-e", "STACK08_ORGANIZATION_ID", "-e", "STACK08_OWNER_ID",
                                 f"{self.project}-backend-1", "node", "-e", source)
        batch = bounded_json_child(command, self.environment)
        require(isinstance(batch.get("batchId"), str) and isinstance(batch.get("rootHash"), str)
                and re.fullmatch(r"0x[0-9a-f]{64}", batch["rootHash"]), "evidence_batch_invalid")
        return batch

    def anchor_and_reconcile(self, batch):
        require(self.context is not None and self.contract_address is not None, "anchor_precondition_missing")
        arguments = ["--batch-id", batch["batchId"], "--organization-id", self.context["organizationId"],
                     "--owner-type", "user", "--owner-id", self.context["ownerId"]]
        self.environment.update(EVIDENCE_CONTRACT_ADDRESS=self.contract_address,
                                EVM_SIGNER_PRIVATE_KEY=ANVIL_DEVELOPMENT_KEY)
        command = docker_command("exec", "-i", "-e", "APP_SESSION_TOKEN", "-e", "EVIDENCE_CONTRACT_ADDRESS",
                                 "-e", "EVM_SIGNER_PRIVATE_KEY", f"{self.project}-backend-1", "node",
                                 "dist/evidence-anchor-cli.js", "anchor", *arguments)
        anchor = bounded_json_child(command, self.environment, anchor_diagnostic)
        require(anchor.get("action") == "anchor" and anchor.get("batchId") == batch["batchId"]
                and anchor.get("rootHash") == batch["rootHash"], "anchor_result_invalid")
        lookup = """const {createDatabase}=require('./dist/database.js'); (async()=>{const d=createDatabase(process.env.DATABASE_URL);try{const r=await d.prisma.evidenceAnchorReservation.findFirst({where:{batchId:process.env.STACK08_BATCH_ID},orderBy:{createdAt:'desc'},select:{id:true}});if(!r)throw Error('reservation');console.log(JSON.stringify({reservationId:r.id}));}finally{await d.prisma.$disconnect();}})().catch(()=>process.exitCode=1);"""
        self.environment["STACK08_BATCH_ID"] = batch["batchId"]
        reservation = bounded_json_child(docker_command("exec", "-i", "-e", "STACK08_BATCH_ID",
                                         f"{self.project}-backend-1", "node", "-e", lookup), self.environment)
        require(isinstance(reservation.get("reservationId"), str), "anchor_reservation_missing")
        reconcile = bounded_json_child(docker_command("exec", "-i", "-e", "APP_SESSION_TOKEN",
                                        "-e", "EVIDENCE_CONTRACT_ADDRESS", f"{self.project}-backend-1", "node",
                                        "dist/evidence-anchor-cli.js", "reconcile", *arguments,
                                        "--reservation-id", reservation["reservationId"]), self.environment, anchor_diagnostic)
        require(reconcile.get("action") == "reconcile" and reconcile.get("batchId") == batch["batchId"]
                and reconcile.get("rootHash") == batch["rootHash"] and reconcile.get("receiptStatus") == "success",
                "reconciliation_result_invalid")

    def collect_owned(self):
        containers = [f"{self.project}-{service}-1" for service in (*BASE_SERVICES, "model-api", ANVIL_SERVICE, *APP_SERVICES)]
        resources = {"containers": containers, "networks": [f"{self.project}_default"],
                     "volumes": [f"{self.project}_postgres_data", f"{self.project}_minio_data",
                                 f"{self.project}_app_postgres_data"]}
        self.owned = verify_owned(self.project, resources, self.environment)

    def execute(self):
        self.phase("preflight"); self.preflight()
        self.phase("build"); self.started = True; self.build()
        if self.notebook_champion:
            self.phase("mlflow"); self.up(*BASE_SERVICES, wait=360)
            champion = self.validated_notebook_champion(self.notebook_run_champion())
            self.phase("notebook_reader"); self.provision_notebook_reader()
            payload = model_smoke_module().Harness.predict_payload()
        else:
            champion = None
            self.phase("anvil"); self.up(ANVIL_SERVICE, wait=60)
            self.phase("contract"); self.deploy_contract()
            self.phase("mlflow"); self.up(*BASE_SERVICES, wait=360)
            payload = self.model_fixture()
        self.phase("model_api"); self.up("model-api", wait=360)
        status, health = http_json(f"http://127.0.0.1:{self.ports[1]}/models/invoice-review/health")
        require(status == 200, "model_api_readiness_failed")
        if champion is not None:
            require(health.get("run_id") == champion[0] and health.get("model_version") == champion[1],
                    "model_api_health_champion_mismatch")
        self.phase("app_schema"); self.bootstrap_app()
        self.phase("app"); self.up(*APP_SERVICES, wait=120)
        status, _ = http_json(f"http://127.0.0.1:{self.ports[2]}/health/live")
        require(status == 200, "app_health_failed")
        self.phase("prediction")
        status, prediction = http_json(f"http://127.0.0.1:{self.ports[1]}/models/invoice-review/predict", "POST", payload)
        require(status == 200 and isinstance(prediction.get("probability"), (int, float)), "prediction_failed")
        if champion is not None:
            require(prediction.get("run_id") == champion[0] and prediction.get("model_version") == champion[1],
                    "model_api_prediction_champion_mismatch")
            self.prediction_context(champion)
            self.phase("anvil"); self.up(ANVIL_SERVICE, wait=60)
            self.phase("contract"); self.deploy_contract()
        self.phase("evidence")
        batch = self.prediction_evidence(context_ready=champion is not None)
        self.phase("anchor_reconcile")
        self.anchor_and_reconcile(batch)
        self.collect_owned()

    def cleanup(self):
        try:
            if self.started:
                owned = self.owned or listed_owned_resources(self.project, self.environment)
                if owned:
                    remove_owned_resources(self.project, owned, self.environment)
                remove_derived_images(self.project, self.environment)
        finally:
            remove_contract_proof()

    def finish(self):
        failure = None
        try:
            self.execute()
        except Failure as error:
            failure = str(error)
        finally:
            try:
                self.cleanup()
            except Failure:
                failure = failure or "cleanup_failed_closed"
        status = "PASS" if failure is None else "FAIL"
        write_receipt(self.receipt_path, safe_receipt(self.project, status, self.phases, failure))
        return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="authorize this local-only Docker operation")
    parser.add_argument("--allow-network-build", action="store_true",
                        help="allow this local build to fetch missing build inputs; no destination allowlist is enforced")
    parser.add_argument("--notebook-champion", action="store_true",
                        help="opt in to the canonical notebook-to-champion flow instead of the STACK-08 fixture")
    parser.add_argument("--project", required=True, help="new isolated stack08-proof-* project")
    parser.add_argument("--mlflow-port", type=int, default=18580)
    parser.add_argument("--api-port", type=int, default=18581)
    parser.add_argument("--backend-port", type=int, default=18582)
    parser.add_argument("--frontend-port", type=int, default=18583)
    parser.add_argument("--anvil-port", type=int, default=18584)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    validate_project(args.project)
    validate_receipt_path(args.receipt)
    if not args.execute:
        return 0
    ports = (args.mlflow_port, args.api_port, args.backend_port, args.frontend_port, args.anvil_port)
    require(all(isinstance(port, int) and 1024 <= port <= 65535 for port in ports) and len(set(ports)) == len(ports),
            "invalid_ports")
    return 0 if Harness(args.project, ports, args.receipt, args.allow_network_build,
                        args.notebook_champion).finish() == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        raise SystemExit(str(error))
