"""Offline tests: only subprocess boundaries are replaced; no daemon or sockets."""

import ast
import contextlib
import copy
import fnmatch
import hashlib
import importlib.util
import io
import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.error import HTTPError as UrlHTTPError, URLError

from requests import Response
from requests.exceptions import ConnectionError as RequestsConnectionError, HTTPError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stack_model_api_smoke.py"
spec = importlib.util.spec_from_file_location("stack02_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
PROJECT = "stack02-proof-unit123"
PORTS = (15500, 15800)
FIXTURE = {"sdk": "3.16.0", "workspace": "default", "run_id": "a" * 32, "version": "1",
           "manifest": {"MLmodel": "b" * 64, "model.skops": "c" * 64}, "status": "FINISHED",
           "artifact_uri": "mlflow-artifacts:/workspaces/default/1/" + "a" * 32 + "/artifacts"}


class Runtime:
    """Model only Docker/HTTP/SDK process I/O, not any harness method."""

    def __init__(self):
        self.calls = []
        self.services = set()
        self.volumes = False
        self.alias = False
        self.active = False
        self.override = {}
        self.bad_labels = False
        self.collision = False
        self.port_collision = False
        self.fail_action = None
        self.timeout_action = None
        self.cleanup_failure = False
        self.extra = False
        self.ready_polls = 0
        self.init_repeats = 0
        self.restarts = 0
        self.initializer_exit_code = 0
        self.initializer_status = "exited"
        self.startup_missing = False
        self.identity_changes = {}
        self.inventory_rows = {}

    def identity(self, service):
        return self.identity_changes.get(service, str(sorted(smoke.SERVICES).index(service) + 1) * 64)

    def response(self, value):
        return smoke.MARKER + json.dumps(value)

    def config(self, env):
        services = {}
        for name in smoke.SERVICES:
            service = {"networks": {"default": None}, "environment": {}}
            if name in {"mlflow", "model-api"}:
                port, target = (PORTS[0], 5000) if name == "mlflow" else (PORTS[1], 8000)
                service["ports"] = [{"mode": "ingress", "host_ip": "127.0.0.1", "target": target,
                                     "published": str(port), "protocol": "tcp"}]
                service["build"] = {"context": str(smoke.BASE if name == "mlflow" else smoke.API),
                                    "dockerfile": "Dockerfile.mlflow" if name == "mlflow" else "Dockerfile"}
            if name == "model-api":
                service["environment"] = {
                    "MLFLOW_TRACKING_URI": "http://mlflow:5000",
                    "MLFLOW_TRACKING_USERNAME": env["STACK_MODEL_API_MLFLOW_USERNAME"],
                    "MLFLOW_TRACKING_PASSWORD": env["STACK_MODEL_API_MLFLOW_PASSWORD"],
                    "MODEL_API_ADMIN_TOKEN": env["MODEL_API_ADMIN_TOKEN"],
                    "MODEL_REFRESH_TIMEOUT_SECONDS": "120", "MODEL_STARTUP_TIMEOUT_SECONDS": "180"}
            if name == "mlflow":
                database = "postgresql+psycopg2://mlflow:" + env["STACK_POSTGRES_PASSWORD"] + "@postgres:5432/mlflow"
                service["environment"] = {"MLFLOW_ARTIFACTS_DESTINATION": "s3://mlflow-artifacts",
                                          "MLFLOW_BACKEND_STORE_URI": database,
                                          "MLFLOW_AUTH_DATABASE_URI": database,
                                          "MLFLOW_S3_ENDPOINT_URL": "http://minio:9000",
                                          "AWS_ACCESS_KEY_ID": env["STACK_MINIO_ROOT_USER"],
                                          "AWS_SECRET_ACCESS_KEY": env["STACK_MINIO_ROOT_PASSWORD"]}
            services[name] = service
        config = {"services": services, "networks": {"default": {"name": PROJECT + "_default"}},
                  "volumes": {v: {"name": PROJECT + "_" + v} for v in ("postgres_data", "minio_data")}}
        change = self.override.get("config")
        if change:
            change(config)
        return config

    def rows(self, kind, selection):
        if kind == "containers":
            rows = [{"Names": PROJECT + "-" + s + "-1", "ID": str(sorted(smoke.SERVICES).index(s) + 1) * 12,
                     "Labels": "com.docker.compose.project=" + PROJECT} for s in sorted(self.services)]
            if self.port_collision:
                rows.append({"Names": "unrelated", "Ports": "127.0.0.1:15500->5000/tcp"})
            if self.extra:
                rows.append({"Names": PROJECT + "-intruder-1", "Labels": ""})
        elif kind == "networks":
            rows = [{"Name": PROJECT + "_default"}] if self.services else []
        elif kind == "volumes":
            rows = [{"Name": PROJECT + "_" + v} for v in ("postgres_data", "minio_data")] if self.volumes else []
        else:
            rows = []
        if self.collision:
            rows.append({"Name": PROJECT + "_existing"})
        rows += self.inventory_rows.get(kind, [])
        key, value = selection.split("=", 1)
        if key == "label":
            rows = [row for row in rows if value in row.get("Labels", "").split(",")]
        elif key == "name":
            rows = [row for row in rows if re.search(value, row.get("Names", row.get("Name", "")))]
        elif key == "reference":
            rows = [row for row in rows if fnmatch.fnmatchcase(row.get("Repository", ""), value)
                    or fnmatch.fnmatchcase(row.get("Repository", "") + ":" + row.get("Tag", ""), value)]
        elif key == "publish":
            port, protocol = value.split("/")
            rows = [row for row in rows if re.search(
                ":" + port + r"->[^,]*/" + protocol + r"(?:,|$)", row.get("Ports", ""))]
        else:
            raise AssertionError("Unexpected inventory filter")
        return "\n".join(json.dumps(row) for row in rows)

    def inspect(self, args):
        kind, _, name = args[:3]
        if args[-1].startswith('{"name":'):
            rows = []
            for target in args[2:args.index("--format")]:
                if kind == "container":
                    service = next((s for s in smoke.SERVICES if target in {
                        self.identity(s), str(sorted(smoke.SERVICES).index(s) + 1) * 64}),
                        target[len(PROJECT) + 1:-2])
                    container_name = PROJECT + "-" + service + "-1"
                    tags, identity, ports, image = map(json.loads, self.inspect([
                        kind, "inspect", container_name, "--format", "legacy"]).split("|"))
                    mount = {"postgres": ("postgres_data", "/var/lib/postgresql/data"),
                             "minio": ("minio_data", "/data")}.get(service)
                    mounts = [{"Type": "volume", "Name": PROJECT + "_" + mount[0],
                               "Destination": mount[1], "RW": True}] if mount else []
                    rows.append({"name": "/" + container_name, "id": identity, "labels": tags,
                                 "ports": ports, "image": image, "mounts": mounts,
                                 "networks": {PROJECT + "_default": {"NetworkID": "d" * 64}},
                                 "network_mode": PROJECT + "_default"})
                else:
                    actual_name = PROJECT + "_default" if kind == "network" else target
                    tags, identity = map(json.loads, self.inspect([
                        kind, "inspect", actual_name, "--format", "legacy"]).split("|"))
                    row = {"name": actual_name, "id": identity, "labels": tags}
                    if kind == "network":
                        row["members"] = {self.identity(s): {"Name": PROJECT + "-" + s + "-1"}
                                          for s in self.services - {"minio-init"}}
                    rows.append(row)
            return "\n".join(json.dumps(row) for row in rows)
        tags = {"com.docker.compose.project": "other" if self.bad_labels else PROJECT}
        if kind == "container":
            service = next((s for s in smoke.SERVICES if name == self.identity(s)),
                           name[len(PROJECT) + 1:-2])
            if ".State.Status" in args[-1]:
                status = self.initializer_status if self.init_repeats else "exited"
                code = self.initializer_exit_code if self.init_repeats else 0
                return "|".join(map(json.dumps, [self.identity(service), status, code, "sha256:" + "a" * 64]))
            tags.update({"com.docker.compose.service": service,
                         "com.docker.compose.project.config_files": ",".join(map(str, smoke.FILES)),
                         "com.docker.compose.project.working_dir": str(smoke.BASE)})
            identity = self.identity(service) if service in smoke.SERVICES else "9" * 64
            ports = {"mlflow": {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(PORTS[0])}]},
                     "model-api": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(PORTS[1])}]}}.get(service, {})
            image = "sha256:" + ("e" if service == "mlflow" else "f") * 64
            return "|".join(map(json.dumps, [tags, identity, ports, image]))
        if kind == "image":
            return json.dumps("sha256:" + ("e" if "-mlflow:" in name else "f") * 64) + "|" + json.dumps("app")
        tags["com.docker.compose.network" if kind == "network" else "com.docker.compose.volume"] = name[len(PROJECT) + 1:]
        return json.dumps(tags) + "|" + json.dumps("d" * 64 if kind == "network" else name)

    def sdk(self, action):
        base = {"sdk": "3.16.1", "workspace": "default"}
        if action == "bootstrap":
            return dict(base, experiment="1", reader_nonadmin=True, exact_read_role=True)
        if action == "fixture":
            self.alias = True
            return copy.deepcopy(FIXTURE)
        if action == "objects":
            return dict(workspace="default", manifest=FIXTURE["manifest"], transport="direct_s3",
                        boto3="1.42.0", mlflow_installed="3.16.1")
        if action == "reader":
            return dict(base, sdk="3.16.0", download_hashes=True, reads=True, creates_denied=True, writes_denied=True)
        self.alias = action == "restore_alias"
        return dict(base, alias_action=action)

    def http(self, data):
        url = data["url"]
        if url.endswith("/health") and "/models/" not in url:
            if self.ready_polls:
                self.ready_polls -= 1
                return {"status": 0, "body": {}, "transport": "indeterminate"}
            return {"status": 200, "body": {"status": "ok"}}
        if "/admin/" in url:
            if data["headers"].get("X-Model-Admin-Token") != self.secret:
                return {"status": 401, "body": {}}
            return {"status": 200 if self.alias else 503, "body": {}}
        if not self.active:
            return {"status": 503, "body": {}}
        body = {"model_id": "invoice-review", "model_version": "1", "run_id": "a" * 32,
                "registered_model": "invoice-review-production", "alias": "champion"}
        if url.endswith("/predict"):
            body["probability"] = 0.5
        return {"status": 200, "body": body}

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        self.secret = kwargs["env"].get("MODEL_API_ADMIN_TOKEN", getattr(self, "secret", None))
        data = json.loads(kwargs["input"]) if kwargs.get("input") else None
        out = ""
        action = ""
        if Path(argv[0]).name in {smoke.DOCKER, smoke.COMPOSE}:
            args = argv[3:]
            if args[:1] == ["ps"]:
                out = self.rows("containers", args[args.index("--filter") + 1])
            elif len(args) > 1 and args[1] == "ls":
                out = self.rows({"network": "networks", "volume": "volumes", "image": "images"}[args[0]],
                                args[args.index("--filter") + 1])
            elif len(args) > 1 and args[1] == "inspect":
                out = self.inspect(args)
            elif args[:2] == ["exec", "-i"]:
                action = args[-1]
                target = args[args.index("python") - 1]
                if target not in {self.identity(s) for s in self.services}:
                    raise AssertionError("SDK must target an owned immutable container ID")
                out = self.response(self.sdk(action))
            elif args[:2] == ["start", "--attach"]:
                if args[2:] != [self.identity("minio-init")]:
                    raise AssertionError("Initializer restart must target its proven immutable ID")
                action = "minio_repeat"
                self.init_repeats += 1
            else:
                self.assert_compose([Path(argv[0]).name, *args])
                command = args[12:]
                action = command[0]
                if action == "config":
                    out = json.dumps(self.config(kwargs["env"]))
                elif action == "up":
                    if command[-1] == "model-api":
                        self.services.add("model-api")
                    else:
                        self.services = smoke.SERVICES - {"model-api"}
                        self.volumes = True
                elif action == "exec":
                    action = command[-1]
                    out = self.response(self.sdk(action))
                elif action == "restart":
                    if command != ["restart", "--timeout", "10", "model-api"]:
                        raise AssertionError("Unexpected restart target or stop budget")
                    self.restarts += 1
                    self.active = self.alias and not self.startup_missing
                elif action == "down":
                    if self.cleanup_failure:
                        return subprocess.CompletedProcess(argv, 1, "secret-child", "secret-child")
                    self.services.clear()
                elif action != "build":
                    raise AssertionError("Unexpected compose command")
        elif "-c" in argv and argv[-1] == smoke.HTTP:
            action = "http"
            out = self.response(self.http(data))
        elif argv[-1] == str(smoke.API / "scripts/model_release_smoke.py"):
            action = "release"
            self.active = True
            out = "Model release smoke passed"
        else:
            raise AssertionError("Unexpected subprocess")
        if action == self.timeout_action:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="secret-child")
        if action == self.fail_action:
            return subprocess.CompletedProcess(argv, 1, "secret-child", "secret-child")
        override = self.override.get(action)
        if override and action != "config":
            if action in {"http", "fixture", "objects", "reader", "bootstrap"}:
                value = json.loads(out.split(smoke.MARKER)[1])
                override(value)
                out = self.response(value)
        return subprocess.CompletedProcess(argv, 0, out, "")

    def assert_compose(self, args):
        expected = [smoke.COMPOSE, "-p", PROJECT, "--project-directory", str(smoke.BASE), "--env-file", "/dev/null"]
        for path in smoke.FILES:
            expected += ["-f", str(path)]
        if args[:len(expected)] != expected:
            raise AssertionError("Unsafe compose origin")


class SmokeTests(unittest.TestCase):
    def test_shutdown_uses_cleanup_reserve_before_at_and_after_1680(self):
        for elapsed in (1679, 1680, 1690):
            with self.subTest(elapsed=elapsed):
                runtime = Runtime()
                clock = [0.0]
                with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(smoke.subprocess, "run", side_effect=runtime):
                    harness = self.harness()
                    harness.execute()
                    clock[0] = elapsed
                    runtime.calls.clear()
                    harness.shutdown()
                self.assertEqual(harness.report["shutdown"], "PASS_VOLUMES_IMAGES_PRESERVED")
                self.assertTrue(any("down" in argv for argv, _ in runtime.calls))
                self.assertEqual(harness.deadline, 1800)
                for _, kwargs in runtime.calls:
                    self.assertLessEqual(kwargs["timeout"], 1800 - elapsed)

    def test_shutdown_post_down_checks_use_remaining_total_budget(self):
        runtime = Runtime()
        actual = runtime.__call__
        clock, observed = [0.0], []

        def boundary(argv, **kwargs):
            if "down" in argv:
                clock[0] = 1795
            elif clock[0] >= 1795:
                observed.append(kwargs["timeout"])
            return actual(argv, **kwargs)
        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(smoke.subprocess, "run", side_effect=boundary):
            harness = self.harness()
            harness.execute()
            clock[0] = 1780
            harness.shutdown()
        self.assertEqual(harness.report["shutdown"], "PASS_VOLUMES_IMAGES_PRESERVED")
        self.assertGreater(len(observed), 0)
        self.assertEqual(set(observed), {5})

    def test_shutdown_stops_dispatch_at_absolute_deadline_including_post_down(self):
        for after_down in (False, True):
            with self.subTest(after_down=after_down):
                runtime = Runtime()
                actual = runtime.__call__
                clock = [0.0]

                def boundary(argv, **kwargs):
                    result = actual(argv, **kwargs)
                    if "down" in argv:
                        clock[0] = 1800
                    return result
                with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(smoke.subprocess, "run", side_effect=boundary):
                    harness = self.harness()
                    harness.execute()
                    runtime.calls.clear()
                    clock[0] = 1710 if after_down else 1800
                    with self.assertRaisesRegex(smoke.Failure, "total_deadline"):
                        harness.shutdown()
                self.assertEqual(bool(runtime.calls), after_down)
                if after_down:
                    self.assertIn("down", runtime.calls[-1][0])
                self.assertNotEqual(harness.report["shutdown"], "PASS_VOLUMES_IMAGES_PRESERVED")

    def test_readonly_defaults_preserve_overrides_and_remaining_budget_clamp(self):
        queries = {
            "inventory_containers": ("ps", "-a"), "inventory_networks": ("network", "ls"),
            "inventory_volumes": ("volume", "ls"), "inventory_images": ("image", "ls"),
            "inspect_containers": ("container", "inspect", "unit-id"),
            "inspect_networks": ("network", "inspect", "unit-id"),
            "inspect_volumes": ("volume", "inspect", "unit-volume"),
            "inspect_images": ("image", "inspect", "unit-image"),
            "compose_config": ("config", "--format", "json"),
        }
        self.assertEqual(smoke.DOCKER_READ_OPERATIONS, set(queries))
        clock = [0.0]
        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess(
                    ["unit-command"], 0, "unit-output", "")) as boundary:
            for operation, args in queries.items():
                with self.subTest(operation=operation):
                    harness = self.harness()
                    dispatch = harness.compose if operation == "compose_config" else harness.docker
                    dispatch(*args, operation=operation)
                    self.assertEqual(boundary.call_args.kwargs["timeout"], 60)
                    dispatch(*args, operation=operation, timeout=7)
                    self.assertEqual(boundary.call_args.kwargs["timeout"], 7)
                    clock[0] += 1665  # 1800 total minus 1665 elapsed minus 120 cleanup leaves 15.
                    dispatch(*args, operation=operation)
                    self.assertEqual(boundary.call_args.kwargs["timeout"], 15)
            harness = self.harness()
            harness.run(["unit-command"])
            self.assertEqual(boundary.call_args.kwargs["timeout"], 30)
            harness.docker("stop", "unit-id")  # Unclassified operations never get the read allowance.
            self.assertEqual(boundary.call_args.kwargs["timeout"], 30)

    def test_45_second_read_query_succeeds_once_without_changing_mutation_budgets(self):
        runtime = Runtime()
        actual = runtime.__call__
        clock, queries = [0.0], []
        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]):
            harness = self.harness()
            def boundary(argv, **kwargs):
                if harness.phase == "base_start" and argv[3:4] == ["ps"] and not queries:
                    queries.append(kwargs["timeout"])
                    clock[0] += min(45, kwargs["timeout"])
                    if kwargs["timeout"] < 45:
                        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                return actual(argv, **kwargs)
            with patch.object(smoke.subprocess, "run", side_effect=boundary):
                report = harness.finish()
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(queries, [60])
        self.assertEqual(clock[0], 45)
        self.assertEqual(harness.deadline, 1800)
        expected = {"build": 600, "start": 30, "restart": 30, "down": 90}
        for argv, kwargs in runtime.calls:
            for command, seconds in expected.items():
                if command in argv:
                    self.assertEqual(kwargs["timeout"], seconds)
            if smoke.SDK in argv:
                self.assertEqual(kwargs["timeout"], 150)
            if "--wait" in argv:
                self.assertEqual(kwargs["timeout"], 360)
                self.assertEqual(argv[argv.index("--wait-timeout") + 1], "330")
        self.assertEqual(smoke.HTTP_REFRESH_SECONDS, 125)
        self.assertEqual(smoke.READINESS_SECONDS, 331)

    def test_full_container_json_can_stall_without_blocking_minimal_inventory_workflow(self):
        runtime = Runtime()
        actual = runtime.__call__
        formats = []
        expected = '{"ID":{{json .ID}},"Names":{{json .Names}},"Labels":{{json .Labels}},"Ports":{{json .Ports}}}'
        def boundary(argv, **kwargs):
            if argv[3:4] == ["ps"]:
                template = argv[argv.index("--format") + 1]
                if template == "{{json .}}":
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="private-full-context")
                self.assertEqual(template, expected)
                self.assertIn("--no-trunc", argv)
                formats.append(template)
            return actual(argv, **kwargs)
        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            report = self.harness().finish()
        self.assertEqual(report["status"], "PASS", report)
        self.assertGreater(len(formats), 0)
        self.assertEqual(set(report["proof_status"].values()), {"PASS"})
        self.assertFalse(any(argv[3:4] == ["ps"] and "{{json .}}" in argv for argv, _ in runtime.calls))

    def test_post_up_minimal_inventory_timeout_keeps_resources_uncertain_and_blocks_down(self):
        runtime = Runtime()
        actual = runtime.__call__
        clock = [0.0]
        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]):
            harness = self.harness()
            def boundary(argv, **kwargs):
                if harness.phase == "base_start" and argv[3:4] == ["ps"]:
                    self.assertEqual(runtime.services, smoke.SERVICES - {"model-api"})
                    self.assertEqual(kwargs["timeout"], 60)
                    clock[0] += 60
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="private-query")
                return actual(argv, **kwargs)
            with patch.object(smoke.subprocess, "run", side_effect=boundary):
                report = harness.finish()
        self.assertEqual(report["failure"], {"phase": "base_start", "code": "timeout_backend_cancellation_not_proven"})
        self.assertEqual(report["operation"], {"category": "inventory_containers", "status": "timeout", "elapsed_seconds": 60})
        self.assertEqual(report["snapshots"], {})
        self.assertEqual(runtime.services, smoke.SERVICES - {"model-api"})
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
        self.assertFalse(any("down" in c or "exec" in c for c, _ in runtime.calls))
        self.assertNotIn("private-", json.dumps(report))

    def real_sdk_check(self, program):
        """Use the existing pinned SDK; HTTP is replaced before any client call."""
        environment = smoke.shared.child_environment()
        with tempfile.TemporaryDirectory(prefix="stack02-sdk-boundary-") as directory:
            environment.update(HOME=directory, MLFLOW_DISABLE_TELEMETRY="true",
                               MLFLOW_TRACKING_USERNAME="synthetic-reader",
                               MLFLOW_TRACKING_PASSWORD="synthetic-password",
                               MLFLOW_TRACKING_URI="http://sdk-boundary.invalid",
                               MLFLOW_HTTP_REQUEST_MAX_RETRIES="0")
            process = subprocess.run([str(smoke.API / ".venv/bin/python"), "-I", "-B", "-c", program],
                                     input=json.dumps({"source": smoke.SDK}), env=environment, cwd=smoke.ROOT,
                                     capture_output=True, text=True, check=False, timeout=60)
        lines = re.findall(r'File "<string>", line ([0-9]{1,4})', process.stderr)[-3:]
        self.assertEqual(process.returncode, 0, "Actual pinned SDK boundary failed at script lines "
                         + repr(lines) + "; provider output suppressed")
        self.assertEqual(process.stdout.strip(), "PINNED_SDK_BOUNDARY_PASS")

    def test_all_action_exception_hook_suppresses_raw_traceback_and_reports_source(self):
        prefix = smoke.SDK.split("action = sys.argv[1]", 1)[0]
        for stage in sorted(smoke.SDK_STAGES):
            with self.subTest(stage=stage):
                program = prefix + f'\nstage = {stage!r}\nraise AssertionError("private-provider-password-url")\n'
                process = subprocess.run([smoke.sys.executable, "-I", "-B", "-c", program],
                                         input="{}", capture_output=True, text=True, timeout=10, check=False)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(process.stderr, "")
                self.assertNotIn("private-provider", process.stdout)
                diagnostic = smoke.reader_diagnostic(process.stdout)
                self.assertIsNotNone(diagnostic)
                self.assertEqual(diagnostic["stage"], stage)
                self.assertEqual(diagnostic["exception"], "AssertionError")
                self.assertEqual(diagnostic["source_line"], len(program.splitlines()))

    def test_fixture_nonzero_retains_safe_diagnostic_without_passing(self):
        runtime = Runtime()
        original = runtime.__call__
        diagnostic = {"stage": "save", "exception": "MlflowException", "http_status": 500,
                      "source_line": 100}
        def failing(argv, **kwargs):
            if smoke.SDK in argv and argv[-1] == "fixture":
                return subprocess.CompletedProcess(argv, 1,
                    smoke.SDK_DIAGNOSTIC_MARKER + json.dumps(diagnostic), "private-provider-output")
            return original(argv, **kwargs)
        with patch.object(smoke.subprocess, "run", side_effect=failing):
            report = self.harness().finish()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["sdk_diagnostic"], diagnostic)
        self.assertNotIn("private-provider", json.dumps(report))

    def test_actual_sdk_unhandled_hook_keeps_exact_status_without_provider_details(self):
        prefix = smoke.SDK.split("action = sys.argv[1]", 1)[0]
        cases = [
            ('from mlflow.exceptions import MlflowException\n'
             'from mlflow.protos.databricks_pb2 import PERMISSION_DENIED\n'
             'raise MlflowException("private-provider-password-url", error_code=PERMISSION_DENIED)\n',
             "MlflowException"),
            ('from requests import Response\nfrom requests.exceptions import HTTPError\n'
             'response = Response()\nresponse.status_code = 403\n'
             'raise HTTPError("private-provider-password-url", response=response)\n', "HTTPError"),
        ]
        for failure, kind in cases:
            with self.subTest(kind=kind):
                environment = smoke.shared.child_environment()
                environment["MLFLOW_DISABLE_TELEMETRY"] = "true"
                program = prefix + '\nstage = "save"\n' + failure
                process = subprocess.run([str(smoke.API / ".venv/bin/python"), "-I", "-B", "-c", program],
                                         input="{}", env=environment, capture_output=True, text=True, timeout=60, check=False)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(process.stderr, "")
                self.assertNotIn("private-provider", process.stdout)
                diagnostic = smoke.reader_diagnostic(process.stdout)
                self.assertIsNotNone(diagnostic)
                self.assertEqual(diagnostic["exception"], kind)
                self.assertEqual(diagnostic["http_status"], 403)

    def test_sdk_source_line_diagnostic_rejects_invalid_types_and_out_of_bounds(self):
        diagnostic = {"stage": "save", "exception": "other", "http_status": 0}
        for line in (True, "1", 1.5, 0, -1, len(smoke.SDK.splitlines()) + 1):
            with self.subTest(line=line):
                output = smoke.SDK_DIAGNOSTIC_MARKER + json.dumps({**diagnostic, "source_line": line})
                self.assertIsNone(smoke.reader_diagnostic(output))

    def test_s3_diagnostic_rejects_unknown_codes_and_extra_response_fields(self):
        base = {"stage": "objects", "exception": "ClientError", "http_status": 403,
                "source_line": 100, "error_code": "AccessDenied"}
        self.assertEqual(smoke.reader_diagnostic(smoke.SDK_DIAGNOSTIC_MARKER + json.dumps(base)), base)
        for extra in ({"error_code": "private-code"}, {"RequestId": "private-id"},
                      {"error_code": []}, {"http_status": True}):
            self.assertIsNone(smoke.reader_diagnostic(smoke.SDK_DIAGNOSTIC_MARKER + json.dumps({**base, **extra})))

    def test_objects_receipt_distinguishes_transport_from_installed_mlflow_metadata(self):
        value = Runtime().sdk("objects")
        smoke.validate_objects(value, FIXTURE)
        for changed in ({"transport": "mlflow_sdk"}, {"mlflow_installed": "3.16.0"},
                        {"boto3": "private-message"}, {"sdk": "3.16.1"},
                        {"manifest": {"MLmodel": "0" * 64}}):
            with self.subTest(changed=changed):
                with self.assertRaises(smoke.Failure):
                    smoke.validate_objects({**value, **changed}, FIXTURE)

    def test_release_nonzero_retains_only_exact_terminal_http_diagnostic(self):
        harness = self.harness()
        error = "Model release smoke failed: POST /admin/models/invoice-review/refresh failed with HTTP 503\n"
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["release"], 1, "private-output", error)):
            with self.assertRaises(smoke.Failure):
                harness.run(["release"], operation="release")
        self.assertEqual(harness.report.get("release_diagnostic"),
                         {"stage": "refresh", "category": "http_response", "http_status": 503})
        self.assertNotIn("private-output", json.dumps(harness.report))

    def test_release_diagnostic_whitelists_paths_outcomes_and_statuses(self):
        prefix = "Model release smoke failed: "
        for suffix, stage, category, status in (
                ("POST /admin/models/invoice-review/refresh failed with HTTP 503", "refresh", "http_response", 503),
                ("GET /models/invoice-review/health failed with HTTP 404", "health", "http_response", 404),
                ("POST /models/invoice-review/predict timed out", "predict", "timeout", 0),
                ("POST /admin/models/invoice-review/refresh failed to reach the Model API", "refresh", "unreachable", 0)):
            self.assertEqual(smoke.release_diagnostic(prefix + suffix),
                             {"stage": stage, "category": category, "http_status": status})
        for value in (prefix + "private-url-password", prefix + "GET /foreign failed with HTTP 403",
                      prefix + "POST /admin/models/invoice-review/refresh failed with HTTP 200",
                      prefix + "POST /admin/models/invoice-review/refresh failed with HTTP 503 private-message",
                      prefix + "x" * 300, prefix + "x\n" + prefix + "y"):
            self.assertIsNone(smoke.release_diagnostic(value))

    def test_sdk_progress_retains_only_constant_stage_from_text_or_timeout_bytes(self):
        for value in ("STACK02_PROGRESS=imports\nSTACK02_PROGRESS=save\nprivate-provider-output",
                      b"STACK02_PROGRESS=imports\nSTACK02_PROGRESS=save\nprivate-provider-output"):
            self.assertEqual(smoke.sdk_progress(value), "save")
        for value in (None, "private-output", "STACK02_PROGRESS=private-password-url", b"\xff"):
            self.assertIsNone(smoke.sdk_progress(value))

    def test_sdk_timeout_retains_flushed_progress_without_passing_or_claiming_cancellation(self):
        harness = self.harness()
        output = b"STACK02_PROGRESS=process_start\nSTACK02_PROGRESS=fit\nprivate-provider-output"
        with patch.object(smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired("unit", 150, output=output)):
            with self.assertRaises(smoke.Failure):
                harness.run(["unit"], timeout=150, operation="sdk_fixture")
        self.assertEqual(harness.report["sdk_last_stage"], "fit")
        self.assertEqual(harness.report["operation"]["status"], "timeout")
        self.assertTrue(harness.uncertain)
        self.assertNotIn("private-provider", json.dumps(harness.report))

    def test_real_sdk_prefix_flushes_decode_and_import_progress_before_stall(self):
        prefix = smoke.SDK.split("action = sys.argv[1]", 1)[0]
        with self.assertRaises(subprocess.TimeoutExpired) as result:
            subprocess.run([smoke.sys.executable, "-I", "-B", "-c", prefix + "\nimport time; time.sleep(30)"],
                           input="{}", text=True, capture_output=True, timeout=1, check=False)
        self.assertEqual(smoke.sdk_progress(result.exception.stdout), "imports")
        self.assertIn(b"STACK02_PROGRESS=process_start", result.exception.stdout)

    def test_actual_sdk_reader_denials_accept_plaintext_and_json_403_only(self):
        self.real_sdk_check(r'''
import ast, json, socket, sys, tempfile
from pathlib import Path
from unittest.mock import patch
import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException, RestException
from requests import Response
from requests.exceptions import HTTPError, ConnectionError
assert mlflow.__version__ == "3.16.0"
tree = ast.parse(json.load(sys.stdin)["source"])
helpers = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in {"denied", "artifact_denied"}]
assert len(helpers) == 2
namespace = dict(MlflowException=MlflowException, RestException=RestException, HTTPError=HTTPError)
exec(compile(ast.Module(body=helpers, type_ignores=[]), "<actual-reader-helpers>", "exec"), namespace)
run_id = "a" * 32
mode = [403, False]
observed = []
def http(method, url, *args, **kwargs):
    observed.append(method)
    response = Response()
    response.url = url
    if method == "GET" and url.endswith("/runs/get"):
        response.status_code = 200
        body = {"run": {"info": {"run_id": run_id, "experiment_id": "1", "status": "FINISHED",
                "artifact_uri": "mlflow-artifacts:/1/" + run_id + "/artifacts", "lifecycle_stage": "active"}, "data": {}}}
        response._content = json.dumps(body).encode()
    elif method == "GET" and url.endswith("/registered-models/get"):
        response.status_code = 200
        response._content = b'{"registered_model":{"name":"synthetic-model","tags":[]}}'
    elif method in {"POST", "PATCH", "PUT"}:
        status, structured = mode
        if status == "transport":
            raise ConnectionError("private-provider-transport")
        response.status_code = status
        code = {401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "RESOURCE_DOES_NOT_EXIST", 500: "INTERNAL_ERROR"}
        response._content = (json.dumps({"error_code": code.get(status, "INTERNAL_ERROR"),
                                        "message": "private-provider-detail"}).encode() if structured else b"Permission denied")
        if status == 200:
            response._content = b'{"experiment_id":"99"}'
    else:
        raise AssertionError("unexpected_http_boundary")
    return response
with patch.object(socket.socket, "connect", side_effect=AssertionError("network_forbidden")), \
        patch("mlflow.utils.rest_utils._get_http_response_with_retries", side_effect=http):
    client = MlflowClient()
    calls = [lambda: client.create_experiment("synthetic-denied"),
             lambda: client.create_registered_model("synthetic-denied"),
             lambda: client.set_experiment_tag("1", "denied", "true"),
             lambda: client.update_registered_model("synthetic-model", "denied"),
             lambda: client.create_run("1"),
             lambda: client.set_registered_model_alias("synthetic-model", "denied", "1")]
    for structured in (False, True):
        mode[:] = [403, structured]
        for call in calls:
            namespace["denied"](call)
    for status in (401, 404, 500, "transport", 200):
        for structured in (False, True):
            mode[:] = [status, structured]
            try:
                namespace["denied"](calls[0])
            except (AssertionError, MlflowException):
                pass
            else:
                raise AssertionError("non_403_accepted")
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "denied.txt"
        source.write_bytes(b"synthetic denied upload")
        for status in (403, 401, 404, 500, "transport", 200):
            mode[:] = [status, False]
            call = lambda: client.log_artifact(run_id, str(source), "denied")
            if status == 403:
                namespace["artifact_denied"](call)
            else:
                try:
                    namespace["artifact_denied"](call)
                except (AssertionError, MlflowException):
                    pass
                else:
                    raise AssertionError("artifact_non_403_accepted")
assert observed.count("PUT") == 6
print("PINNED_SDK_BOUNDARY_PASS")
''')

    def test_actual_sdk_run_download_survives_denied_optional_logged_model_search(self):
        self.real_sdk_check(r'''
import hashlib, json, socket, tempfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
import mlflow
from requests import Response
assert mlflow.__version__ == "3.16.0"
run_id = "a" * 32
root = "workspaces/default/1/" + run_id + "/artifacts"
files = {name: ("synthetic-" + name).encode() for name in
         ("MLmodel", "model.skops", "conda.yaml", "python_env.yaml", "requirements.txt")}
seen = []
def http(method, url, *args, **kwargs):
    response = Response()
    response.status_code = 200
    response.url = url
    path = urlsplit(url).path
    if path.endswith("/runs/get"):
        body = {"run": {"info": {"run_id": run_id, "experiment_id": "1", "status": "FINISHED",
                "artifact_uri": "mlflow-artifacts:/" + root, "lifecycle_stage": "active"}, "data": {}}}
    elif path.endswith("/server-info"):
        body = {"multipart_downloads_enabled": False}
    elif path.endswith("/logged-models/search"):
        seen.append("optional_search_denied")
        response.status_code = 403
        body = None
        response._content = b"Permission denied"
    elif path.endswith("/mlflow-artifacts/artifacts"):
        assert kwargs["params"]["path"] == root + "/model"
        body = {"files": [{"path": name, "is_dir": False, "file_size": len(payload)} for name, payload in files.items()]}
    elif "/mlflow-artifacts/artifacts/" in path:
        assert path.endswith(root + "/model/" + path.rsplit("/", 1)[-1])
        name = path.rsplit("/", 1)[-1]
        seen.append("file_download")
        body = None
        response._content = files[name]
    else:
        raise AssertionError("unexpected_http_boundary")
    if body is not None:
        response._content = json.dumps(body).encode()
    response._content_consumed = True
    return response
with patch.object(socket.socket, "connect", side_effect=AssertionError("network_forbidden")), \
        patch("mlflow.utils.rest_utils._get_http_response_with_retries", side_effect=http), \
        tempfile.TemporaryDirectory() as directory:
    downloaded = Path(mlflow.MlflowClient().download_artifacts(run_id, "model", directory))
    actual = {p.relative_to(downloaded).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in downloaded.rglob("*") if p.is_file()}
    assert actual == {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()}
assert seen.count("optional_search_denied") == 1
assert seen.count("file_download") == 5
print("PINNED_SDK_BOUNDARY_PASS")
''')

    def embedded_reader(self, failure_stage=None):
        tree = ast.parse(smoke.SDK)
        step = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reader_step")
        progress = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "progress")
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                      and any(isinstance(v, ast.Constant) and v.value == "reader" for v in n.test.comparators))
        class MlflowBoundary(Exception):
            def __init__(self, status):
                self.status = status
            def get_http_status_code(self):
                return self.status
            def __str__(self):
                raise AssertionError("Provider exception rendering is forbidden")
        class RestBoundary(MlflowBoundary):
            pass
        payloads = {name: ("synthetic-" + name).encode() for name in
                    ("MLmodel", "model.skops", "conda.yaml", "python_env.yaml", "requirements.txt")}
        data = copy.deepcopy(FIXTURE)
        data.update(project=PROJECT, experiment="1", manifest={
            name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()})
        calls = []
        def observed(stage, value=None):
            calls.append(stage)
            if stage == failure_stage and stage != "manifest":
                raise MlflowBoundary(401)
            return value
        def download(run_id, path, directory):
            observed("download")
            self.assertEqual((run_id, path), (data["run_id"], "model"))
            target = Path(directory) / "model"
            target.mkdir()
            for name, payload in payloads.items():
                (target / name).write_bytes(b"tampered" if failure_stage == "manifest" else payload)
            return str(target)
        def denied(stage):
            observed(stage)
            raise MlflowBoundary(403)  # Plain-text 403 is not a RestException.
        def artifact(*args):
            observed("deny_artifact_write")
            response = Response()
            response.status_code = 403
            raise HTTPError("private-provider-detail", response=response)
        client = SimpleNamespace(
            get_experiment=lambda *_: observed("read_experiment", SimpleNamespace(experiment_id="1")),
            get_model_version_by_alias=lambda *_: observed("read_alias", SimpleNamespace(run_id=data["run_id"], version="1")),
            get_run=lambda *_: observed("read_run", SimpleNamespace(info=SimpleNamespace(status="FINISHED"))),
            download_artifacts=download, create_experiment=lambda *_: denied("deny_create_experiment"),
            create_registered_model=lambda *_: denied("deny_create_model"),
            set_experiment_tag=lambda *_: denied("deny_experiment_tag"),
            update_registered_model=lambda *_: denied("deny_model_update"), create_run=lambda *_: denied("deny_run_create"),
            set_registered_model_alias=lambda *_: denied("deny_alias_write"), log_artifact=artifact)
        namespace = {"contextmanager": contextlib.contextmanager, "MlflowException": MlflowBoundary,
                     "RestException": RestBoundary, "HTTPError": HTTPError, "json": json, "hashlib": hashlib,
                     "Path": Path, "tempfile": tempfile, "client": client, "data": data,
                     "name": "invoice-review-production", "result": {}}
        error = None
        with contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                exec(compile(ast.Module(body=[progress, step, *branch.body], type_ignores=[]), "<embedded-reader>", "exec"), namespace)
            except Exception as caught:
                error = caught
        return namespace["result"], calls, output.getvalue(), error

    def test_entire_reader_branch_retains_five_hashes_and_all_seven_denials(self):
        result, calls, output, error = self.embedded_reader()
        self.assertIsNone(error)
        self.assertEqual(result, {"download_hashes": True, "reads": True, "creates_denied": True, "writes_denied": True})
        self.assertEqual(calls, ["read_experiment", "read_alias", "read_run", "download",
                                "deny_create_experiment", "deny_create_model", "deny_experiment_tag",
                                "deny_model_update", "deny_run_create", "deny_alias_write", "deny_artifact_write"])
        self.assertEqual(smoke.sdk_progress(output), "deny_artifact_write")
        self.assertIsNone(smoke.reader_diagnostic(output))

    def test_reader_failure_diagnostics_come_from_actual_branch_and_do_not_hide_failures(self):
        for stage in smoke.READER_STAGES:
            with self.subTest(stage=stage):
                result, _, output, error = self.embedded_reader(stage)
                self.assertIsNotNone(error)
                self.assertEqual(result, {})
                diagnostic = smoke.reader_diagnostic(output)
                self.assertIsNotNone(diagnostic)
                self.assertEqual(diagnostic["stage"], stage)
                self.assertEqual(set(diagnostic), {"stage", "exception", "http_status"})
                self.assertNotIn("private-", output)

    def test_reader_nonzero_report_retains_only_valid_diagnostic_not_provider_output(self):
        runtime = Runtime()
        actual = runtime.__call__
        diagnostic = {"stage": "download", "exception": "MlflowException", "http_status": 403}
        def boundary(argv, **kwargs):
            result = actual(argv, **kwargs)
            if smoke.SDK in argv and argv[-1] == "reader":
                return subprocess.CompletedProcess(argv, 1,
                    "private-stdout\n" + smoke.SDK_DIAGNOSTIC_MARKER + json.dumps(diagnostic), "private-stderr")
            return result
        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            report = self.harness().finish()
        self.assertEqual(report["failure"], {"phase": "reader", "code": "child_failed_output_suppressed"})
        self.assertEqual(report["sdk_diagnostic"], diagnostic)
        self.assertEqual(report["operation"]["category"], "sdk_reader")
        self.assertEqual(report["operation"]["status"], "failed")
        self.assertEqual(report["proof_status"]["reader"], "UNKNOWN")
        self.assertEqual(report["shutdown"], "PASS_VOLUMES_IMAGES_PRESERVED")
        self.assertNotIn("private-", json.dumps(report))

    def test_reader_diagnostic_rejects_unbounded_unknown_or_extra_provider_fields(self):
        valid = {"stage": "download", "exception": "MlflowException", "http_status": 403}
        marker = smoke.SDK_DIAGNOSTIC_MARKER
        invalid = [[], {**valid, "stage": "private-stage"}, {**valid, "exception": "private-class"},
                   {**valid, "http_status": "403"}, {**valid, "http_status": True}, {**valid, "http_status": 200},
                   {**valid, "message": "private-provider-detail"}, {**valid, "stage": ["download"]}]
        for value in invalid:
            self.assertIsNone(smoke.reader_diagnostic(marker + json.dumps(value)))
        for output in (marker + "{", marker + "x" * 257,
                       marker + json.dumps(valid) + "\n" + marker + json.dumps(valid)):
            self.assertIsNone(smoke.reader_diagnostic(output))
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["private-argv"], 1, marker + json.dumps(valid), "private-stderr")):
            with self.assertRaises(smoke.Failure):
                harness.run(["private-argv"], operation="compose_build")
        self.assertNotIn("sdk_diagnostic", harness.report)

    def test_global_inventory_blocked_does_not_block_scoped_or_exact_workflow(self):
        runtime = Runtime()
        runtime.inventory_rows["containers"] = [{"Names": "foreign-service", "ID": "8" * 64, "Labels": ""}]
        actual = runtime.__call__

        def boundary(argv, **kwargs):
            args = argv[3:]
            listing = args[:1] == ["ps"] or len(args) > 1 and args[1] == "ls"
            if listing and "--filter" not in args:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="private-global-query")
            return actual(argv, **kwargs)

        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            harness = self.harness()
            report = harness.finish()
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(runtime.inventory_rows["containers"][0]["Names"], "foreign-service")
        self.assertEqual(harness.known["containers"][PROJECT + "-mlflow-1"], runtime.identity("mlflow"))
        for argv, _ in runtime.calls:
            args = argv[3:]
            if args[:1] == ["ps"] or len(args) > 1 and args[1] == "ls":
                self.assertEqual(args.count("--filter"), 1, "Separate queries must implement name OR label")

    def test_scoped_preflight_rejects_name_only_and_label_only_for_every_kind(self):
        for kind in ("containers", "networks", "volumes", "images"):
            for collision in ("exact", "hyphen", "underscore", "label_only"):
                with self.subTest(kind=kind, collision=collision):
                    runtime = Runtime()
                    name = {"exact": PROJECT, "hyphen": PROJECT + "-unlabeled",
                            "underscore": PROJECT + "_unlabeled", "label_only": "foreign-name"}[collision]
                    field = {"containers": "Names", "images": "Repository"}.get(kind, "Name")
                    runtime.inventory_rows[kind] = [{field: name, "ID": "8" * 64, "Tag": "latest",
                        "Labels": "com.docker.compose.project=" + PROJECT if collision == "label_only" else ""}]
                    _, report = self.run_harness(runtime)
                    self.assertEqual(report["failure"], {"phase": "preflight", "code": "preexisting_resource"})
                    self.assertFalse(any(any(word in c for word in ("build", "up", "down", "exec"))
                                         for c, _ in runtime.calls))

    def test_scoped_inventory_deduplicates_union_and_postfilters_substring_matches(self):
        runtime = Runtime()
        label = "com.docker.compose.project=" + PROJECT
        for kind, field in (("containers", "Names"), ("networks", "Name"), ("volumes", "Name"),
                            ("images", "Repository")):
            runtime.inventory_rows[kind] = [
                {field: PROJECT + "-candidate", "ID": "8" * 64, "Tag": "latest", "Labels": label},
                {field: PROJECT + "suffix-not-owned", "ID": "7" * 64, "Tag": "latest", "Labels": ""}]
        actual = runtime.__call__

        def boundary(argv, **kwargs):
            result = actual(argv, **kwargs)
            if argv[3:5] == ["image", "ls"]:
                # The real image ls JSON formatter does not expose Config.Labels.
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                for row in rows:
                    row.pop("Labels", None)
                result.stdout = "\n".join(json.dumps(row) for row in rows)
            return result

        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            inventory = self.harness().inventory(images=True)
        self.assertEqual({kind: len(rows) for kind, rows in inventory.items()},
                         {"containers": 1, "networks": 1, "volumes": 1, "images": 1})
        self.assertEqual(inventory["images"][0]["Labels"], label)

    def test_scoped_preflight_retains_published_host_port_collisions_all_protocols(self):
        for port in PORTS:
            for protocol in ("tcp", "udp", "sctp"):
                with self.subTest(port=port, protocol=protocol):
                    runtime = Runtime()
                    runtime.inventory_rows["containers"] = [{"Names": "foreign-service", "ID": "8" * 64,
                        "Ports": f"127.0.0.1:{port}->5000/{protocol}", "Labels": ""}]
                    _, report = self.run_harness(runtime)
                    self.assertEqual(report["failure"], {"phase": "preflight", "code": "port_in_use"})
                    self.assertFalse(any("up" in c or "build" in c or "down" in c for c, _ in runtime.calls))

    def test_sdk_revalidation_batches_only_exact_snapshot_targets_and_executes_id(self):
        runtime = Runtime()
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", side_effect=runtime):
            harness.execute()
            runtime.calls.clear()
            harness.sdk("objects", {"manifest": FIXTURE["manifest"]}, admin=True)
        commands = [argv[3:] for argv, _ in runtime.calls]
        self.assertEqual([c[:2] for c in commands],
                         [["network", "inspect"], ["volume", "inspect"], ["container", "inspect"], ["exec", "-i"]])
        for command, kind in zip(commands, ("networks", "volumes", "containers")):
            self.assertEqual(set(command[2:command.index("--format")]), set(harness.known[kind].values()))
            self.assertNotIn(".Config.Env", command[-1])
            self.assertNotIn("{{json .}}", command[-1])
        self.assertEqual(commands[-1][commands[-1].index("python") - 1], runtime.identity("mlflow"))
        self.assertEqual(runtime.calls[-1][1]["timeout"], 150)

    def test_exact_inspection_rejects_missing_replaced_and_tampered_resources(self):
        cases = ("missing_id", "missing_row", "id", "name", "project", "service", "files", "directory",
                 "image", "base_image", "ports", "mount", "mount_binding", "network", "network_mode",
                 "member_id", "member_name")
        for defect in cases:
            with self.subTest(defect=defect):
                runtime = Runtime()
                harness = self.harness()
                with patch.object(smoke.subprocess, "run", side_effect=runtime):
                    harness.execute()
                actual = runtime.__call__
                def boundary(argv, **kwargs):
                    result = actual(argv, **kwargs)
                    kind = "network" if defect.startswith("member_") else "container"
                    if argv[3:5] != [kind, "inspect"] or not argv[-1].startswith('{"name":'):
                        return result
                    rows = [json.loads(line) for line in result.stdout.splitlines()]
                    row = next((r for r in rows if r["name"].endswith("-mlflow-1")), rows[0])
                    if defect == "missing_id":
                        return subprocess.CompletedProcess(argv, 1, "private-inspect", "private-inspect")
                    if defect == "missing_row":
                        rows.pop()
                    elif defect == "id":
                        row["id"] = "9" * 64
                    elif defect == "name":
                        row["name"] = "/foreign-name"
                    elif defect in {"project", "service", "files", "directory"}:
                        key = {"project": "project", "service": "service", "files": "project.config_files",
                               "directory": "project.working_dir"}[defect]
                        row["labels"]["com.docker.compose." + key] = "foreign-value"
                    elif defect == "image":
                        row["image"] = "sha256:" + "9" * 64
                    elif defect == "base_image":
                        next(r for r in rows if r["name"].endswith("-postgres-1"))["image"] = "sha256:" + "9" * 64
                    elif defect == "ports":
                        row["ports"]["5000/tcp"][0]["HostIp"] = "0.0.0.0"
                    elif defect == "mount":
                        row["mounts"] = [{"Type": "bind", "Source": "/private", "Destination": "/data", "RW": True}]
                    elif defect == "mount_binding":
                        next(r for r in rows if r["name"].endswith("-postgres-1"))["mounts"][0]["Name"] = "foreign-volume"
                    elif defect == "network":
                        row["networks"][PROJECT + "_default"]["NetworkID"] = "9" * 64
                    elif defect == "network_mode":
                        row["network_mode"] = "host"
                    elif defect == "member_id":
                        row["members"]["9" * 64] = {"Name": PROJECT + "-mlflow-1"}
                    elif defect == "member_name":
                        row["members"]["9" * 64] = {"Name": "foreign-container"}
                    result.stdout = "\n".join(json.dumps(row) for row in rows)
                    return result
                count = len(runtime.calls)
                with patch.object(smoke.subprocess, "run", side_effect=boundary):
                    with self.assertRaises(smoke.Failure):
                        harness.sdk("bootstrap", {"project": PROJECT}, admin=True)
                    with self.assertRaises(smoke.Failure):
                        harness.shutdown()
                self.assertFalse(any("exec" in c or "down" in c for c, _ in runtime.calls[count:]))

    def test_cleanup_discovery_unavailable_never_falls_back_to_exact_only_down(self):
        runtime = Runtime()
        actual = runtime.__call__
        finished = [False]
        def boundary(argv, **kwargs):
            if finished[0] and "--filter" in argv:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="private-cleanup-query")
            result = actual(argv, **kwargs)
            if argv[-1] == "restore_alias":
                finished[0] = True
            return result
        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            report = self.harness().finish()
        self.assertEqual(set(report["proof_status"].values()), {"PASS"})
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
        self.assertEqual(report["operation"]["category"], "inventory_containers")
        self.assertEqual(report["operation"]["status"], "timeout")
        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_unexpected_name_or_label_candidate_before_cleanup_keeps_shutdown_unknown(self):
        for kind in ("containers", "networks", "volumes"):
            for label_only in (False, True):
                with self.subTest(kind=kind, label_only=label_only):
                    runtime = Runtime()
                    actual = runtime.__call__
                    def boundary(argv, **kwargs):
                        result = actual(argv, **kwargs)
                        if argv[-1] == "restore_alias":
                            field = "Names" if kind == "containers" else "Name"
                            runtime.inventory_rows[kind] = [{
                                field: "foreign-name" if label_only else PROJECT + "-unexpected",
                                "ID": "9" * 64,
                                "Labels": "com.docker.compose.project=" + PROJECT if label_only else ""}]
                        return result
                    with patch.object(smoke.subprocess, "run", side_effect=boundary):
                        report = self.harness().finish()
                    self.assertEqual(set(report["proof_status"].values()), {"PASS"})
                    self.assertEqual(report["status"], "FAIL")
                    self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
                    self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_timeout_operation_distinguishes_predispatch_query_from_sdk_and_sanitizes(self):
        for category, seconds in (("inspect_networks", 60), ("sdk_bootstrap", 150)):
            with self.subTest(category=category):
                runtime = Runtime()
                actual = runtime.__call__
                clock, dispatched = [0.0], []
                with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]):
                    harness = self.harness()
                    def boundary(argv, **kwargs):
                        if smoke.SDK in argv:
                            dispatched.append(argv[-1])
                        target = argv[3:5] == ["network", "inspect"] if category == "inspect_networks" else smoke.SDK in argv
                        if harness.phase == "bootstrap" and target:
                            clock[0] += seconds
                            raise subprocess.TimeoutExpired(argv + ["private-argument"], kwargs["timeout"],
                                                            output="private-stdout", stderr="private-stderr")
                        return actual(argv, **kwargs)
                    with patch.object(smoke.subprocess, "run", side_effect=boundary):
                        report = harness.finish()
                self.assertEqual(report["failure"]["phase"], "bootstrap")
                self.assertEqual(report["operation"], {"category": category, "status": "timeout", "elapsed_seconds": seconds})
                self.assertEqual(dispatched, [] if category == "inspect_networks" else ["bootstrap"])
                self.assertEqual(report["backend_cancellation"], "NOT_PROVEN")
                self.assertNotIn("private-", json.dumps(report))
                self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_operation_whitelist_and_primary_failure_survive_cleanup_without_raw_command(self):
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", side_effect=AssertionError("must not dispatch")):
            with self.assertRaisesRegex(smoke.Failure, "invalid_operation"):
                harness.run(["private-argument"], operation="private-label")
        runtime = Runtime()
        runtime.fail_action = "fixture"
        _, report = self.run_harness(runtime)
        self.assertEqual(report["shutdown"], "PASS_VOLUMES_IMAGES_PRESERVED")
        self.assertEqual(report["operation"]["category"], "sdk_fixture")
        self.assertEqual(report["operation"]["status"], "failed")
        self.assertEqual(set(report["operation"]), {"category", "status", "elapsed_seconds"})
        self.assertNotIn("secret-child", json.dumps(report))

    def embedded_http(self, data, *, opening_error=None, reading_error=None, delay=0):
        """Execute the entire HTTP child; replace only urllib's external boundary."""
        calls = []

        def open_request(request, timeout):
            calls.append(timeout)
            if opening_error:
                raise opening_error
            if delay > timeout:
                raise TimeoutError("private-transport-detail")
            return response

        def read(_):
            if reading_error:
                raise reading_error
            return b'{}'

        response = SimpleNamespace(status=503, read=read)
        class ResponseContext:
            def __enter__(self):
                return response
            def __exit__(self, *_):
                pass
        opener = SimpleNamespace(open=lambda *a, **kw: (
            open_request(*a, **kw) and ResponseContext()))
        with patch("urllib.request.build_opener", return_value=opener), \
                patch("sys.stdin", io.StringIO(json.dumps(data))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            exec(compile(smoke.HTTP, "<embedded-http>", "exec"), {})
        return smoke.receipt(output.getvalue()), calls

    def test_negative_refresh_waits_beyond_five_seconds_for_terminal_503(self):
        runtime = Runtime()
        actual = runtime.__call__
        waits = []
        def boundary(argv, **kwargs):
            data = json.loads(kwargs["input"]) if kwargs.get("input") else {}
            if smoke.HTTP in argv and "/admin/" in data["url"] and runtime.active and not runtime.alias:
                result, observed = self.embedded_http(data, delay=6)
                waits.extend(observed)
                self.assertGreater(kwargs["timeout"], observed[0])
                return subprocess.CompletedProcess(argv, 0, runtime.response(result), "")
            return actual(argv, **kwargs)
        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            report = self.harness().finish()
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 120)

    def test_embedded_transport_errors_are_sanitized_and_http_errors_are_terminal(self):
        data = {"url": "http://127.0.0.1:15800/admin/models/invoice-review/refresh",
                "method": "POST", "headers": {}, "timeout_seconds": 125}
        for error in (TimeoutError("private-timeout"), URLError("private-url"), OSError("private-os")):
            for stage in ("opening_error", "reading_error"):
                with self.subTest(error=type(error).__name__, stage=stage):
                    result, calls = self.embedded_http(data, **{stage: error})
                    self.assertEqual(result, {"status": 0, "body": {}, "transport": "indeterminate"})
                    self.assertEqual(calls, [125])
        error = UrlHTTPError(data["url"], 503, "private-http-error", {}, io.BytesIO(b"private-body"))
        result, calls = self.embedded_http(data, opening_error=error)
        self.assertEqual(result, {"status": 503, "body": {}})
        self.assertEqual(calls, [125])

    def test_terminal_unexpected_http_response_fails_without_retry(self):
        runtime = Runtime()
        actual = runtime.__call__
        attempts = []
        def boundary(argv, **kwargs):
            data = json.loads(kwargs["input"]) if kwargs.get("input") else {}
            if smoke.HTTP in argv and "/admin/" in data["url"] and runtime.active and not runtime.alias:
                attempts.append(1)
                return subprocess.CompletedProcess(argv, 0, runtime.response({"status": 500, "body": {}}), "")
            return actual(argv, **kwargs)
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", side_effect=boundary):
            report = harness.finish()
        self.assertEqual(report["failure"], {"phase": "missing_alias", "code": "http_status_mismatch"})
        self.assertEqual(attempts, [1])
        self.assertFalse(harness.uncertain)  # A terminal HTTP failure is not transport uncertainty.

    def test_release_budget_is_accepted_by_actual_release_consumer(self):
        runtime = Runtime()
        _, report = self.run_harness(runtime)
        self.assertEqual(report["status"], "PASS")
        spec = importlib.util.spec_from_file_location(
            "stack02_release_budget", smoke.API / "scripts/model_release_smoke.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(smoke.sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
            releases = [(argv, kw) for argv, kw in runtime.calls
                        if argv[-1].endswith("model_release_smoke.py")]
            self.assertEqual(len(releases), 2)
            for _, kwargs in releases:
                with patch.dict(smoke.os.environ, kwargs["env"], clear=True):
                    config = module.read_configuration()
                self.assertEqual(config.http_timeout_seconds, smoke.HTTP_REFRESH_SECONDS)
                self.assertGreater(config.http_timeout_seconds, smoke.REFRESH_SECONDS)
                self.assertGreater(kwargs["timeout"], 3 * config.http_timeout_seconds)

    def test_release_child_failure_cannot_trigger_blind_shutdown(self):
        runtime = Runtime()
        runtime.fail_action = "release"
        harness, report = self.run_harness(runtime)
        self.assertTrue(harness.uncertain)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_indeterminate_refresh_stops_without_retry_or_blind_down(self):
        for failure in ("read_timeout", "url_error", "os_error", "malformed", "missing_receipt",
                        "child_exit", "outer_timeout"):
            with self.subTest(failure=failure):
                runtime = Runtime()
                actual = runtime.__call__
                attempts = []
                def boundary(argv, **kwargs):
                    data = json.loads(kwargs["input"]) if kwargs.get("input") else {}
                    if smoke.HTTP in argv and "/admin/" in data["url"] and runtime.active and not runtime.alias:
                        attempts.append(1)
                        if failure == "outer_timeout":
                            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                        if failure == "child_exit":
                            return subprocess.CompletedProcess(argv, 9, "private-child-detail", "")
                        if failure == "missing_receipt":
                            return subprocess.CompletedProcess(argv, 0, "private-child-detail", "")
                        if failure == "malformed":
                            return subprocess.CompletedProcess(argv, 0, smoke.MARKER + "{", "")
                        error = {"read_timeout": TimeoutError("private-transport-detail"),
                                 "url_error": URLError("private-transport-detail"),
                                 "os_error": OSError("private-transport-detail")}[failure]
                        result, _ = self.embedded_http(data, reading_error=error)
                        return subprocess.CompletedProcess(argv, 0, runtime.response(result), "")
                    return actual(argv, **kwargs)
                harness = self.harness()
                with patch.object(smoke.subprocess, "run", side_effect=boundary):
                    report = harness.finish()
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["failure"]["phase"], "missing_alias")
                self.assertTrue(harness.uncertain)
                self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
                self.assertNotIn("private-", json.dumps(report))
                self.assertEqual(attempts, [1])
                self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_refresh_terminal_deadline_prevents_dispatch_and_cleanup(self):
        harness = self.harness()
        harness.started = True
        harness.env["MODEL_API_ADMIN_TOKEN"] = "unit-token"
        harness.deadline = 0
        with patch.object(smoke.subprocess, "run", side_effect=AssertionError("no dispatch")):
            with self.assertRaisesRegex(smoke.Failure, "total_deadline"):
                harness.http("/admin/models/invoice-review/refresh", "POST", token="unit-token")
            self.assertTrue(harness.uncertain)
            with self.assertRaisesRegex(smoke.Failure, "backend_state_uncertain"):
                harness.shutdown()

    def harness(self):
        return smoke.Harness(PROJECT, *PORTS, {"PATH": "/usr/bin:/bin", "DOCKER_HOST": smoke.LOCAL})

    def run_harness(self, runtime):
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", side_effect=runtime):
            report = harness.finish()
        return harness, report

    def test_refusal_without_execute_never_mutates_or_invokes_subprocess(self):
        with patch.object(smoke.subprocess, "run", side_effect=AssertionError("forbidden")), \
                patch.object(smoke.os, "open", side_effect=AssertionError("forbidden")), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            result = smoke.main(["--project", PROJECT, "--docker-host", smoke.LOCAL,
                                 "--mlflow-port", "15500", "--api-port", "15800", "--report", "/nonexistent/receipt"])
        self.assertEqual(result, 2)
        self.assertIn("REFUSED", output.getvalue())

    def test_input_validation_and_report_no_adoption(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory).resolve() / "proof.json"
            smoke.validate_inputs(PROJECT, smoke.LOCAL, *PORTS, report)
            cases = [("invoiceops-stack", smoke.LOCAL, *PORTS),
                     ("stack02-proof-../../x", smoke.LOCAL, *PORTS),
                     (PROJECT, "ssh://remote", *PORTS), (PROJECT, smoke.LOCAL, 80, PORTS[1]),
                     (PROJECT, smoke.LOCAL, True, PORTS[1]), (PROJECT, smoke.LOCAL, PORTS[0], PORTS[0])]
            for values in cases:
                with self.subTest(values=values), self.assertRaises(smoke.Failure):
                    smoke.validate_inputs(*values, report)
            report.touch()
            with self.assertRaisesRegex(smoke.Failure, "unsafe_report_path"):
                smoke.validate_inputs(PROJECT, smoke.LOCAL, *PORTS, report)

    def test_full_orchestration_receipt_and_command_scope(self):
        runtime = Runtime()
        harness, report = self.run_harness(runtime)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["phases"], ["preflight", "build", "base_start", "minio_idempotency",
                         "bootstrap", "api_start", "missing_champion", "fixture", "objects", "reader",
                         "release", "startup_champion", "missing_alias", "restored"])
        self.assertEqual(set(report["proof_status"].values()), {"PASS"})
        self.assertNotIn("artifact_uri", report["proofs"]["fixture"])
        self.assertEqual(report["proofs"]["fixture"]["manifest"], FIXTURE["manifest"])
        commands = [argv for argv, _ in runtime.calls]
        downs = [c for c in commands if "down" in c]
        self.assertEqual(len(downs), 1)
        self.assertNotIn("-v", downs[0])
        self.assertNotIn("--volumes", downs[0])
        self.assertTrue(runtime.volumes)
        api_up = next(c for c in commands if "up" in c and c[-1] == "model-api")
        self.assertNotIn("--wait", api_up)
        build = next(c for c in commands if "build" in c)
        self.assertEqual(build[-3:], ["build", "mlflow", "model-api"])
        sdk_actions = [c[-1] for c in commands if smoke.SDK in c]
        self.assertEqual(sdk_actions, ["bootstrap", "fixture", "objects", "reader", "remove_alias", "restore_alias"])
        raw_commands = json.dumps(commands)
        rendered = json.dumps(report)
        for key, value in harness.env.items():
            if key not in {"PATH", "DOCKER_HOST", "STACK_MLFLOW_PORT", "STACK_MODEL_API_PORT"}:
                self.assertNotIn(value, raw_commands)
                self.assertNotIn(value, rendered)
        for _, kwargs in runtime.calls:
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 600)

    def test_collisions_and_ambiguous_ownership_stop_mutations(self):
        for kind in ("collision", "port_collision"):
            runtime = Runtime()
            setattr(runtime, kind, True)
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["phase"], "preflight")
            self.assertFalse(any("build" in c or "up" in c or "down" in c for c, _ in runtime.calls))
        runtime = Runtime()
        runtime.bad_labels = True
        _, report = self.run_harness(runtime)
        self.assertEqual(report["failure"]["code"], "ambiguous_resource")
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_base_start_allows_observed_delay_but_rejects_failed_wait_and_timeout(self):
        for outcome in ("healthy_at_289", "not_healthy_at_330", "outer_timeout"):
            with self.subTest(outcome=outcome):
                runtime = Runtime()
                actual = runtime.__call__
                clock, waits = [0.0], []

                def boundary(argv, **kwargs):
                    if "up" in argv and "--wait" in argv:
                        waits.append((argv[argv.index("up"):], kwargs["timeout"]))
                        clock[0] += {"healthy_at_289": 289, "not_healthy_at_330": 330,
                                     "outer_timeout": 360}[outcome]
                        if outcome == "outer_timeout":
                            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                        result = actual(argv, **kwargs)
                        if outcome == "not_healthy_at_330":
                            return subprocess.CompletedProcess(argv, 1, "private-unhealthy", "")
                        return result
                    return actual(argv, **kwargs)

                with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(smoke.subprocess, "run", side_effect=boundary):
                    harness = self.harness()
                    report = harness.finish()
                self.assertEqual(waits, [([
                    "up", "-d", "--no-build", "--wait", "--wait-timeout", "330",
                    "postgres", "minio", "minio-init", "mlflow",
                ], 360)])
                if outcome == "healthy_at_289":
                    self.assertEqual(report["status"], "PASS", report)
                else:
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(report["failure"]["phase"], "base_start")
                    self.assertNotIn("bootstrap", report["phases"])
                    self.assertNotIn("base", report["snapshots"])
                    self.assertNotIn("private-unhealthy", json.dumps(report))
                    self.assertEqual(harness.uncertain, outcome == "outer_timeout")
                    self.assertEqual(report["failure"]["code"],
                                     "timeout_backend_cancellation_not_proven"
                                     if outcome == "outer_timeout" else "child_failed_output_suppressed")
                    if outcome == "outer_timeout":
                        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
                        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_base_start_remains_clamped_to_total_budget_and_cleanup_reserve(self):
        runtime = Runtime()
        actual = runtime.__call__
        clock, waits = [0.0], []

        def boundary(argv, **kwargs):
            if "build" in argv:
                clock[0] = 1600
            if "up" in argv and "--wait" in argv:
                waits.append(kwargs["timeout"])
                clock[0] += kwargs["timeout"]
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return actual(argv, **kwargs)

        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(smoke.subprocess, "run", side_effect=boundary):
            harness = self.harness()
            self.assertEqual(harness.deadline, 1800)
            report = harness.finish()
        self.assertEqual(waits, [80])  # 1800 total - 1600 elapsed - 120 cleanup.
        self.assertEqual(report["failure"]["phase"], "base_start")
        self.assertTrue(harness.uncertain)
        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_unsafe_config_no_build(self):
        changes = [lambda c: c["services"]["model-api"]["environment"].update(MLFLOW_WORKSPACE="shared"),
                   lambda c: c["volumes"]["minio_data"].update(external=True),
                   lambda c: c["services"]["model-api"]["ports"][0].update(host_ip="0.0.0.0"),
                   lambda c: c["services"]["mlflow"]["environment"].update(MLFLOW_ARTIFACTS_DESTINATION="/tmp/model")]
        for change in changes:
            runtime = Runtime()
            runtime.override["config"] = change
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL")
            self.assertFalse(any("build" in c for c, _ in runtime.calls))

    def test_negative_unloaded_state_not_confused_with_process_health(self):
        runtime = Runtime()
        runtime.active = True
        _, report = self.run_harness(runtime)
        self.assertEqual(report["failure"], {"phase": "missing_champion", "code": "http_status_mismatch"})
        self.assertNotIn("fixture", report["proofs"])
        self.assertEqual(report["proof_status"]["release"], "UNKNOWN")

    def test_observed_active_metadata_and_probability_failure_propagate(self):
        for key, value, phase in (("run_id", "d" * 32, "release"),
                                  ("alias", "other", "release"),
                                  ("probability", float("nan"), "startup_champion")):
            runtime = Runtime()
            def alter(response):
                if response["status"] == 200 and response["body"].get("model_id"):
                    if key != "probability" or "probability" in response["body"]:
                        response["body"][key] = value
            runtime.override["http"] = alter
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["phase"], phase)
            self.assertEqual(report["proof_status"]["restored"], "UNKNOWN")

    def test_readiness_poll_is_bounded_without_retrying_proofs(self):
        runtime = Runtime()
        runtime.ready_polls = 1000
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        with patch.object(smoke.time, "sleep", side_effect=advance), \
                patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]):
            _, report = self.run_harness(runtime)
        self.assertEqual(report["failure"]["code"], "process_not_ready")
        requests = [c for c, _ in runtime.calls if smoke.HTTP in c]
        self.assertEqual(len(requests), smoke.READINESS_SECONDS)

    def test_readiness_last_dispatch_is_clamped_and_late_receipt_is_rejected(self):
        for outcome in ("late_receipt", "watchdog_timeout", "on_time"):
            with self.subTest(outcome=outcome):
                runtime = Runtime()
                runtime.ready_polls = smoke.READINESS_SECONDS - 1
                actual = runtime.__call__
                clock, observed = [0.0], []

                def boundary(argv, *, runtime=runtime, actual=actual, clock=clock,
                             observed=observed, outcome=outcome, **kwargs):
                    data = json.loads(kwargs["input"]) if kwargs.get("input") else {}
                    if smoke.HTTP in argv and data["url"].endswith("/health") \
                            and "/models/" not in data["url"] \
                            and not runtime.restarts \
                            and clock[0] >= smoke.READINESS_SECONDS - 1:
                        observed.append((data["timeout_seconds"], kwargs["timeout"]))
                        if outcome == "watchdog_timeout":
                            clock[0] += kwargs["timeout"]
                            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                        clock[0] += (1.01 if outcome == "late_receipt" else 0.1)
                        return subprocess.CompletedProcess(argv, 0, runtime.response(
                            {"status": 200, "body": {"status": "ok"}}), "")
                    return actual(argv, **kwargs)

                harness = self.harness()
                with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(smoke.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
                        patch.object(smoke.subprocess, "run", side_effect=boundary):
                    report = harness.finish()
                self.assertEqual(len(observed), 1)
                self.assertGreater(observed[0][0], 0)
                self.assertLessEqual(observed[0][0], 1)
                self.assertGreater(observed[0][1], 0)
                self.assertLessEqual(observed[0][1], 1)
                self.assertFalse(harness.uncertain)
                if outcome == "on_time":
                    self.assertEqual(report["status"], "PASS", report)
                else:
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(report["failure"]["phase"], "missing_champion")
                    self.assertNotIn("missing_champion", harness.proven)
                    self.assertTrue(any("down" in c for c, _ in runtime.calls))

    def test_metadata_manifest_route_and_probability_validation(self):
        changes = [("version", "x"), ("run_id", "wrong"), ("sdk", "3.16.1"),
                   ("workspace", "shared"), ("artifact_uri", "file:///tmp/local"),
                   ("manifest", {"MLmodel": "secret", "model.skops": "c" * 64})]
        for key, value in changes:
            fixture = copy.deepcopy(FIXTURE)
            fixture[key] = value
            with self.subTest(key=key), self.assertRaises(smoke.Failure):
                smoke.validate_fixture(fixture)
        for probability in (True, -1, 2, float("nan"), float("inf"), "secret"):
            value = {"model_id": "invoice-review", "model_version": "1", "run_id": "a" * 32,
                     "probability": probability}
            with self.subTest(probability=probability), self.assertRaises(smoke.Failure):
                smoke.Harness.validate_active(value, FIXTURE, probability=True)
        with self.assertRaisesRegex(smoke.Failure, "active_metadata"):
            smoke.Harness.validate_active({"model_id": "other"}, FIXTURE)

    def test_hash_mismatch_role_failure_and_release_error_never_pass_or_retry(self):
        for action in ("objects", "reader", "release", "restore_alias"):
            runtime = Runtime()
            runtime.fail_action = action
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL")
            self.assertNotIn("secret-child", json.dumps(report))
            calls = [c for c, _ in runtime.calls if c[-1] == action or
                     (action == "release" and c[-1].endswith("model_release_smoke.py"))]
            self.assertEqual(len(calls), 1)
        runtime = Runtime()
        runtime.override["objects"] = lambda v: v.update(manifest={"MLmodel": "d" * 64})
        _, report = self.run_harness(runtime)
        self.assertEqual(report["failure"]["code"], "object_hash_mismatch")
        self.assertEqual(report["proof_status"]["reader"], "UNKNOWN")

    def test_timeouts_do_not_claim_cancellation_or_blindly_down(self):
        runtime = Runtime()
        runtime.timeout_action = "fixture"
        _, report = self.run_harness(runtime)
        self.assertEqual(report["failure"], {"phase": "fixture", "code": "timeout_backend_cancellation_not_proven"})
        self.assertEqual(report["backend_cancellation"], "NOT_PROVEN")
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
        self.assertFalse(any("down" in c for c, _ in runtime.calls))
        harness = self.harness()
        harness.deadline = 0
        with patch.object(smoke.subprocess, "run", side_effect=AssertionError("forbidden")), \
                self.assertRaisesRegex(smoke.Failure, "total_deadline"):
            harness.run(["unused"])

    def test_cleanup_failure_downgrades_complete_proof(self):
        runtime = Runtime()
        runtime.cleanup_failure = True
        _, report = self.run_harness(runtime)
        self.assertEqual(set(report["proof_status"].values()), {"PASS"})
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")

    def test_new_resource_prevents_destructive_cleanup(self):
        runtime = Runtime()
        harness = self.harness()
        with patch.object(smoke.subprocess, "run", side_effect=runtime):
            harness.execute()
            runtime.extra = True
            with self.assertRaisesRegex(smoke.Failure, "ambiguous_resource"):
                harness.shutdown()
        self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_missing_proof_and_receipt_noise_never_pass(self):
        runtime = Runtime()
        harness = self.harness()
        # An external process returns a false role proof, not a mocked verifier.
        runtime.override["reader"] = lambda v: v.update(writes_denied=False)
        with patch.object(smoke.subprocess, "run", side_effect=runtime):
            report = harness.finish()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["proof_status"]["reader"], "UNKNOWN")
        for output in ("noise", smoke.MARKER + "[]", smoke.MARKER + "{}\n" + smoke.MARKER + "{}"):
            with self.assertRaises(smoke.Failure):
                smoke.receipt(output)
        self.assertEqual(smoke.receipt("noise\n" + smoke.MARKER + '{"ok":true}'), {"ok": True})

    def test_anonymous_cli_environment_excludes_ambient_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(smoke.os.environ, {"HOME": "/private", "DOCKER_CONTEXT": "remote",
                                              "AWS_SECRET_ACCESS_KEY": "secret", "HTTP_PROXY": "secret"}):
                env = smoke.isolated_cli_environment(directory, "/opt/trusted/docker-compose")
            self.assertEqual(set(env), {"PATH", "DOCKER_HOST", "PYTHONDONTWRITEBYTECODE", "DOCKER_CONFIG", "BUILDX_CONFIG", "HOME", "COMPOSE_DISABLE_ENV_FILE"})
            self.assertEqual(env["HOME"], directory)
            config = json.loads((Path(directory) / "config.json").read_text())
            self.assertEqual(config["auths"], {"https://index.docker.io/v1/": {}})
            self.assertEqual(config["credsStore"], "")
            self.assertEqual(config["credHelpers"], {})

    def test_portable_fake_compose_config_uses_private_home_and_selected_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "docker-compose"
            binary.write_text(
                "#!" + smoke.sys.executable + "\n"
                "import json, os, pathlib, sys\n"
                "assert sys.argv[1:] == ['config', '--format', 'json']\n"
                "home = pathlib.Path(os.environ['HOME'])\n"
                "assert os.environ['DOCKER_CONFIG'] == str(home)\n"
                "assert 'DOCKER_CONTEXT' not in os.environ\n"
                "config = json.loads((home / 'config.json').read_text())\n"
                "assert config['cliPluginsExtraDirs'] == [str(pathlib.Path(sys.argv[0]).parent)]\n"
                "print(json.dumps({'services': {}}))\n")
            binary.chmod(0o700)
            with tempfile.TemporaryDirectory(dir=root) as home:
                environment = smoke.isolated_cli_environment(home, str(binary))
                result = subprocess.run([str(binary), "config", "--format", "json"],
                                        env=environment, capture_output=True, text=True,
                                        check=False, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"services": {}})

    def test_main_real_parser_private_report_and_exclusive_creation(self):
        runtime = Runtime()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory).resolve() / "receipt.json"
            binaries = []
            for name in (smoke.DOCKER, smoke.COMPOSE):
                binary = Path(directory).resolve() / name
                binary.write_text("#!/bin/sh\nexit 97\n")
                binary.chmod(0o700)
                binaries.append(str(binary))
            args = ["--execute", "--project", PROJECT, "--docker-host", smoke.LOCAL,
                    "--mlflow-port", "15500", "--api-port", "15800", "--report", str(report),
                    "--docker-binary", binaries[0], "--compose-binary", binaries[1],
                    "--temp-root", str(Path(directory).resolve())]
            with patch.object(smoke.subprocess, "run", side_effect=runtime), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                result = smoke.main(args)
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
            self.assertEqual(json.loads(report.read_text())["status"], "PASS")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertTrue(all(Path(kw["env"]["HOME"]).parent == Path(directory).resolve()
                                for argv, kw in runtime.calls if Path(argv[0]).name in {smoke.DOCKER, smoke.COMPOSE}))
            self.assertTrue(any(argv[0] == binaries[0] for argv, _ in runtime.calls))
            self.assertTrue(any(argv[0] == binaries[1] for argv, _ in runtime.calls))
            count = len(runtime.calls)
            original = report.read_bytes()
            with patch.object(smoke.subprocess, "run", side_effect=runtime), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(smoke.main(args), 1)
            self.assertEqual(len(runtime.calls), count)
            self.assertEqual(report.read_bytes(), original)

    def test_trusted_executable_selection_is_portable_and_missing_binary_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory).resolve() / "docker-compose"
            binary.write_text("#!/bin/sh\nexit 97\n")
            with self.assertRaisesRegex(smoke.Failure, "blocked_environment"):
                smoke.trusted_executable(str(binary), smoke.COMPOSE)
            binary.chmod(0o700)
            with patch.object(smoke.subprocess, "run", side_effect=AssertionError("no execution")):
                self.assertEqual(smoke.trusted_executable(str(binary), smoke.COMPOSE), str(binary))
                with patch.dict(smoke.os.environ, {"PATH": directory}):
                    self.assertEqual(smoke.trusted_executable(None, smoke.COMPOSE), str(binary))
                with self.assertRaisesRegex(smoke.Failure, "invalid_executable_path"):
                    smoke.trusted_executable("relative/docker", smoke.DOCKER)
                with self.assertRaisesRegex(smoke.Failure, "blocked_environment"):
                    smoke.trusted_executable(str(binary.parent / "missing"), smoke.DOCKER)

    def test_embedded_programs_parse_without_importing_sdk(self):
        ast.parse(smoke.SDK)
        ast.parse(smoke.HTTP)

    def test_fixture_pins_dependencies_without_automatic_inference(self):
        tree = ast.parse(smoke.SDK)
        save = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "save_model")
        requirements = next((item.value for item in save.keywords
                             if item.arg == "pip_requirements"), None)
        self.assertIsNotNone(requirements, "save_model must not launch dependency inference")
        packages = {"numpy", "scipy", "pandas", "scikit-learn", "skops"}
        visited = []

        def package_version(package):
            visited.append(package)
            return "1.2.3"

        actual = eval(compile(ast.Expression(requirements), "<fixture-requirements>", "eval"),
                      {"package_version": package_version})
        self.assertEqual(set(visited), packages)
        self.assertEqual(set(actual), {package + "==1.2.3" for package in packages})
        self.assertEqual(len(actual), len(packages))

    def test_both_new_proofs_reuse_owned_ids_and_run_in_required_order(self):
        runtime = Runtime()
        _, report = self.run_harness(runtime)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(len(smoke.REQUIRED), 11)
        self.assertEqual(runtime.init_repeats, 1)
        self.assertEqual(runtime.restarts, 1)
        self.assertEqual(report["proofs"]["minio_idempotency"]["container_id"], runtime.identity("minio-init"))
        self.assertEqual(report["proofs"]["minio_idempotency"]["exit_code"], 0)
        self.assertEqual(report["proofs"]["startup_champion"]["run_id"], FIXTURE["run_id"])
        self.assertEqual(report["proofs"]["startup_champion"]["model_version"], FIXTURE["version"])
        self.assertEqual(report["snapshots"]["base"]["containers"], {
            k: v for k, v in report["snapshots"]["api"]["containers"].items()
            if not k.endswith("-model-api-1")})
        commands = [argv for argv, _ in runtime.calls]
        repeat = next(i for i, c in enumerate(commands) if "start" in c)
        bootstrap = next(i for i, c in enumerate(commands) if c[-1] == "bootstrap")
        releases = [i for i, c in enumerate(commands) if c[-1].endswith("model_release_smoke.py")]
        restart = next(i for i, c in enumerate(commands) if "restart" in c)
        remove = next(i for i, c in enumerate(commands) if c[-1] == "remove_alias")
        self.assertLess(repeat, bootstrap)
        self.assertLess(releases[0], restart)
        self.assertLess(restart, remove)
        self.assertLess(remove, releases[1])
        self.assertFalse(any("run" in c for c in commands))
        for argv, kwargs in runtime.calls:
            if "start" in argv or "restart" in argv:
                self.assertLessEqual(kwargs["timeout"], 30)
                self.assertEqual(kwargs["env"]["MODEL_API_ADMIN_TOKEN"], runtime.secret)

    def test_initializer_requires_exited_zero_even_when_attach_command_succeeds(self):
        for status, code in (("exited", 1), ("running", 0)):
            runtime = Runtime()
            runtime.initializer_status, runtime.initializer_exit_code = status, code
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["phase"], "minio_idempotency")
            self.assertEqual(runtime.init_repeats, 1)
            self.assertEqual(report["proof_status"]["minio_idempotency"], "UNKNOWN")
            self.assertFalse(any(c[-1] == "bootstrap" for c, _ in runtime.calls))

    def test_new_proof_mutations_timeout_without_blind_cleanup(self):
        for action in ("minio_repeat", "restart"):
            runtime = Runtime()
            runtime.timeout_action = action
            harness, report = self.run_harness(runtime)
            self.assertTrue(harness.uncertain)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
            self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_new_proofs_reject_replaced_own_or_other_resource_ids(self):
        for action, service in (("start", "minio-init"), ("restart", "model-api"), ("restart", "postgres")):
            runtime = Runtime()
            actual = runtime.__call__

            def boundary(argv, *, action=action, service=service, runtime=runtime, actual=actual, **kwargs):
                result = actual(argv, **kwargs)
                if action in argv:
                    runtime.identity_changes[service] = "9" * 64
                return result

            with patch.object(smoke.subprocess, "run", side_effect=boundary):
                report = self.harness().finish()
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["code"], "ownership_changed")
            self.assertEqual(report["shutdown"], "UNKNOWN_REQUIRES_REVIEW")
            self.assertFalse(any("down" in c for c, _ in runtime.calls))

    def test_positive_startup_missing_champion_or_wrong_metadata_cannot_pass(self):
        for defect in ("missing", "run_id", "model_version", "alias", "registered_model", "probability"):
            runtime = Runtime()
            runtime.startup_missing = defect == "missing"

            def alter(value, *, defect=defect, runtime=runtime):
                if runtime.restarts and value["status"] == 200 and value["body"].get("model_id"):
                    if defect == "probability":
                        if "probability" in value["body"]:
                            value["body"]["probability"] = float("nan")
                    elif defect != "missing":
                        value["body"][defect] = "wrong"

            runtime.override["http"] = alter
            _, report = self.run_harness(runtime)
            self.assertEqual(report["status"], "FAIL", defect)
            self.assertEqual(report["failure"]["phase"], "startup_champion")
            self.assertEqual(report["proof_status"]["startup_champion"], "UNKNOWN")
            self.assertFalse(any(c[-1] == "remove_alias" for c, _ in runtime.calls))

    def test_positive_startup_readiness_rejects_late_receipt(self):
        runtime = Runtime()
        actual = runtime.__call__
        clock, late_calls = [0.0], []

        def boundary(argv, **kwargs):
            data = json.loads(kwargs["input"]) if kwargs.get("input") else {}
            if smoke.HTTP in argv and runtime.restarts and data["url"].endswith("/health") \
                    and "/models/" not in data["url"]:
                late_calls.append(1)
                clock[0] += smoke.READINESS_SECONDS
                return subprocess.CompletedProcess(argv, 0, runtime.response(
                    {"status": 200, "body": {"status": "ok"}}), "")
            return actual(argv, **kwargs)

        with patch.object(smoke.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(smoke.subprocess, "run", side_effect=boundary):
            harness = self.harness()
            report = harness.finish()
        self.assertEqual(late_calls, [1])
        self.assertEqual(report["failure"], {"phase": "startup_champion", "code": "http_deadline"})
        self.assertFalse(harness.uncertain)
        self.assertEqual(report["proof_status"]["startup_champion"], "UNKNOWN")

    def embedded_reader_checks(self):
        tree = ast.parse(smoke.SDK)
        helpers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name in {"denied", "artifact_denied"}]
        self.assertEqual({node.name for node in helpers}, {"denied", "artifact_denied"})

        # Only the unavailable MLflow REST exception boundary is represented by
        # a stub. Artifact tests use installed requests exceptions and Response.
        class RestExceptionBoundary(Exception):
            def __init__(self, status):
                self.status = status

            def get_http_status_code(self):
                return self.status

        namespace = {"HTTPError": HTTPError, "RestException": RestExceptionBoundary,
                     "MlflowException": RestExceptionBoundary}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "<embedded-reader-helpers>", "exec"), namespace)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "artifact_denied"]
        self.assertEqual(len(calls), 1)
        upload = compile(ast.Expression(body=calls[0]), "<embedded-reader-upload>", "eval")
        return namespace, upload

    def test_embedded_artifact_denial_accepts_only_observed_requests_403(self):
        namespace, upload = self.embedded_reader_checks()

        class PrivateHTTPError(HTTPError):
            def __str__(self):
                raise AssertionError("Exception rendering is forbidden")

            def __repr__(self):
                raise AssertionError("Exception rendering is forbidden")

        class ClientBoundary:
            def __init__(self, error):
                self.error, self.calls = error, []

            def log_artifact(self, *args):
                self.calls.append(args)
                if self.error is not None:
                    raise self.error

        def http_error(status):
            response = Response()
            response.status_code = status
            return PrivateHTTPError("private-provider-detail", response=response)

        cases = [("403", http_error(403), True), ("401", http_error(401), False),
                 ("500", http_error(500), False), ("missing_status", http_error(None), False),
                 ("missing_response", PrivateHTTPError("private-provider-detail"), False),
                 ("transport", RequestsConnectionError("private-provider-detail"), False),
                 ("successful_write", None, False)]
        for label, error, accepted in cases:
            with self.subTest(case=label):
                client = ClientBoundary(error)
                namespace.update(client=client, data={"run_id": "a" * 32}, source=Path("unused-denied.txt"))
                if accepted:
                    self.assertFalse(error.response)  # A real requests 403 Response is falsey.
                    self.assertIsNone(eval(upload, namespace))
                else:
                    with self.assertRaises((AssertionError, RequestsConnectionError)):
                        eval(upload, namespace)
                self.assertEqual(client.calls, [("a" * 32, "unused-denied.txt", "stack02-denied")])

    def test_embedded_rest_denial_remains_exact_and_does_not_accept_http_error(self):
        namespace, _ = self.embedded_reader_checks()
        denied = namespace["denied"]

        def fail(error):
            raise error

        for status in (403, 401, 500, None):
            with self.subTest(status=status):
                call = lambda: fail(namespace["RestException"](status))
                if status == 403:
                    self.assertIsNone(denied(call))
                else:
                    with self.assertRaises(AssertionError):
                        denied(call)
        response = Response()
        response.status_code = 403
        with self.assertRaises(HTTPError):
            denied(lambda: fail(HTTPError(response=response)))
        with self.assertRaises(AssertionError):
            denied(lambda: None)


if __name__ == "__main__":
    unittest.main()
