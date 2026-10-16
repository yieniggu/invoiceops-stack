"""One-shot, local-only Compose proof; never print child output or credentials."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / "invoiceops-mlflow"
ML = ROOT.parent / "invoiceops-ml"
LOCAL = "unix:///var/run/docker.sock"
TEMP_ROOT = Path("/var/folders/y3/m1xh54ds39x7c15dzzngkh1r0000gn/T/opencode")
MARKER = "STACK01_RECEIPT="
PAYLOAD = b"STACK01 synthetic artifact\n"
PROGRESS_PHASES = ("sdk_import", "helper_import", "configuration", "configure",
                   "workspace_create", "workspace_select", "client", "experiment",
                   "run_start", "artifact_source", "artifact_log", "run_end",
                   "run_query", "artifact_download", "local_validation", "receipt")


def progress_sequence(previous=False):
    phases = [phase for phase in PROGRESS_PHASES if not previous or phase not in
              {"workspace_create", "experiment", "run_start", "artifact_source", "artifact_log", "run_end"}]
    events = [{"phase": phase, "state": state} for phase in phases for state in ("before", "after")]
    # Metadata is checked while evaluating the existing receipt expression.
    events[-1:-1] = [{"phase": "workspace_metadata", "state": state} for state in ("before", "after")]
    return events


def collect_progress(journal, previous=False):
    """Bound reads, retain only validated enums, and never return raw records."""
    journal.seek(0)
    data = journal.read(32769)
    limit = "TOTAL_LIMIT" if len(data) > 32768 else None
    lines = data.splitlines(keepends=True)
    if limit is None and len(lines) > 64:
        limit = "EVENT_LIMIT"
    events = []
    reason = "COMPLETE" if data else "EMPTY"
    expected = progress_sequence(previous)

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    for line in lines[:64]:
        if len(line) > 512:
            reason = "RECORD_LIMIT"
            break
        if not line.endswith(b"\n"):
            reason = "TRUNCATED"
            break
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=unique_fields)
            if (not isinstance(event, dict) or set(event) != {"phase", "state"}
                    or type(event["phase"]) is not str or type(event["state"]) is not str
                    or event["phase"] not in {*PROGRESS_PHASES, "workspace_metadata"}
                    or event["state"] not in {"before", "after"}):
                raise ValueError
        except (ValueError, UnicodeError):
            reason = "INVALID_RECORD"
            break
        if len(events) >= len(expected) or event != expected[len(events)]:
            reason = "INVALID_SEQUENCE"
            break
        events.append(event)
    return {"events": events, "reason": limit or reason}

CONSUMER = r'''
import hashlib, json, os, tempfile
from pathlib import Path

def progress(phase, state):
    # Call sites supply constants only; diagnostic failure must not mask SDK work.
    try:
        fd = os.environ.get("STACK_SMOKE_PROGRESS_FD")
        if fd is not None:
            os.write(int(fd), (json.dumps({"phase": phase, "state": state}) + "\n").encode("utf-8"))
    except (OSError, ValueError):
        return

progress("sdk_import", "before")
import mlflow
progress("sdk_import", "after")
progress("helper_import", "before")
from invoiceops_ml.mlflow import mlflow_config_from_env, configure_mlflow
progress("helper_import", "after")

def receipt_workspace_name(workspace):
    progress("workspace_metadata", "before")
    # set_workspace persists active selection to MLFLOW_WORKSPACE in SDK 3.16.0.
    assert workspace and mlflow_config_from_env().workspace == workspace
    name = mlflow.get_workspace(workspace).name
    assert name == workspace
    progress("workspace_metadata", "after")
    return name

progress("configuration", "before")
config = mlflow_config_from_env()
assert config.tracking_username and config.tracking_password and config.workspace
progress("configuration", "after")
progress("configure", "before")
configure_mlflow(config)
progress("configure", "after")
workspace = config.workspace
run_id = os.environ.get("STACK_SMOKE_RUN_ID")
if not run_id:
    progress("workspace_create", "before")
    mlflow.create_workspace(workspace)
    progress("workspace_create", "after")
progress("workspace_select", "before")
mlflow.set_workspace(workspace)
progress("workspace_select", "after")
progress("client", "before")
client = mlflow.MlflowClient()
progress("client", "after")
payload = b"STACK01 synthetic artifact\n"
with tempfile.TemporaryDirectory(prefix="stack01-artifact-") as directory:
    if not run_id:
        progress("experiment", "before")
        experiment = mlflow.create_experiment("stack01-proof")
        progress("experiment", "after")
        progress("run_start", "before")
        with mlflow.start_run(experiment_id=experiment) as run:
            progress("run_start", "after")
            run_id = run.info.run_id
            progress("artifact_source", "before")
            source = Path(directory) / "proof.txt"
            source.write_bytes(payload)
            progress("artifact_source", "after")
            progress("artifact_log", "before")
            mlflow.log_artifact(str(source), "smoke")
            progress("artifact_log", "after")
            progress("run_end", "before")
        progress("run_end", "after")
    progress("run_query", "before")
    run = client.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.info.artifact_uri.startswith("mlflow-artifacts:/")
    progress("run_query", "after")
    progress("artifact_download", "before")
    downloaded = Path(client.download_artifacts(run_id, "smoke/proof.txt", directory))
    progress("artifact_download", "after")
    progress("local_validation", "before")
    actual = downloaded.read_bytes()
    assert actual == payload
    progress("local_validation", "after")
    progress("receipt", "before")
    print("STACK01_RECEIPT=" + json.dumps({
        "run_id": run_id, "workspace": receipt_workspace_name(workspace),
        "sha256": hashlib.sha256(actual).hexdigest(),
        "artifact_uri": run.info.artifact_uri, "sdk_version": mlflow.__version__,
        "bytes": len(actual), "status": run.info.status,
    }, sort_keys=True))
    progress("receipt", "after")
'''

OBJECT_PROOF = r'''
import boto3, hashlib, json, os
s3 = boto3.client("s3", endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"])
objects = s3.list_objects_v2(Bucket="mlflow-artifacts", MaxKeys=10).get("Contents", [])
assert len(objects) == 1
key = objects[0]["Key"]
assert key.endswith("/smoke/proof.txt")
body = s3.get_object(Bucket="mlflow-artifacts", Key=key)["Body"].read(1024)
print("STACK01_RECEIPT=" + json.dumps({"key": key, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}))
'''


class SmokeError(Exception):
    """A privacy-safe diagnostic with no child output attached."""


def validate_inputs(project, port):
    if not re.fullmatch(r"stack01-proof-[a-z0-9][a-z0-9-]{5,39}", project):
        raise SmokeError("invalid_project")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise SmokeError("invalid_port")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SmokeError("port_unavailable") from None


def child_environment():
    # In particular, do not inherit HOME, Docker contexts, auth or proxy settings.
    return {"PATH": os.environ.get("PATH", os.defpath), "DOCKER_HOST": LOCAL,
            "PYTHONDONTWRITEBYTECODE": "1"}


def isolated_cli_environment(directory):
    """Use installed plugins, but no ambient Docker configuration or keychain."""
    directory = Path(directory)
    plugins = Path("/Applications/Docker.app/Contents/Resources/cli-plugins")
    config = {"auths": {"https://index.docker.io/v1/": {}},
              "credsStore": "", "credHelpers": {}}
    # A nonempty anonymous auths map prevents Docker's default-store detection
    # from selecting an ambient keychain helper (Docker CLI ContainsAuth).
    if sys.platform == "darwin" and (plugins / "docker-compose").is_file():
        config["cliPluginsExtraDirs"] = [str(plugins)]
    with (directory / "config.json").open("x", encoding="utf-8") as output:
        json.dump(config, output)
    environment = child_environment()
    environment.update(DOCKER_CONFIG=str(directory),
                       BUILDX_CONFIG=str(directory / "buildx"))
    return environment


def receipt(output):
    lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise SmokeError("invalid_receipt_count")
    try:
        result = json.loads(lines[0])
    except ValueError:
        raise SmokeError("invalid_receipt_json") from None
    if not isinstance(result, dict):
        raise SmokeError("invalid_receipt_shape")
    return result


def validate_consumer(result, workspace, previous=None):
    if set(result) != {"run_id", "workspace", "sha256", "artifact_uri", "sdk_version", "bytes", "status"}:
        raise SmokeError("invalid_consumer_fields")
    if (not re.fullmatch(r"[a-f0-9]{32}", result["run_id"])
            or result["workspace"] != workspace
            or result["sha256"] != hashlib.sha256(PAYLOAD).hexdigest()
            or result["bytes"] != len(PAYLOAD) or result["status"] != "FINISHED"
            or not re.fullmatch(r"mlflow-artifacts:/[A-Za-z0-9_./-]+", result["artifact_uri"])
            or not re.fullmatch(r"\d+\.\d+\.\d+", result["sdk_version"])):
        raise SmokeError("consumer_contract_failed")
    if previous is not None and result != previous:
        raise SmokeError("persistence_mismatch")
    return result


class Harness:
    def __init__(self, project, port):
        self.project, self.port = project, port
        self.env = child_environment()
        self.report = {"project": project, "endpoint": LOCAL, "port": port,
                       "status": "FAIL", "commands": [], "snapshots": {}}
        self.started = False
        self.known = {kind: set() for kind in ("containers", "networks", "volumes")}

    def run(self, stage, args, timeout=30, environment=None, progress=False):
        start = time.monotonic()
        record = {"stage": stage, "timeout_seconds": timeout}
        self.report["commands"].append(record)
        journal = None
        try:
            options = {}
            if progress:
                record["progress"] = {"events": [], "reason": "EMPTY"}
                journal = tempfile.TemporaryFile(mode="w+b", prefix="stack01-progress-", dir=TEMP_ROOT)
                os.fchmod(journal.fileno(), 0o600)
                environment = dict(environment or self.env, STACK_SMOKE_PROGRESS_FD=str(journal.fileno()))
                options["pass_fds"] = (journal.fileno(),)
            result = subprocess.run(args, env=environment or self.env, cwd=ROOT,
                                    capture_output=True, text=True, timeout=timeout, check=False, **options)
        except subprocess.TimeoutExpired:
            record.update(outcome="TIMEOUT", backend_cancelled="NOT_PROVEN")
            raise SmokeError(f"{stage}:timeout_backend_cancellation_not_proven") from None
        except OSError:
            record["outcome"] = "UNAVAILABLE"
            raise SmokeError(f"{stage}:command_unavailable") from None
        finally:
            if journal is not None:
                try:
                    record["progress"] = collect_progress(journal, stage == "consumer_after")
                except (OSError, ValueError):
                    record["progress"] = {"events": [], "reason": "INVALID_RECORD"}
                finally:
                    try:
                        journal.close()
                    except OSError:
                        record["progress"] = {"events": [], "reason": "INVALID_RECORD"}
            record["duration_seconds"] = round(time.monotonic() - start, 3)
        record.update(returncode=result.returncode, outcome="PASS" if result.returncode == 0 else "FAIL")
        if result.returncode:
            raise SmokeError(f"{stage}:exit_{result.returncode}_output_suppressed")
        return result.stdout

    def docker(self, stage, *args, timeout=30):
        return self.run(stage, ["docker", "--host", LOCAL, *args], timeout)

    def compose(self, stage, *args, timeout=30):
        return self.docker(stage, "compose", "-p", self.project,
                           "--project-directory", str(BASE), "--env-file", "/dev/null",
                           "-f", str(BASE / "compose.yml"), "-f", str(ROOT / "compose.yml"),
                           *args, timeout=timeout)

    def inventory(self):
        resources = {}
        for kind, command in (("containers", ["ps", "-a"]),
                              ("networks", ["network", "ls"]), ("volumes", ["volume", "ls"])):
            output = self.docker("inventory_" + kind, *command, "--filter",
                                 f"label=com.docker.compose.project={self.project}", "--format", "{{json .}}")
            resources[kind] = [json.loads(line) for line in output.splitlines() if line.strip()]
        return resources

    def preflight(self):
        self.report["docker_version"] = self.docker("daemon", "version", "--format", "{{.Server.Version}}").strip()
        self.report["compose_version"] = self.docker("compose_version", "compose", "version", "--short").strip()
        if any(self.inventory().values()):
            raise SmokeError("preexisting_project_resources")
        # Names can collide even without Compose labels: reject instead of adopting.
        for kind, command, field in (("containers", ["ps", "-a"], "Names"),
                                     ("networks", ["network", "ls"], "Name"),
                                     ("volumes", ["volume", "ls"], "Name")):
            names = self.docker("collision_" + kind, *command, "--format", "{{." + field + "}}")
            if any(name == self.project or name.startswith((self.project + "_", self.project + "-"))
                   for name in names.splitlines()):
                raise SmokeError("preexisting_resource_name")
        images = self.docker("collision_image", "image", "ls", "--format", "{{.Repository}}")
        if self.project + "-mlflow" in images.splitlines():
            raise SmokeError("preexisting_build_image")
        self.env.update({
            "STACK_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            "STACK_MINIO_ROOT_USER": "s" + secrets.token_hex(8),
            "STACK_MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
            "MLFLOW_AUTH_ADMIN_USERNAME": "a" + secrets.token_hex(8),
            "MLFLOW_AUTH_ADMIN_PASSWORD": secrets.token_urlsafe(32),
            "MLFLOW_FLASK_SERVER_SECRET_KEY": secrets.token_urlsafe(32),
            "STACK_MLFLOW_PORT": str(self.port),
        })
        config = json.loads(self.compose("config", "config", "--format", "json"))
        for kind in ("networks", "volumes"):
            expected = {"default"} if kind == "networks" else {"postgres_data", "minio_data"}
            if set(config[kind]) != expected:
                raise SmokeError("unexpected_config_resources")
            for name, resource in config[kind].items():
                if resource.get("external") or resource["name"] != f"{self.project}_{name}":
                    raise SmokeError("unsafe_config_ownership")
        if set(config["services"]) != {"mlflow", "minio", "minio-init", "postgres"}:
            raise SmokeError("unexpected_services")
        for name, service in config["services"].items():
            if service.get("container_name") or service.get("network_mode"):
                raise SmokeError("unsafe_service_identity")
            ports = service.get("ports", [])
            if name == "mlflow":
                if len(ports) != 1 or ports[0] != {"mode": "ingress", "host_ip": "127.0.0.1", "target": 5000,
                                                            "published": str(self.port), "protocol": "tcp"}:
                    raise SmokeError("unsafe_config_ports")
            elif ports:
                raise SmokeError("unexpected_config_ports")

    def snapshot(self, label):
        inventory = self.inventory()
        snapshot = {kind: [] for kind in inventory}
        for kind, rows in inventory.items():
            for row in rows:
                identity = row["Name"] if kind == "volumes" else row["ID"]
                resource = {"containers": "container", "networks": "network", "volumes": "volume"}[kind]
                template = ('{{json .Id}}|{{json .Name}}|{{json .Labels}}' if kind != "containers" else
                            '{{json .Id}}|{{json .Name}}|{{json .Config.Labels}}|{{json .State.Status}}|{{json .NetworkSettings.Ports}}')
                if kind == "volumes":
                    # Docker volumes have names, not an independent immutable ID.
                    template = '{{json .Name}}|{{json .Name}}|{{json .Labels}}'
                fields = self.docker("inspect_" + kind, resource, "inspect", identity, "--format", template).strip().split("|")
                rid, name, labels = (json.loads(value) for value in fields[:3])
                name = name.lstrip("/")
                if labels.get("com.docker.compose.project") != self.project:
                    raise SmokeError("resource_ownership_mismatch")
                if kind == "containers":
                    service = labels.get("com.docker.compose.service")
                    if service not in {"postgres", "minio", "minio-init", "mlflow"} or name != f"{self.project}-{service}-1":
                        raise SmokeError("unexpected_container")
                    expected_files = f"{BASE / 'compose.yml'},{ROOT / 'compose.yml'}"
                    if (labels.get("com.docker.compose.project.config_files") != expected_files
                            or labels.get("com.docker.compose.project.working_dir") != str(BASE)):
                        raise SmokeError("unexpected_compose_origin")
                elif name not in {f"{self.project}_{suffix}" for suffix in
                                  (("default",) if kind == "networks" else ("postgres_data", "minio_data"))}:
                    raise SmokeError("unexpected_resource")
                if (label == "before_shutdown" and self.known[kind]
                        and rid not in self.known[kind]):
                    raise SmokeError("unknown_resource_before_shutdown")
                self.known[kind].add(rid)
                item = {"id": rid, "name": name}
                if kind == "containers":
                    item.update(service=service, status=json.loads(fields[3]), ports=json.loads(fields[4]))
                snapshot[kind].append(item)
            snapshot[kind].sort(key=lambda item: item["name"])
        self.report["snapshots"][label] = snapshot
        return snapshot

    def consumer(self, previous=None):
        environment = child_environment()
        environment.update({"PYTHONPATH": str(ML / "src"),
                            "MLFLOW_TRACKING_URI": f"http://127.0.0.1:{self.port}",
                            "MLFLOW_TRACKING_USERNAME": self.env["MLFLOW_AUTH_ADMIN_USERNAME"],
                            "MLFLOW_TRACKING_PASSWORD": self.env["MLFLOW_AUTH_ADMIN_PASSWORD"],
                            "MLFLOW_WORKSPACE": self.project,
                            "MLFLOW_DISABLE_AGENT_HINT": "1",
                            "MLFLOW_HTTP_REQUEST_TIMEOUT": "10", "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
                            "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false"})
        if previous:
            environment["STACK_SMOKE_RUN_ID"] = previous["run_id"]
        output = self.run("consumer_after" if previous else "consumer_before",
                          [str(ML / ".venv/bin/python"), "-B", "-c", CONSUMER], 90, environment, progress=True)
        result = validate_consumer(receipt(output), self.project, previous)
        if self.report["commands"][-1]["progress"] != {
                "events": progress_sequence(bool(previous)), "reason": "COMPLETE"}:
            raise SmokeError("consumer_progress_incomplete")
        return result

    def auth_negative(self):
        outcomes = {}
        opener = build_opener(ProxyHandler({}))
        for mode in ("anonymous", "invalid"):
            request = Request(f"http://127.0.0.1:{self.port}/api/2.0/mlflow/experiments/search",
                              data=b'{"max_results":1}', headers={"Content-Type": "application/json"})
            if mode == "invalid":
                token = base64.b64encode(b"invalid-user:invalid-password").decode()
                request.add_header("Authorization", "Basic " + token)
            try:
                with opener.open(request, timeout=10) as response:
                    status = response.status
            except HTTPError as error:
                status = error.code
                error.close()
            outcomes[mode] = status
            if status != 401:
                raise SmokeError("auth_negative_failed")
        self.report["auth"] = outcomes

    def execute(self):
        self.preflight()
        # Build/pull have backend effects even if their CLI times out.
        self.started = True
        self.compose("build", "build", "mlflow", timeout=600)
        self.compose("startup", "up", "-d", "--wait", "--wait-timeout", "180", timeout=240)
        before = self.snapshot("before")
        published = [(item["service"], port, binding) for item in before["containers"]
                     for port, bindings in (item["ports"] or {}).items() for binding in (bindings or [])]
        if published != [("mlflow", "5000/tcp", {"HostIp": "127.0.0.1", "HostPort": str(self.port)})]:
            raise SmokeError("runtime_port_contract_failed")
        self.report["auth"] = {}
        self.auth_negative()
        first = self.consumer()
        self.report["consumer_before"] = first
        mlflow_id = next(item["id"] for item in before["containers"] if item["service"] == "mlflow")
        self.report["server_version"] = self.docker("server_version", "exec", mlflow_id,
                    "python", "-c", "import mlflow; print(mlflow.__version__)").strip()
        obj = receipt(self.docker("minio_object", "exec", mlflow_id, "python", "-c", OBJECT_PROOF))
        if (set(obj) != {"key", "sha256", "bytes"} or obj["sha256"] != first["sha256"]
                or obj["bytes"] != len(PAYLOAD) or first["run_id"] not in obj["key"]):
            raise SmokeError("minio_object_mismatch")
        self.report["minio_object"] = obj
        self.compose("recreate_mlflow", "up", "-d", "--no-deps", "--force-recreate",
                     "--wait", "--wait-timeout", "120", "mlflow", timeout=150)
        after = self.snapshot("after_recreate")
        for kind in ("volumes", "networks"):
            if before[kind] != after[kind]:
                raise SmokeError("persistent_resource_changed")
        old = {item["service"]: item["id"] for item in before["containers"]}
        new = {item["service"]: item["id"] for item in after["containers"]}
        if old.keys() != new.keys() or old["mlflow"] == new["mlflow"] or any(
                old[name] != new[name] for name in old if name != "mlflow"):
            raise SmokeError("recreation_scope_failed")
        self.report["consumer_after"] = self.consumer(first)
        self.report["status"] = "PASS"

    def shutdown(self):
        if not self.started:
            self.report["shutdown"] = "NOT_NEEDED_NO_MUTATION_STARTED"
            return
        before = self.snapshot("before_shutdown")
        self.compose("shutdown", "down", "--timeout", "20", timeout=90)
        after = self.snapshot("stopped")
        if after["containers"] or after["networks"] or before["volumes"] != after["volumes"]:
            raise SmokeError("shutdown_preservation_failed")
        self.report["shutdown"] = "PASS_VOLUMES_PRESERVED"
        self.report["residual_image"] = self.project + "-mlflow:latest"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    # Exclusive creation prevents replacing an earlier failure report.
    validate_inputs(args.project, args.port)
    with args.report.open("x", encoding="utf-8") as output, tempfile.TemporaryDirectory(
            prefix="stack01-docker-", dir=TEMP_ROOT) as docker_config:
        harness = Harness(args.project, args.port)
        # Private CLI state contains no credentials, contexts or saved builders.
        harness.env = isolated_cli_environment(docker_config)
        start = time.monotonic()
        try:
            harness.execute()
        except (SmokeError, OSError, ValueError, KeyError, TypeError, StopIteration):
            error = sys.exc_info()[1]
            harness.report["failure"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
        finally:
            try:
                harness.shutdown()
            except (SmokeError, OSError, ValueError, KeyError, TypeError):
                error = sys.exc_info()[1]
                harness.report.update(status="FAIL", shutdown="FAILED_REQUIRES_REVIEW",
                                      shutdown_failure=str(error) if isinstance(error, SmokeError) else type(error).__name__)
            harness.report["duration_seconds"] = round(time.monotonic() - start, 3)
            json.dump(harness.report, output, indent=2, sort_keys=True)
            output.write("\n")
        print(json.dumps({"status": harness.report["status"], "project": args.project,
                          "report": str(args.report), "shutdown": harness.report.get("shutdown")}))
        return 0 if harness.report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
