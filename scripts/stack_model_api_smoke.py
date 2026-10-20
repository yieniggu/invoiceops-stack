"""Opt-in STACK02 proof on a NEW local project; preparation never runs Docker.

Runtime consent includes builds/pulls, two loopback listeners, synthetic fitting,
exact reader-role creation, and removing/restoring this instance's champion alias.
No academic training, release eligibility, remote proof, volume/image deletion or
shared-instance operation is performed. A timeout does not prove cancellation.
"""

import argparse
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / "invoiceops-mlflow"
API = ROOT.parent / "invoiceops-model-api"
FILES = (BASE / "compose.yml", ROOT / "compose.yml", ROOT / "compose.model-api.yml")
LOCAL = "unix:///var/run/docker.sock"
DOCKER = "docker"
COMPOSE = "docker-compose"
# Local read-only tolerance; never extends mutation budgets or adds retries.
DOCKER_READ_TIMEOUT_SECONDS = 60
DOCKER_READ_OPERATIONS = frozenset({
    "inventory_containers", "inventory_networks", "inventory_volumes", "inventory_images",
    "inspect_containers", "inspect_networks", "inspect_volumes", "inspect_images", "compose_config",
})
# Explicit local allowance for the observed ~289s MLflow startup under contention.
BASE_START_SECONDS = 330
BASE_START_COMMAND_SECONDS = BASE_START_SECONDS + 30
# One local provider budget drives both positive and negative refresh consumers.
REFRESH_SECONDS = 120
STARTUP_SECONDS = 180
HTTP_REFRESH_SECONDS = REFRESH_SECONDS + 5
HTTP_CHILD_GRACE_SECONDS = 7
PROCESS_IMPORT_ALLOWANCE_SECONDS = 150
READINESS_SECONDS = PROCESS_IMPORT_ALLOWANCE_SECONDS + STARTUP_SECONDS + 1
SERVICES = {"postgres", "minio", "minio-init", "mlflow", "model-api"}
REQUIRED = {"preflight", "bootstrap", "missing_champion", "fixture", "objects",
            "reader", "release", "missing_alias", "restored", "minio_idempotency",
            "startup_champion"}
MARKER = "STACK02_RECEIPT="
# Full-context JSON evaluates unrelated formatter getters (including Size).
CONTAINER_INVENTORY_FORMAT = '{"ID":{{json .ID}},"Names":{{json .Names}},"Labels":{{json .Labels}},"Ports":{{json .Ports}}}'
SDK_DIAGNOSTIC_MARKER = "STACK02_DIAGNOSTIC="
SDK_PROGRESS_MARKER = "STACK02_PROGRESS="
SDK_STAGES = frozenset({"imports", "configure", "bootstrap", "fixture_imports", "fit", "save",
                        "untrusted_types", "reload", "classes", "probability_shape", "manifest",
                        "start_run", "log_artifacts", "run_status", "version_create", "alias_assignment",
                        "reader", "objects", "remove_alias", "restore_alias", "process_start", "stdin_decode",
                        "bootstrap_auth_import", "bootstrap_client", "bootstrap_workspace", "bootstrap_experiment",
                         "bootstrap_model", "bootstrap_user", "bootstrap_role", "bootstrap_permissions",
                         "s3_client", "s3_list", "s3_get_headers", "s3_body_read", "s3_manifest"})
READER_STAGES = frozenset({"read_experiment", "read_alias", "read_run", "download", "manifest",
                           "deny_create_experiment", "deny_create_model", "deny_experiment_tag",
                           "deny_model_update", "deny_run_create", "deny_alias_write", "deny_artifact_write"})
S3_EXCEPTION_KINDS = frozenset({"ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError",
                               "ClientError", "ConnectionClosedError", "HTTPClientError", "IncompleteReadError",
                               "ResponseStreamingError", "NoCredentialsError", "PartialCredentialsError"})
S3_ERROR_CODES = frozenset({"AccessDenied", "NoSuchKey", "NoSuchBucket", "RequestTimeout", "SlowDown",
                            "InternalError", "SignatureDoesNotMatch", "other"})
SDK_EXCEPTION_KINDS = frozenset({"MlflowException", "RestException", "HTTPError", "AssertionError", "other"}) | S3_EXCEPTION_KINDS
SDK_HTTP_STATUSES = frozenset({0, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504})
SDK_OPERATIONS = {action: "sdk_" + action for action in
                  ("bootstrap", "fixture", "reader", "objects", "remove_alias", "restore_alias")}
OPERATIONS = frozenset(SDK_OPERATIONS.values()) | {
    "command", "docker_query", "compose_query", "inventory_containers", "inventory_networks",
    "inventory_volumes", "inventory_images", "inspect_containers", "inspect_networks",
    "inspect_volumes", "inspect_images", "compose_config", "compose_build", "compose_base_start",
    "compose_api_start", "compose_api_restart", "compose_shutdown", "minio_restart", "http", "release",
}
spec = importlib.util.spec_from_file_location("stack02_shared", ROOT / "scripts/stack_ml_smoke.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

# STACK01's Harness, validators and receipts deliberately remain untouched: their
# four-service/two-layer/project invariants are incompatible with this proof.
# Only its side-effect-free child environment allowlist is reused.
SDK = r'''
print("STACK02_PROGRESS=process_start", flush=True)
import hashlib, json, os, sys, tempfile
from contextlib import contextmanager
from pathlib import Path
def progress(value):
    print("STACK02_PROGRESS=" + value, flush=True)
    return value
stage = progress("stdin_decode")
diagnostic_emitted = False
s3_exception_kinds = {"ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError", "ClientError",
                     "ConnectionClosedError", "HTTPClientError", "IncompleteReadError", "ResponseStreamingError",
                     "NoCredentialsError", "PartialCredentialsError"}
s3_error_codes = {"AccessDenied", "NoSuchKey", "NoSuchBucket", "RequestTimeout", "SlowDown",
                  "InternalError", "SignatureDoesNotMatch", "other"}
def safe_exception(kind, error, traceback):
    if diagnostic_emitted:
        return
    status = 0
    try:
        status = error.get_http_status_code()
    except Exception:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", 0)
        if isinstance(response, dict):
            metadata = response.get("ResponseMetadata")
            status = metadata.get("HTTPStatusCode", 0) if isinstance(metadata, dict) else 0
    if type(status) is not int or status not in {0, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504}:
        status = 0
    label = kind.__name__ if kind.__name__ in {"MlflowException", "RestException", "HTTPError", "AssertionError"} | s3_exception_kinds else "other"
    # Service-specific classes (for example S3 NoSuchKey) subclass ClientError.
    if any(base.__name__ == "ClientError" and base.__module__ == "botocore.exceptions"
           for base in kind.__mro__):
        label = "ClientError"
    line = 0
    while traceback is not None:
        if (traceback.tb_frame.f_code.co_filename == "<string>"
                and traceback.tb_frame.f_globals is globals()):
            line = traceback.tb_lineno
        traceback = traceback.tb_next
    diagnostic = {"stage": stage, "exception": label, "http_status": status, "source_line": line}
    if label in s3_exception_kinds:
        response = getattr(error, "response", None)
        details = response.get("Error") if isinstance(response, dict) else None
        code = details.get("Code") if isinstance(details, dict) else None
        diagnostic["error_code"] = code if isinstance(code, str) and code in s3_error_codes else "other"
    print("STACK02_DIAGNOSTIC=" + json.dumps(diagnostic), flush=True)
sys.excepthook = safe_exception
data = json.load(sys.stdin)
stage = progress("imports")
action = sys.argv[1]
if action != "objects":
    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException, RestException
    from requests.exceptions import HTTPError
@contextmanager
def reader_step(stage):
    global diagnostic_emitted
    progress(stage)
    try:
        yield
    except Exception as error:
        status, kind = 0, "other"
        if isinstance(error, MlflowException):
            status = error.get_http_status_code()
            kind = "RestException" if isinstance(error, RestException) else "MlflowException"
        elif isinstance(error, HTTPError):
            status = error.response.status_code if error.response is not None else 0
            kind = "HTTPError"
        elif isinstance(error, AssertionError):
            kind = "AssertionError"
        if type(status) is not int or status not in {0, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504}:
            status = 0
        print("STACK02_DIAGNOSTIC=" + json.dumps({"stage": stage, "exception": kind, "http_status": status}), flush=True)
        diagnostic_emitted = True
        raise
stage = progress("configure")
assert "MLFLOW_WORKSPACE" not in os.environ
if action != "objects":
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    client = MlflowClient()
name = "invoice-review-production"
result = {"workspace": "default"}
if action != "objects":
    result["sdk"] = mlflow.__version__
if action == "bootstrap":
    stage = progress("bootstrap_auth_import")
    from mlflow.server import get_app_client
    stage = progress("bootstrap_client")
    auth = get_app_client("basic-auth", tracking_uri=mlflow.get_tracking_uri())
    stage = progress("bootstrap_workspace")
    assert mlflow.get_workspace("default").name == "default"
    stage = progress("bootstrap_experiment")
    experiment = client.create_experiment(data["project"])
    stage = progress("bootstrap_model")
    client.create_registered_model(name)
    stage = progress("bootstrap_user")
    user = auth.create_user(os.environ["STACK_MODEL_API_MLFLOW_USERNAME"],
                            os.environ["STACK_MODEL_API_MLFLOW_PASSWORD"])
    assert user.is_admin is False
    stage = progress("bootstrap_role")
    role = auth.create_role(workspace="default", name=data["project"] + "-reader")
    stage = progress("bootstrap_permissions")
    expected = {("experiment", experiment, "READ"), ("registered_model", name, "READ")}
    for kind, pattern, permission in sorted(expected):
        auth.add_role_permission(role.id, kind, pattern, permission)
    auth.assign_role(user.username, role.id)
    assert {(p.resource_type, p.resource_pattern, p.permission)
            for p in auth.list_role_permissions(role.id)} == expected
    assert [r.id for r in auth.list_user_roles(user.username)] == [role.id]
    assert auth.get_user(user.username).is_admin is False
    result.update(experiment=experiment, reader_nonadmin=True, exact_read_role=True)
elif action == "fixture":
    stage = progress("fixture_imports")
    from importlib.metadata import version as package_version
    import mlflow.sklearn
    import pandas as pd
    import skops.io as sio
    from mlflow.models import infer_signature
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder
    rows = pd.DataFrame({
        "invoice_amount_cents": [125000 + i * 1000 for i in range(32)],
        "vendor_tenure_days": [365 + i for i in range(32)],
        "previous_incidents_12m": [i % 2 for i in range(32)],
        "amount_vs_vendor_median": [1.1] * 32,
        "has_purchase_order": [True] * 32, "three_way_match": [True] * 32,
        "bank_account_recently_changed": [False] * 32,
        "country_risk": [("low", "medium", "high")[i % 3] for i in range(32)]})
    model = Pipeline([("encode", ColumnTransformer([
        ("numeric", "passthrough", [0, 1, 2, 3]),
        ("flags", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [4, 5, 6]),
        ("risk", OrdinalEncoder(), [7])])),
        ("classifier", HistGradientBoostingClassifier(min_samples_leaf=2, random_state=0))])
    stage = progress("fit")
    model.fit(rows, [i % 2 for i in range(32)])
    trusted = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]
    with tempfile.TemporaryDirectory(prefix="stack02-model-") as directory:
        target = Path(directory) / "model"
        stage = progress("save")
        mlflow.sklearn.save_model(model, str(target), serialization_format="skops",
            # This fixed synthetic pipeline has known dependencies. Avoid spawning
            # another model-loading process merely to rediscover them.
            pip_requirements=[f"{package}=={package_version(package)}" for package in
                              ("numpy", "scipy", "pandas", "scikit-learn", "skops")],
            skops_trusted_types=trusted, signature=infer_signature(rows, model.predict(rows)))
        stage = progress("untrusted_types")
        assert sio.get_untrusted_types(file=target / "model.skops") == trusted
        stage = progress("reload")
        restored = mlflow.sklearn.load_model(str(target))
        stage = progress("classes")
        assert list(restored.classes_) == [0, 1]
        stage = progress("probability_shape")
        assert len(restored.predict_proba([rows.iloc[0].tolist()])[0]) == 2
        stage = progress("manifest")
        manifest = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in target.rglob("*") if p.is_file()}
        stage = progress("start_run")
        with mlflow.start_run(experiment_id=data["experiment"]) as run:
            stage = progress("log_artifacts")
            mlflow.log_artifacts(str(target), "model")
        rid = run.info.run_id
    stage = progress("run_status")
    info = client.get_run(rid).info
    assert info.status == "FINISHED" and info.artifact_uri.startswith("mlflow-artifacts:/")
    stage = progress("version_create")
    version = client.create_model_version(name, info.artifact_uri + "/model", run_id=rid)
    assert version.status == "READY" and version.run_id == rid
    stage = progress("alias_assignment")
    client.set_registered_model_alias(name, "champion", version.version)
    result.update(run_id=rid, version=version.version, manifest=manifest,
                  artifact_uri=info.artifact_uri, status=info.status)
elif action == "reader":
    stage = progress("reader")
    with reader_step("read_experiment"):
        assert client.get_experiment(data["experiment"]).experiment_id == data["experiment"]
    with reader_step("read_alias"):
        version = client.get_model_version_by_alias(name, "champion")
        assert version.run_id == data["run_id"] and version.version == data["version"]
    with reader_step("read_run"):
        assert client.get_run(data["run_id"]).info.status == "FINISHED"
    with tempfile.TemporaryDirectory(prefix="stack02-reader-") as directory:
        with reader_step("download"):
            downloaded = Path(client.download_artifacts(data["run_id"], "model", directory))
        with reader_step("manifest"):
            actual = {p.relative_to(downloaded).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in downloaded.rglob("*") if p.is_file()}
            assert actual == data["manifest"]
    def denied(call):
        try:
            call()
        except MlflowException as error:
            # Basic-auth plain-text 403 becomes MlflowException, not RestException.
            assert error.get_http_status_code() == 403
        else:
            raise AssertionError("write_not_denied")
    def artifact_denied(call):
        try:
            call()
        except HTTPError as error:
            if error.response is None or error.response.status_code != 403:
                raise AssertionError("artifact_write_not_denied") from None
        else:
            raise AssertionError("artifact_write_not_denied")
    with reader_step("deny_create_experiment"):
        denied(lambda: client.create_experiment(data["project"] + "-denied"))
    with reader_step("deny_create_model"):
        denied(lambda: client.create_registered_model(data["project"] + "-denied"))
    with reader_step("deny_experiment_tag"):
        denied(lambda: client.set_experiment_tag(data["experiment"], "stack02.denied", "true"))
    with reader_step("deny_model_update"):
        denied(lambda: client.update_registered_model(name, "stack02-denied"))
    with reader_step("deny_run_create"):
        denied(lambda: client.create_run(data["experiment"]))
    with reader_step("deny_alias_write"):
        denied(lambda: client.set_registered_model_alias(name, "stack02-denied", data["version"]))
    with tempfile.TemporaryDirectory(prefix="stack02-denied-") as directory:
        source = Path(directory) / "denied.txt"
        source.write_bytes(b"STACK02 denied artifact write\n")
        with reader_step("deny_artifact_write"):
            artifact_denied(lambda: client.log_artifact(data["run_id"], str(source), "stack02-denied"))
    result.update(download_hashes=True, reads=True, creates_denied=True, writes_denied=True)
elif action in {"remove_alias", "restore_alias"}:
    stage = progress(action)
    assert client.get_model_version(name, data["version"]).run_id == data["run_id"]
    if action == "remove_alias":
        assert client.get_model_version_by_alias(name, "champion").version == data["version"]
        client.delete_registered_model_alias(name, "champion")
    else:
        client.set_registered_model_alias(name, "champion", data["version"])
        assert client.get_model_version_by_alias(name, "champion").run_id == data["run_id"]
    result["alias_action"] = action
elif action == "objects":
    stage = progress("objects")
    import boto3
    from importlib.metadata import version as package_version
    from urllib.parse import urlsplit
    # Direct S3 reads, NOT a second local download through MLflow.
    stage = progress("s3_client")
    s3 = boto3.client("s3", endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"],
                      config=__import__("botocore.config", fromlist=["Config"]).Config(
                          connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}))
    prefix = urlsplit(data["artifact_uri"]).path.lstrip("/") + "/model/"
    assert data["run_id"] in prefix and ".." not in prefix.split("/")
    stage = progress("s3_list")
    listing = s3.list_objects_v2(Bucket="mlflow-artifacts", Prefix=prefix, MaxKeys=64)
    assert not listing.get("IsTruncated")
    actual = {}
    for entry in listing.get("Contents", []):
        key = entry["Key"]
        stage = progress("s3_get_headers")
        body = s3.get_object(Bucket="mlflow-artifacts", Key=key)["Body"]
        try:
            stage = progress("s3_body_read")
            payload = body.read(8 * 1024 * 1024 + 1)
        finally:
            body.close()
        assert len(payload) <= 8 * 1024 * 1024
        actual[key[len(prefix):]] = hashlib.sha256(payload).hexdigest()
    stage = progress("s3_manifest")
    assert actual == data["manifest"]
    result.update(manifest=actual, transport="direct_s3", boto3=boto3.__version__,
                  mlflow_installed=package_version("mlflow"))
else:
    raise AssertionError("unknown_action")
print("STACK02_RECEIPT=" + json.dumps(result, sort_keys=True))
'''

HTTP = r'''
import json, sys
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request
data = json.load(sys.stdin)
body = json.dumps(data["payload"]).encode() if data.get("payload") is not None else None
request = Request(data["url"], data=body, method=data["method"], headers=data["headers"])
try:
    with build_opener(ProxyHandler({})).open(request, timeout=data["timeout_seconds"]) as response:
        payload = response.read(65537)
        assert len(payload) <= 65536
        result = {"status": response.status, "body": json.loads(payload)}
except HTTPError as error:
    result = {"status": error.code, "body": {}}
    error.close()
except (URLError, OSError):
    # Includes connect/read TimeoutError and socket.timeout. Never expose details
    # or assert that the provider cancelled an already submitted operation.
    result = {"status": 0, "body": {}, "transport": "indeterminate"}
print("STACK02_RECEIPT=" + json.dumps(result))
'''


class Failure(Exception):
    """Only harness-defined constant error codes may be exposed."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def trusted_executable(override, name):
    """Resolve installed executables only; never query contexts or credentials."""
    if override is not None:
        candidates = [Path(override)]
        require(candidates[0].is_absolute(), "invalid_executable_path")
    else:
        found = shutil.which(name)
        candidates = [Path(found)] if found else []
        # Known installation locations, not a recursive HOME search.
        candidates += [Path("/Applications/Docker.app/Contents/Resources") /
                       ("bin/docker" if name == DOCKER else "cli-plugins/docker-compose")]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    raise Failure("blocked_environment_executable_unavailable")


def isolated_cli_environment(directory, compose_binary):
    """Private HOME and anonymous CLI configuration, independent of STACK01."""
    directory = Path(directory)
    config = {"auths": {"https://index.docker.io/v1/": {}}, "credsStore": "", "credHelpers": {},
              "cliPluginsExtraDirs": [str(Path(compose_binary).parent)]}
    with (directory / "config.json").open("x", encoding="utf-8") as output:
        json.dump(config, output)
    environment = shared.child_environment()
    environment.update(HOME=str(directory), DOCKER_CONFIG=str(directory),
                       BUILDX_CONFIG=str(directory / "buildx"), COMPOSE_DISABLE_ENV_FILE="true")
    return environment


def validate_inputs(project, endpoint, mlflow_port, api_port, report):
    require(isinstance(project, str) and re.fullmatch(r"stack02-proof-[a-z0-9][a-z0-9-]{5,39}", project),
            "invalid_project")
    require(endpoint == LOCAL, "invalid_socket")
    require(all(type(p) is int and 1024 <= p <= 65535 for p in (mlflow_port, api_port))
            and mlflow_port != api_port, "invalid_ports")
    require(report.is_absolute() and report.parent.is_dir() and not report.exists()
            and not report.is_symlink() and report.parent.resolve() == report.parent
            and report.parent.stat().st_uid == os.getuid(), "unsafe_report_path")


def receipt(output):
    lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    require(len(lines) == 1 and len(lines[0]) <= 65536, "invalid_receipt")
    try:
        value = json.loads(lines[0])
    except ValueError:
        raise Failure("invalid_receipt") from None
    require(isinstance(value, dict), "invalid_receipt")
    return value


def reader_diagnostic(output):
    """Accept one fixed-size SDK marker; never retain provider output."""
    marker = None
    for line in output.splitlines():
        if line.startswith(SDK_DIAGNOSTIC_MARKER):
            if marker is not None or len(line) > len(SDK_DIAGNOSTIC_MARKER) + 256:
                return None
            marker = line[len(SDK_DIAGNOSTIC_MARKER):]
    if marker is None:
        return None
    try:
        value = json.loads(marker)
    except ValueError:
        return None
    if (isinstance(value, dict)
            and (set(value) == {"stage", "exception", "http_status"}
                  or (set(value) in ({"stage", "exception", "http_status", "source_line"},
                                     {"stage", "exception", "http_status", "source_line", "error_code"})
                      and type(value["source_line"]) is int and 1 <= value["source_line"] <= len(SDK.splitlines())))
            and isinstance(value["stage"], str) and value["stage"] in READER_STAGES | SDK_STAGES
            and isinstance(value["exception"], str) and value["exception"] in SDK_EXCEPTION_KINDS
            and type(value["http_status"]) is int and value["http_status"] in SDK_HTTP_STATUSES
            and ("error_code" not in value or (value["exception"] in S3_EXCEPTION_KINDS
                 and isinstance(value["error_code"], str) and value["error_code"] in S3_ERROR_CODES))):
        return value
    return None


def sdk_progress(output):
    """Retain only the last constant stage, including partial timeout output."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    last = None
    for line in (output or "").splitlines():
        if line.startswith(SDK_PROGRESS_MARKER):
            value = line[len(SDK_PROGRESS_MARKER):]
            if value in SDK_STAGES | READER_STAGES:
                last = value
    return last


def release_diagnostic(output):
    """Recognize only constant errors emitted by the existing release script."""
    prefix = "Model release smoke failed: "
    lines = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if len(lines) != 1 or len(lines[0]) > 256:
        return None
    paths = {"POST /admin/models/invoice-review/refresh": "refresh",
             "GET /models/invoice-review/health": "health",
             "POST /models/invoice-review/predict": "predict"}
    outcomes = {"timed out": "timeout", "failed to reach the Model API": "unreachable",
                "failed to complete the request": "request_failed",
                "returned invalid JSON": "invalid_json",
                "returned an unexpected JSON response": "invalid_json"}
    for path, stage in paths.items():
        for suffix, category in outcomes.items():
            if lines[0] == path + " " + suffix:
                return {"stage": stage, "category": category, "http_status": 0}
        match = re.fullmatch(re.escape(path) + r" failed with HTTP ([0-9]{3})", lines[0])
        if match and int(match[1]) in SDK_HTTP_STATUSES - {0}:
            return {"stage": stage, "category": "http_response", "http_status": int(match[1])}
    return None


def validate_fixture(value):
    require(set(value) == {"sdk", "workspace", "run_id", "version", "manifest", "artifact_uri", "status"},
            "fixture_fields")
    require(value["sdk"] == "3.16.0" and value["workspace"] == "default"
            and value["status"] == "FINISHED"
            and re.fullmatch(r"[a-f0-9]{32}", value["run_id"])
            and re.fullmatch(r"[1-9][0-9]*", value["version"]), "fixture_metadata")
    manifest = value["manifest"]
    require(isinstance(manifest, dict) and {"MLmodel", "model.skops"} <= manifest.keys()
            and 2 <= len(manifest) <= 32, "fixture_manifest")
    for path, digest in manifest.items():
        require(re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", path) and not path.startswith("/")
                and all(p not in {"", ".", ".."} for p in path.split("/"))
                and re.fullmatch(r"[a-f0-9]{64}", digest), "fixture_manifest")
    uri = value["artifact_uri"]
    require(re.fullmatch(r"mlflow-artifacts:/[A-Za-z0-9_/-]+", uri)
            and uri.endswith("/" + value["run_id"] + "/artifacts")
            and ".." not in uri.split("/"), "fixture_artifact_route")
    return value


def validate_objects(value, fixture):
    """Do not describe a metadata-only MLflow version lookup as SDK execution."""
    require(set(value) == {"workspace", "manifest", "transport", "boto3", "mlflow_installed"}
            and value["transport"] == "direct_s3" and value["workspace"] == "default"
            and value["mlflow_installed"] == "3.16.1"
            and isinstance(value["boto3"], str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["boto3"])
            and value["manifest"] == fixture["manifest"], "object_hash_mismatch")


class Harness:
    def __init__(self, project, mlflow_port, api_port, environment,
                 docker_binary=DOCKER, compose_binary=COMPOSE):
        self.docker_binary, self.compose_binary = docker_binary, compose_binary
        self.project, self.ports, self.env = project, (mlflow_port, api_port), dict(environment)
        self.started = False
        self.uncertain = False
        self.known = None
        self.images = {}
        self.container_images = {}
        self.deadline = time.monotonic() + 1800
        self.phase = "preflight"
        self.proven = set()
        self.report = {"schema": "STACK02-v1", "project": project, "status": "UNKNOWN",
                       "phases": [], "proofs": {}, "snapshots": {}, "shutdown": "NOT_STARTED",
                       "backend_cancellation": "NOT_PROVEN"}

    def run(self, args, timeout=30, environment=None, data=None, cleanup=False,
            deadline=None, uncertain_on_timeout=True, operation="command"):
        require(operation in OPERATIONS, "invalid_operation")
        remaining = self.deadline - time.monotonic() - (0 if cleanup else 120)
        require(remaining > 0, "total_deadline")
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
            require(remaining > 0, "http_deadline")
        began = time.monotonic()
        observed = {"category": operation, "status": "failed", "elapsed_seconds": 0.0}
        try:
            result = subprocess.run(args, env=environment or self.env, cwd=ROOT,
                                    input=json.dumps(data) if data is not None else None,
                                    capture_output=True, text=True, check=False,
                                    timeout=min(timeout, remaining))
        except subprocess.TimeoutExpired as error:
            observed["status"] = "timeout"
            if operation in SDK_OPERATIONS.values():
                last = sdk_progress(error.stdout)
                if last is not None:
                    self.report["sdk_last_stage"] = last
            if uncertain_on_timeout:
                self.uncertain = True
                raise Failure("timeout_backend_cancellation_not_proven") from None
            raise Failure("http_request_timeout") from None
        except OSError:
            raise Failure("command_unavailable") from None
        else:
            if result.returncode != 0 and operation == "release":
                diagnostic = release_diagnostic(result.stdout + "\n" + result.stderr)
                if diagnostic is not None:
                    self.report["release_diagnostic"] = diagnostic
            if result.returncode != 0 and operation in SDK_OPERATIONS.values():
                last = sdk_progress(result.stdout)
                if last is not None:
                    self.report["sdk_last_stage"] = last
                diagnostic = reader_diagnostic(result.stdout)
                if diagnostic is not None:
                    self.report["sdk_diagnostic"] = diagnostic
            require(result.returncode == 0, "child_failed_output_suppressed")
            observed["status"] = "completed"
        finally:
            observed["elapsed_seconds"] = round(max(0, time.monotonic() - began), 3)
            # Preserve the primary failure's operation through cleanup queries.
            if "failure" not in self.report:
                self.report["operation"] = observed
        return result.stdout

    def docker(self, *args, **kwargs):
        kwargs.setdefault("operation", "docker_query")
        if kwargs["operation"] in DOCKER_READ_OPERATIONS:
            kwargs.setdefault("timeout", DOCKER_READ_TIMEOUT_SECONDS)
        return self.run([self.docker_binary, "--host", LOCAL, *args], **kwargs)

    def compose(self, *args, **kwargs):
        kwargs.setdefault("operation", "compose_query")
        if kwargs["operation"] in DOCKER_READ_OPERATIONS:
            kwargs.setdefault("timeout", DOCKER_READ_TIMEOUT_SECONDS)
        command = [self.compose_binary, "--host", LOCAL, "-p", self.project, "--project-directory", str(BASE),
                   "--env-file", "/dev/null"]
        for path in FILES:
            command.extend(["-f", str(path)])
        return self.run([*command, *args], **kwargs)

    def candidate(self, name):
        return name == self.project or name.startswith((self.project + "-", self.project + "_"))

    def inventory(self, images=False, ports=False):
        """Union separate name/reference and label queries, never an AND filter.

        Docker name filters match substrings/regex; image reference uses globs.
        Postfilter every row to the exact candidate rule. Label-filtered image
        rows carry the matched label because image ls has no Labels formatter.
        """
        result = {}
        for kind, args in (("containers", ["ps", "-a"]), ("networks", ["network", "ls"]),
                            ("volumes", ["volume", "ls"]), ("images", ["image", "ls", "-a"])):
            if kind == "images" and not images:
                continue
            filters = (["reference=" + self.project + suffix for suffix in ("", "-*", "_*")]
                       if kind == "images" else ["name=" + self.project])
            label = "com.docker.compose.project=" + self.project
            filters.append("label=" + label)
            if kind == "containers" and ports:
                filters += [f"publish={port}/{protocol}" for port in self.ports for protocol in ("tcp", "udp", "sctp")]
            rows = {}
            for selection in filters:
                output = self.docker(*args, "--filter", selection,
                                     *([] if kind == "volumes" else ["--no-trunc"]), "--format",
                                     CONTAINER_INVENTORY_FORMAT if kind == "containers" else "{{json .}}",
                                     operation="inventory_" + kind)
                for line in output.splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    name = row.get("Names", row.get("Name", row.get("Repository", "")))
                    if kind == "images" and selection == "label=" + label:
                        row["Labels"] = label
                    matched_label = label in row.get("Labels", "").split(",")
                    port_collision = kind == "containers" and ports and any(
                        re.search(r":" + str(port) + r"->", row.get("Ports", "")) for port in self.ports)
                    if self.candidate(name) or matched_label or port_collision:
                        key = (row.get("ID"), name, row.get("Tag"))
                        previous = rows.get(key, {})
                        # A name and label query can return the same row. Keep label evidence.
                        if label in previous.get("Labels", "").split(","):
                            row["Labels"] = previous["Labels"]
                        rows[key] = row
            result[kind] = list(rows.values())
        return result

    def owned(self, strict=False, discover=False):
        """Batch exact snapshot inspection; rediscover before project-wide mutations."""
        if strict:
            require(self.known is not None, "ownership_changed")
        if not strict or discover:
            inventory = self.inventory()
            targets = {kind: {} for kind in ("containers", "networks", "volumes")}
            for kind in targets:
                for row in inventory[kind]:
                    name = row.get("Names", row.get("Name", ""))
                    require(self.candidate(name), "ambiguous_resource")
                    targets[kind][name] = name
        else:
            targets = self.known
        found = {"containers": {}, "networks": {}, "volumes": {}}
        observed_images = {}
        for kind in ("networks", "volumes", "containers"):
            if not targets[kind]:
                continue
            resource = {"containers": "container", "networks": "network", "volumes": "volume"}[kind]
            fields = {"name": ".Name", "labels": ".Config.Labels" if kind == "containers" else ".Labels",
                      "id": ".Name" if kind == "volumes" else ".Id"}
            fields.update({"ports": ".NetworkSettings.Ports", "image": ".Image", "mounts": ".Mounts",
                           "networks": ".NetworkSettings.Networks", "network_mode": ".HostConfig.NetworkMode"}
                          if kind == "containers" else {"members": ".Containers"} if kind == "networks" else {})
            template = "{" + ",".join('"' + key + '":{{json ' + value + '}}' for key, value in fields.items()) + "}"
            output = self.docker(resource, "inspect", *targets[kind].values(), "--format", template,
                                 operation="inspect_" + kind)
            rows = [json.loads(line) for line in output.splitlines() if line.strip()]
            require(len(rows) == len(targets[kind]), "ownership_changed")
            for row in rows:
                name = row["name"].removeprefix("/") if kind == "containers" else row["name"]
                require(name in targets[kind] and name not in found[kind], "ownership_changed")
                tags, identity = row["labels"] or {}, row["id"]
                require(tags.get("com.docker.compose.project") == self.project, "ambiguous_resource")
                if kind == "containers":
                    service = tags.get("com.docker.compose.service")
                    require(service in SERVICES and name == f"{self.project}-{service}-1"
                            and tags.get("com.docker.compose.project.config_files") == ",".join(map(str, FILES))
                            and tags.get("com.docker.compose.project.working_dir") == str(BASE), "ambiguous_resource")
                    bindings = row["ports"] or {}
                    expected = {"mlflow": ("5000/tcp", self.ports[0]), "model-api": ("8000/tcp", self.ports[1])}
                    published = {p: v for p, v in bindings.items() if v}
                    wanted = {expected[service][0]: [{"HostIp": "127.0.0.1", "HostPort": str(expected[service][1])}]} if service in expected else {}
                    require(published == wanted, "runtime_ports")
                    if service in self.images:
                        require(row["image"] == self.images[service], "runtime_image_mismatch")
                    require(name not in self.container_images or row["image"] == self.container_images[name],
                            "runtime_image_mismatch")
                    observed_images[name] = row["image"]
                    mounts = row["mounts"] or []
                    wanted_mounts = {"postgres": ("postgres_data", "/var/lib/postgresql/data"),
                                     "minio": ("minio_data", "/data")}
                    actual_mounts = [(v.get("Type"), v.get("Name"), v.get("Destination"), v.get("RW")) for v in mounts]
                    wanted_mount = ([("volume", f"{self.project}_{wanted_mounts[service][0]}",
                                      wanted_mounts[service][1], True)] if service in wanted_mounts else [])
                    require(actual_mounts == wanted_mount, "runtime_mounts")
                    network = self.project + "_default"
                    memberships = row["networks"] or {}
                    require(set(memberships) == {network} and row["network_mode"] == network
                            and memberships[network].get("NetworkID") == found["networks"].get(network),
                            "runtime_networks")
                else:
                    suffixes = {"default"} if kind == "networks" else {"postgres_data", "minio_data"}
                    require(name in {f"{self.project}_{s}" for s in suffixes}, "ambiguous_resource")
                    key = "com.docker.compose.network" if kind == "networks" else "com.docker.compose.volume"
                    require(tags.get(key) in suffixes and name == f"{self.project}_{tags[key]}", "ambiguous_resource")
                    if kind == "networks":
                        members = row["members"] or {}
                        require(all(member.get("Name") in targets["containers"] for member in members.values()),
                                "runtime_network_members")
                        # Exact membership IDs are compared after container inspection below.
                        network_members = members
                require(isinstance(identity, str) and (identity == name if kind == "volumes" else
                        re.fullmatch(r"[a-f0-9]{64}", identity)), "invalid_resource_id")
                found[kind][name] = identity
        if found["networks"]:
            require(all(found["containers"].get(member.get("Name")) == identity
                        for identity, member in network_members.items()), "runtime_network_members")
        if strict:
            require(self.known is not None and found == self.known, "ownership_changed")
        self.container_images.update(observed_images)
        return found

    def built_images(self):
        for service in ("mlflow", "model-api"):
            fields = self.docker("image", "inspect", f"{self.project}-{service}:latest", "--format",
                                 "{{json .Id}}|{{json .Config.User}}", operation="inspect_images").strip().split("|")
            identity, user = map(json.loads, fields)
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", identity), "invalid_image_id")
            if service == "model-api":
                require(user == "app", "unsafe_api_image_user")
            self.images[service] = identity
        self.report["images"] = dict(self.images)

    def preflight(self):
        inventory = self.inventory(images=True, ports=True)
        for kind, rows in inventory.items():
            for row in rows:
                name = row.get("Names", row.get("Name", row.get("Repository", "")))
                require(not (name == self.project or name.startswith((self.project + "_", self.project + "-")))
                        and f"com.docker.compose.project={self.project}" not in row.get("Labels", "").split(","),
                        "preexisting_resource")
                if kind == "containers":
                    require(not any(re.search(r":" + str(port) + r"->", row.get("Ports", ""))
                                    for port in self.ports), "port_in_use")
        self.env.update({key: secrets.token_urlsafe(32) for key in (
            "STACK_POSTGRES_PASSWORD", "STACK_MINIO_ROOT_PASSWORD", "MLFLOW_AUTH_ADMIN_PASSWORD",
            "MLFLOW_FLASK_SERVER_SECRET_KEY", "STACK_MODEL_API_MLFLOW_PASSWORD", "MODEL_API_ADMIN_TOKEN")})
        self.env.update({"STACK_MINIO_ROOT_USER": "s" + secrets.token_hex(8),
                         "MLFLOW_AUTH_ADMIN_USERNAME": "a" + secrets.token_hex(8),
                         "STACK_MODEL_API_MLFLOW_USERNAME": "r" + secrets.token_hex(8),
                         "STACK_MLFLOW_PORT": str(self.ports[0]), "STACK_MODEL_API_PORT": str(self.ports[1])})
        config = json.loads(self.compose("config", "--format", "json", operation="compose_config"))
        require(set(config["services"]) == SERVICES, "unexpected_services")
        for kind, suffixes in (("networks", {"default"}), ("volumes", {"postgres_data", "minio_data"})):
            require(set(config[kind]) == suffixes, "unexpected_resources")
            for suffix, resource in config[kind].items():
                require(not resource.get("external") and resource["name"] == f"{self.project}_{suffix}",
                        "unsafe_resource_config")
        for name, service in config["services"].items():
            require(not any(service.get(k) for k in ("container_name", "network_mode", "privileged", "devices", "secrets", "configs")),
                    "unsafe_service_config")
            require(set(service.get("networks", {})) == {"default"}, "unsafe_service_network")
            require(all(v["type"] == "volume" and v["source"] in {"postgres_data", "minio_data"}
                        for v in service.get("volumes", [])), "unsafe_mount")
            expected = {"mlflow": (5000, self.ports[0]), "model-api": (8000, self.ports[1])}
            ports = service.get("ports", [])
            require(ports == ([{"mode": "ingress", "host_ip": "127.0.0.1", "target": expected[name][0],
                                "published": str(expected[name][1]), "protocol": "tcp"}] if name in expected else []),
                    "unsafe_ports")
            if name in {"mlflow", "model-api"}:
                require(service.get("image", f"{self.project}-{name}") in
                        {f"{self.project}-{name}", f"{self.project}-{name}:latest"}, "unsafe_build_image")
                require(service["build"]["context"] == str(BASE if name == "mlflow" else API)
                        and service["build"].get("dockerfile", "Dockerfile") ==
                        ("Dockerfile.mlflow" if name == "mlflow" else "Dockerfile"), "unsafe_build_context")
        api_env = config["services"]["model-api"]["environment"]
        require(api_env == {"MLFLOW_TRACKING_URI": "http://mlflow:5000",
                           "MLFLOW_TRACKING_USERNAME": self.env["STACK_MODEL_API_MLFLOW_USERNAME"],
                           "MLFLOW_TRACKING_PASSWORD": self.env["STACK_MODEL_API_MLFLOW_PASSWORD"],
                           "MODEL_API_ADMIN_TOKEN": self.env["MODEL_API_ADMIN_TOKEN"],
                           "MODEL_REFRESH_TIMEOUT_SECONDS": str(REFRESH_SECONDS),
                           "MODEL_STARTUP_TIMEOUT_SECONDS": str(STARTUP_SECONDS)}, "unsafe_api_environment")
        require(config["services"]["mlflow"]["environment"]["MLFLOW_ARTIFACTS_DESTINATION"] ==
                "s3://mlflow-artifacts", "unsafe_artifact_route")
        mlflow_env = config["services"]["mlflow"]["environment"]
        database = "postgresql+psycopg2://mlflow:" + self.env["STACK_POSTGRES_PASSWORD"] + "@postgres:5432/mlflow"
        require(mlflow_env.get("MLFLOW_BACKEND_STORE_URI") == database
                and mlflow_env.get("MLFLOW_AUTH_DATABASE_URI") == database
                and mlflow_env.get("MLFLOW_S3_ENDPOINT_URL") == "http://minio:9000"
                and mlflow_env.get("AWS_ACCESS_KEY_ID") == self.env["STACK_MINIO_ROOT_USER"]
                and mlflow_env.get("AWS_SECRET_ACCESS_KEY") == self.env["STACK_MINIO_ROOT_PASSWORD"],
                "unsafe_store_environment")
        self.proven.add("preflight")

    def sdk(self, action, data, admin=False, service="mlflow"):
        self.owned(strict=True)
        environment = dict(self.env)
        environment.update(MLFLOW_TRACKING_URI="http://127.0.0.1:5000" if service == "mlflow" else "http://mlflow:5000",
                           MLFLOW_TRACKING_USERNAME=self.env["MLFLOW_AUTH_ADMIN_USERNAME"] if admin else self.env["STACK_MODEL_API_MLFLOW_USERNAME"],
                           MLFLOW_TRACKING_PASSWORD=self.env["MLFLOW_AUTH_ADMIN_PASSWORD"] if admin else self.env["STACK_MODEL_API_MLFLOW_PASSWORD"],
                           MLFLOW_HTTP_REQUEST_TIMEOUT="10", MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
                           MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR="0", MLFLOW_DISABLE_TELEMETRY="true")
        keys = [k for k in environment if k.startswith("MLFLOW_TRACKING_") or k.startswith("MLFLOW_HTTP_")]
        keys += ["MLFLOW_DISABLE_TELEMETRY"]
        if action == "bootstrap":
            keys += ["STACK_MODEL_API_MLFLOW_USERNAME", "STACK_MODEL_API_MLFLOW_PASSWORD"]
        require(action in SDK_OPERATIONS and service in {"mlflow", "model-api"}, "invalid_sdk_action")
        args = ["exec", "-i"]
        for key in keys:
            args.extend(["-e", key])  # Names only: raw credentials never enter argv.
        identity = self.known["containers"][f"{self.project}-{service}-1"]
        return receipt(self.docker(*args, identity, "python", "-B", "-c", SDK, action,
                                   timeout=150, environment=environment, data=data,
                                   operation=SDK_OPERATIONS[action]))

    def http(self, path, method="GET", payload=None, token=None, *, deadline=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Model-Admin-Token"] = token
        mutation = method == "POST" and path.startswith("/admin/") and token == self.env.get("MODEL_API_ADMIN_TOKEN")
        timeout = HTTP_REFRESH_SECONDS if mutation else 5
        try:
            child_timeout = timeout + HTTP_CHILD_GRACE_SECONDS
            if deadline is not None:
                remaining = min(deadline, self.deadline - 120) - time.monotonic()
                require(remaining > 0, "http_deadline")
                timeout = min(timeout, remaining)
                child_timeout = min(child_timeout, remaining)
            value = receipt(self.run([sys.executable, "-B", "-c", HTTP],
                                    timeout=child_timeout, deadline=deadline,
                                    uncertain_on_timeout=mutation, operation="http",
                                    data={"url": f"http://127.0.0.1:{self.ports[1]}{path}",
                                          "method": method, "payload": payload, "headers": headers,
                                          "timeout_seconds": timeout}))
            if deadline is not None:
                require(time.monotonic() < min(deadline, self.deadline - 120), "http_deadline")
            require(type(value.get("status")) is int and 0 <= value["status"] <= 599
                    and isinstance(value.get("body"), dict), "invalid_http_receipt")
            if value["status"] == 0:
                require(value.get("transport") == "indeterminate", "invalid_http_receipt")
                if mutation:
                    raise Failure("transport_backend_cancellation_not_proven")
            return value
        except (Failure, ValueError):
            if mutation:
                self.uncertain = True
            raise

    def expect_http(self, path, status, method="GET", payload=None, token=None):
        value = self.http(path, method, payload, token)
        require(value.get("status") == status, "http_status_mismatch")
        return value.get("body")

    def release(self, fixture):
        self.owned(strict=True)
        environment = shared.child_environment()
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        environment.update(MODEL_API_BASE_URL=f"http://127.0.0.1:{self.ports[1]}",
                           MODEL_API_ADMIN_TOKEN=self.env["MODEL_API_ADMIN_TOKEN"],
                           MODEL_RELEASE_EXPECTED_MODEL_VERSION=fixture["version"],
                           MODEL_RELEASE_EXPECTED_RUN_ID=fixture["run_id"],
                           MODEL_RELEASE_HTTP_TIMEOUT_SECONDS=str(HTTP_REFRESH_SECONDS), NO_PROXY="*")
        try:
            # Three requests, each with its own bounded child/watchdog.
            self.run([sys.executable, "-B", str(API / "scripts/model_release_smoke.py")],
                     timeout=3 * HTTP_REFRESH_SECONDS + 25, environment=environment, operation="release")
        except Failure:
            # A nonzero release child cannot prove whether its refresh completed.
            self.uncertain = True
            raise
        health = self.expect_http("/models/invoice-review/health", 200)
        require(health.get("registered_model") == "invoice-review-production" and health.get("alias") == "champion"
                and health.get("run_id") == fixture["run_id"] and health.get("model_version") == fixture["version"],
                "canonical_metadata_mismatch")

    def phase_start(self, phase):
        self.phase = phase
        self.report["phases"].append(phase)

    def minio_idempotency(self):
        self.owned(strict=True)
        identity = self.known["containers"][f"{self.project}-minio-init-1"]
        fields = self.docker("container", "inspect", identity, "--format",
                             "{{json .Id}}|{{json .State.Status}}|{{json .State.ExitCode}}|{{json .Image}}",
                             operation="inspect_containers")
        before = [json.loads(part) for part in fields.strip().split("|")]
        require(len(before) == 4 and before[:3] == [identity, "exited", 0]
                and isinstance(before[3], str) and re.fullmatch(r"sha256:[a-f0-9]{64}", before[3]),
                "initializer_not_stopped_successfully")
        # Restart the exact proven container, not a name that could be replaced.
        self.docker("start", "--attach", identity, timeout=30, operation="minio_restart")
        self.owned(strict=True)
        fields = self.docker("container", "inspect", identity, "--format",
                             "{{json .Id}}|{{json .State.Status}}|{{json .State.ExitCode}}|{{json .Image}}",
                             operation="inspect_containers")
        after = [json.loads(part) for part in fields.strip().split("|")]
        require(after == before, "initializer_repeat_failed")
        self.report["proofs"]["minio_idempotency"] = {
            "container_id": identity, "image_id": before[3], "status": "exited", "exit_code": 0,
        }
        self.proven.add("minio_idempotency")

    def wait_for_process(self):
        # Readiness polling only; failures in proof phases are never retried.
        ready_deadline = min(time.monotonic() + READINESS_SECONDS, self.deadline - 120)
        while time.monotonic() < ready_deadline:
            process = self.http("/health", deadline=ready_deadline)
            if process.get("status") == 200:
                require(process.get("body") == {"status": "ok"}, "process_health")
                return
            require(process.get("status") == 0, "process_health")
            time.sleep(min(1, max(0, ready_deadline - time.monotonic())))
        raise Failure("process_not_ready")

    def execute(self):
        self.phase_start("preflight")
        self.preflight()
        self.phase_start("build")
        self.known = self.owned()
        require(not any(self.known.values()), "preexisting_resource")
        self.started = True
        self.compose("build", "mlflow", "model-api", timeout=600, operation="compose_build")
        self.built_images()
        require(not any(self.owned().values()), "preexisting_resource")
        self.phase_start("base_start")
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", str(BASE_START_SECONDS),
                     "postgres", "minio", "minio-init", "mlflow", timeout=BASE_START_COMMAND_SECONDS,
                     operation="compose_base_start")
        self.known = self.owned()
        require(len(self.known["containers"]) == 4 and len(self.known["networks"]) == 1
                and len(self.known["volumes"]) == 2, "base_topology")
        self.report["snapshots"]["base"] = self.known
        self.phase_start("minio_idempotency")
        self.minio_idempotency()
        self.phase_start("bootstrap")
        bootstrap = self.sdk("bootstrap", {"project": self.project}, admin=True)
        require(bootstrap == {"sdk": "3.16.1", "workspace": "default", "experiment": bootstrap.get("experiment"),
                              "reader_nonadmin": True, "exact_read_role": True}
                and re.fullmatch(r"[0-9]+", bootstrap["experiment"]), "bootstrap_contract")
        self.proven.add("bootstrap")
        self.phase_start("api_start")
        self.owned(strict=True, discover=True)
        self.compose("up", "-d", "--no-build", "--no-deps", "model-api", timeout=60, operation="compose_api_start")
        previous = self.known
        self.known = self.owned()
        require(len(self.known["containers"]) == 5 and all(self.known[k] == previous[k]
                for k in ("networks", "volumes")) and all(self.known["containers"].get(n) == i
                for n, i in previous["containers"].items()), "api_topology")
        self.report["snapshots"]["api"] = self.known
        self.phase_start("missing_champion")
        self.wait_for_process()
        self.expect_http("/models/invoice-review/health", 503)
        self.expect_http("/models/invoice-review/predict", 503, "POST", self.predict_payload())
        for token in (None, "invalid-stack02-token"):
            self.expect_http("/admin/models/invoice-review/refresh", 401, "POST", token=token)
        self.proven.add("missing_champion")
        data = {"project": self.project, "experiment": bootstrap["experiment"]}
        self.phase_start("fixture")
        fixture = validate_fixture(self.sdk("fixture", data, admin=True, service="model-api"))
        data.update(fixture)
        self.report["proofs"]["fixture"] = {k: fixture[k] for k in ("sdk", "workspace", "run_id", "version", "manifest", "status")}
        self.proven.add("fixture")
        self.phase_start("objects")
        objects = self.sdk("objects", data, admin=True)
        validate_objects(objects, fixture)
        self.report["proofs"]["objects"] = objects
        self.proven.add("objects")
        self.phase_start("reader")
        reader = self.sdk("reader", data, service="model-api")
        require(reader == {"sdk": "3.16.0", "workspace": "default", "download_hashes": True,
                           "reads": True, "creates_denied": True, "writes_denied": True}, "reader_contract")
        self.proven.add("reader")
        self.phase_start("release")
        self.release(fixture)
        self.proven.add("release")
        self.phase_start("startup_champion")
        self.owned(strict=True, discover=True)
        self.compose("restart", "--timeout", "10", "model-api", timeout=30, operation="compose_api_restart")
        self.owned(strict=True)
        self.wait_for_process()
        self.owned(strict=True)
        health = self.expect_http("/models/invoice-review/health", 200)
        prediction = self.expect_http("/models/invoice-review/predict", 200, "POST", self.predict_payload())
        self.validate_active(health, fixture)
        self.validate_active(prediction, fixture, probability=True)
        require(health.get("registered_model") == "invoice-review-production"
                and health.get("alias") == "champion", "canonical_metadata_mismatch")
        self.report["proofs"]["startup_champion"] = {
            "container_id": self.known["containers"][f"{self.project}-model-api-1"],
            "run_id": fixture["run_id"], "model_version": fixture["version"],
            "health_status": 200, "predict_status": 200, "probability": prediction["probability"],
        }
        self.proven.add("startup_champion")
        self.phase_start("missing_alias")
        self.alias("remove_alias", data)
        self.expect_http("/admin/models/invoice-review/refresh", 503, "POST", token=self.env["MODEL_API_ADMIN_TOKEN"])
        health = self.expect_http("/models/invoice-review/health", 200)
        prediction = self.expect_http("/models/invoice-review/predict", 200, "POST", self.predict_payload())
        self.validate_active(health, fixture)
        self.validate_active(prediction, fixture, probability=True)
        self.proven.add("missing_alias")
        self.phase_start("restored")
        self.alias("restore_alias", data)
        self.release(fixture)
        self.proven.add("restored")

    @staticmethod
    def predict_payload():
        # Read-only import of the existing stdlib smoke; no request on import.
        spec = importlib.util.spec_from_file_location("stack02_release", API / "scripts/model_release_smoke.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.PREDICT_PAYLOAD

    @staticmethod
    def validate_active(value, fixture, probability=False):
        import math
        require(value.get("model_id") == "invoice-review" and value.get("model_version") == fixture["version"]
                and value.get("run_id") == fixture["run_id"], "active_metadata_mismatch")
        if probability:
            p = value.get("probability")
            require(type(p) in (float, int) and math.isfinite(p) and 0 <= p <= 1, "invalid_probability")

    def alias(self, action, data):
        value = self.sdk(action, data, admin=True)
        require(value == {"sdk": "3.16.1", "workspace": "default", "alias_action": action}, "alias_contract")

    def shutdown(self):
        if not self.started:
            self.report["shutdown"] = "NOT_NEEDED"
            return
        # Timed-out backend operations might still create resources after inspection.
        require(not self.uncertain, "backend_state_uncertain")
        before = self.owned(strict=True, discover=True)
        self.report["snapshots"]["before_shutdown"] = before
        self.compose("down", "--timeout", "20", timeout=90, cleanup=True, operation="compose_shutdown")
        after = self.owned()
        require(not after["containers"] and not after["networks"]
                and after["volumes"] == before["volumes"], "shutdown_preservation")
        images = dict(self.images)
        self.built_images()
        require(self.images == images, "shutdown_image_preservation")
        self.report["snapshots"]["stopped"] = after
        self.report["shutdown"] = "PASS_VOLUMES_IMAGES_PRESERVED"

    def finish(self):
        try:
            self.execute()
            require(self.proven == REQUIRED, "missing_proof")
        except Exception as error:
            self.report["failure"] = {"phase": self.phase, "code": str(error) if isinstance(error, Failure)
                                      else "unexpected_failure_output_suppressed"}
        finally:
            try:
                self.shutdown()
            except Exception:
                self.report["shutdown"] = "UNKNOWN_REQUIRES_REVIEW"
            self.report["proof_status"] = {key: "PASS" if key in self.proven else "UNKNOWN" for key in sorted(REQUIRED)}
            self.report["status"] = ("PASS" if self.proven == REQUIRED and "failure" not in self.report
                                     and self.report["shutdown"] == "PASS_VOLUMES_IMAGES_PRESERVED" else "FAIL")
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Requires separate operational authorization")
    parser.add_argument("--project", required=True)
    parser.add_argument("--docker-host", required=True, choices=[LOCAL])
    parser.add_argument("--mlflow-port", required=True, type=int)
    parser.add_argument("--api-port", required=True, type=int)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--docker-binary", help="Trusted absolute Docker executable; default: PATH/known installation")
    parser.add_argument("--compose-binary", help="Trusted absolute standalone Compose executable; default: PATH/known installation")
    parser.add_argument("--temp-root", type=Path, help="Existing temporary parent; default: platform tempfile location")
    args = parser.parse_args(argv)
    if not args.execute:
        print("REFUSED: explicit --execute and separate operational consent required", file=sys.stderr)
        return 2
    try:
        validate_inputs(args.project, args.docker_host, args.mlflow_port, args.api_port, args.report)
        docker_binary = trusted_executable(args.docker_binary, DOCKER)
        compose_binary = trusted_executable(args.compose_binary, COMPOSE)
        require(args.temp_root is None or (args.temp_root.is_absolute() and args.temp_root.is_dir()),
                "invalid_temp_root")
        # Exclusive, private receipt: never overwrite an earlier attempt or follow a link.
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output, tempfile.TemporaryDirectory(
                prefix="stack02-docker-", dir=args.temp_root) as directory:
            environment = isolated_cli_environment(directory, compose_binary)
            environment["PATH"] = os.pathsep.join(dict.fromkeys([
                str(Path(docker_binary).parent), str(Path(compose_binary).parent),
                "/usr/bin", "/bin", "/usr/sbin", "/sbin",
            ]))
            harness = Harness(args.project, args.mlflow_port, args.api_port, environment,
                              docker_binary, compose_binary)
            report = harness.finish()
            json.dump(report, output, sort_keys=True, indent=2)
            output.write("\n")
        print(json.dumps({"status": report["status"], "shutdown": report["shutdown"]}))
        return 0 if report["status"] == "PASS" else 1
    except Exception as error:
        print("FAIL: " + (str(error) if isinstance(error, Failure) else
                          "preparation_failed_output_suppressed"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
