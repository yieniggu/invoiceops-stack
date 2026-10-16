import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stack_ml_smoke.py"
spec = importlib.util.spec_from_file_location("stack_ml_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
PROJECT = "stack01-proof-unit123"


class SmokeTests(unittest.TestCase):
    def test_rejects_adoption_and_option_injection(self):
        for project in ("invoiceops-stack", "stack01-proof-x", "--context=remote", "stack01-proof-ABC123", "stack01-proof-../../x"):
            with self.subTest(project=project), self.assertRaises(smoke.SmokeError):
                smoke.validate_inputs(project, 15432)

    def test_rejects_invalid_ports(self):
        for port in (0, 80, 65536, True, "15432"):
            with self.subTest(port=port), self.assertRaises(smoke.SmokeError):
                smoke.validate_inputs(PROJECT, port)

    def test_rejects_occupied_loopback_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(smoke.SmokeError, "port_unavailable"):
                smoke.validate_inputs(PROJECT, listener.getsockname()[1])

    def test_child_environment_does_not_inherit_credentials_or_sessions(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://remote", "HOME": "/private/home",
                                    "AWS_SECRET_ACCESS_KEY": "private", "HTTP_PROXY": "private"}):
            environment = smoke.child_environment()
        self.assertEqual(set(environment), {"PATH", "DOCKER_HOST", "PYTHONDONTWRITEBYTECODE"})
        self.assertEqual(environment["DOCKER_HOST"], smoke.LOCAL)

    def test_isolated_cli_config_is_anonymous_and_resolves_real_compose(self):
        with tempfile.TemporaryDirectory(prefix="stack01-cli-test-", dir=smoke.TEMP_ROOT) as directory:
            environment = smoke.isolated_cli_environment(directory)
            config = json.loads((Path(directory) / "config.json").read_text())
            self.assertEqual(config["auths"], {"https://index.docker.io/v1/": {}})
            self.assertEqual(config["credsStore"], "")
            self.assertEqual(config["credHelpers"], {})
            self.assertNotIn("currentContext", config)
            self.assertEqual(environment["DOCKER_HOST"], smoke.LOCAL)
            process = subprocess.run(["docker", "--host", smoke.LOCAL, "compose", "version", "--short"],
                                     env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(process.returncode, 0, "Isolated Compose plugin resolution failed; output suppressed")
            self.assertRegex(process.stdout.strip(), r"^v?\d+\.\d+\.\d+")

    def test_receipt_tolerates_noise_without_accepting_duplicates(self):
        self.assertEqual(smoke.receipt('noise\nSTACK01_RECEIPT={"ok":true}\nmore noise'), {"ok": True})
        outputs = ("noise", "STACK01_RECEIPT=[]", "STACK01_RECEIPT=bad",
                   'STACK01_RECEIPT={}\nSTACK01_RECEIPT={}')
        for output in outputs:
            with self.subTest(output=output), self.assertRaises(smoke.SmokeError):
                smoke.receipt(output)

    def valid_receipt(self):
        return {"run_id": "a" * 32, "workspace": PROJECT,
                "sha256": smoke.hashlib.sha256(smoke.PAYLOAD).hexdigest(),
                "artifact_uri": "mlflow-artifacts:/workspaces/test/1/run/artifacts",
                "sdk_version": "3.16.0", "bytes": len(smoke.PAYLOAD), "status": "FINISHED"}

    def test_consumer_proof_requires_workspace_proxy_and_matching_bytes(self):
        result = self.valid_receipt()
        self.assertEqual(smoke.validate_consumer(result, PROJECT), result)
        for key, bad in (("workspace", "default"), ("sha256", "0" * 64),
                         ("artifact_uri", "s3://bucket/path"), ("bytes", 99),
                         ("status", "RUNNING"), ("run_id", "not-run")):
            with self.subTest(key=key), self.assertRaises(smoke.SmokeError):
                smoke.validate_consumer(dict(result, **{key: bad}), PROJECT)

    def test_persistence_receipt_cannot_change_run(self):
        previous = self.valid_receipt()
        with self.assertRaisesRegex(smoke.SmokeError, "persistence_mismatch"):
            smoke.validate_consumer(dict(previous, run_id="b" * 32), PROJECT, previous)

    def test_consumer_receipt_uses_real_sdk_workspace_contract_without_network(self):
        program = r'''
import ast, contextlib, inspect, io, json, socket, sys
from types import SimpleNamespace
from unittest.mock import patch
import mlflow
from mlflow.entities import Workspace
from invoiceops_ml.mlflow import configure_mlflow, mlflow_config_from_env
tree = ast.parse(sys.stdin.read())
namespace = {}
definitions = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
exec(compile(ast.Module(body=definitions, type_ignores=[]), "consumer-definitions", "exec"), namespace)
prints = [node for node in ast.walk(tree) if isinstance(node, ast.Expr)
          and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
          and node.value.func.id == "print"]
assert len(prints) == 1
receipt_code = compile(ast.Module(body=prints, type_ignores=[]), "consumer-receipt", "exec")
config = mlflow_config_from_env()
configure_mlflow(config)
workspace = config.workspace
inspect.signature(mlflow.get_workspace).bind(workspace)
mlflow.set_workspace(workspace)
assert mlflow_config_from_env().workspace == workspace
namespace.update(workspace=workspace, run_id="a" * 32, actual=b"STACK01 synthetic artifact\n",
                 run=SimpleNamespace(info=SimpleNamespace(artifact_uri="mlflow-artifacts:/proof/artifacts", status="FINISHED")))
with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
    with patch.object(mlflow.MlflowClient, "get_workspace", autospec=True,
                      return_value=Workspace(name=workspace)) as metadata:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(receipt_code, namespace)
        result = json.loads(output.getvalue().split("STACK01_RECEIPT=", 1)[1])
        assert result["workspace"] == workspace and isinstance(result["workspace"], str)
        assert metadata.call_count == 1 and metadata.call_args.args[1] == workspace
        # Metadata for the requested name must not masquerade as active selection.
        mlflow.set_workspace("stack01-proof-other123")
        try:
            exec(receipt_code, namespace)
        except AssertionError:
            pass
        else:
            raise AssertionError("active workspace mismatch accepted")
        mlflow.set_workspace(workspace)
    with patch.object(mlflow.MlflowClient, "get_workspace", autospec=True,
                      return_value=Workspace(name="stack01-proof-other123")):
        try:
            exec(receipt_code, namespace)
        except AssertionError:
            pass
        else:
            raise AssertionError("metadata workspace mismatch accepted")
print("SDK_WORKSPACE_RECEIPT_PASS " + mlflow.__version__)
'''
        environment = smoke.child_environment()
        environment.update(PYTHONPATH=str(smoke.ML / "src"), MLFLOW_DISABLE_AGENT_HINT="1",
                           MLFLOW_TRACKING_URI="http://127.0.0.1:1", MLFLOW_WORKSPACE=PROJECT,
                           MLFLOW_TRACKING_USERNAME="synthetic-user", MLFLOW_TRACKING_PASSWORD="synthetic-password")
        process = subprocess.run([str(smoke.ML / ".venv/bin/python"), "-B", "-c", program],
                                 env=environment, input=smoke.CONSUMER, capture_output=True,
                                 text=True, timeout=30)
        self.assertEqual(process.returncode, 0, "Real SDK consumer receipt contract failed; output suppressed")
        self.assertIn("SDK_WORKSPACE_RECEIPT_PASS 3.16.0", process.stdout)

    def test_timeout_is_bounded_and_does_not_claim_backend_cancelled(self):
        harness = smoke.Harness(PROJECT, 15432)
        with patch.object(smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 5, output="secret")) as process:
            with self.assertRaisesRegex(smoke.SmokeError, "cancellation_not_proven"):
                harness.docker("test", "version", timeout=5)
        self.assertEqual(process.call_args.kwargs["timeout"], 5)
        self.assertEqual(harness.report["commands"][0]["backend_cancelled"], "NOT_PROVEN")
        self.assertNotIn("secret", json.dumps(harness.report))

    def test_failed_command_never_exposes_output(self):
        harness = smoke.Harness(PROJECT, 15432)
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 9, "secret", "secret")):
            with self.assertRaisesRegex(smoke.SmokeError, "exit_9_output_suppressed"):
                harness.docker("test", "version")
        self.assertNotIn("secret", json.dumps(harness.report))

    def test_preexisting_volume_blocks_before_any_compose_mutation(self):
        harness = smoke.Harness(PROJECT, 15432)
        outputs = ["27.4.0", "2.31.0", "", "", '{"Name":"existing"}']
        with patch.object(smoke.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, out, "") for out in outputs]) as process:
            with self.assertRaisesRegex(smoke.SmokeError, "preexisting_project_resources"):
                harness.preflight()
        self.assertFalse(harness.started)
        self.assertEqual(process.call_count, 5)

    def test_preexisting_unlabelled_name_blocks_adoption(self):
        harness = smoke.Harness(PROJECT, 15432)
        outputs = ["27", "2.31", "", "", "", PROJECT + "-mlflow-1"]
        with patch.object(smoke.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, out, "") for out in outputs]):
            with self.assertRaisesRegex(smoke.SmokeError, "preexisting_resource_name"):
                harness.preflight()
        self.assertFalse(harness.started)

    def test_ownership_mismatch_prevents_shutdown(self):
        harness = smoke.Harness(PROJECT, 15432)
        harness.started = True
        outputs = ['{"ID":"123"}', "", "", '"123"|"foreign"|{"com.docker.compose.project":"foreign"}|"running"|{}']
        with patch.object(smoke.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, out, "") for out in outputs]) as process:
            with self.assertRaisesRegex(smoke.SmokeError, "resource_ownership_mismatch"):
                harness.shutdown()
        for call in process.call_args_list:
            self.assertNotIn("down", call.args[0])

    def test_shutdown_without_mutation_does_not_touch_docker(self):
        harness = smoke.Harness(PROJECT, 15432)
        with patch.object(smoke.subprocess, "run") as process:
            harness.shutdown()
        process.assert_not_called()

    def test_shutdown_preserves_named_volumes_without_delete_flag(self):
        harness = smoke.Harness(PROJECT, 15432)
        harness.started = True
        name = PROJECT + "_minio_data"
        listing = json.dumps({"Name": name})
        inspection = "|".join(json.dumps(value) for value in
                              (name, name, {"com.docker.compose.project": PROJECT}))
        outputs = ["", "", listing, inspection, "", "", "", listing, inspection]
        with patch.object(smoke.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, out, "") for out in outputs]) as process:
            harness.shutdown()
        commands = [call.args[0] for call in process.call_args_list]
        down = next(command for command in commands if "down" in command)
        self.assertNotIn("-v", down)
        self.assertNotIn("--volumes", down)
        self.assertEqual(down[1:3], ["--host", smoke.LOCAL])
        self.assertEqual(harness.report["shutdown"], "PASS_VOLUMES_PRESERVED")
        self.assertEqual(harness.report["snapshots"]["stopped"]["volumes"], [{"id": name, "name": name}])


class ConsumerProgressEvidenceTests(unittest.TestCase):
    """S1 contract for S2, not a test-side collector (AC01–AC07).

    Private seam: consumer owns a 0600 temporary journal under TEMP_ROOT and
    passes its writable descriptor via STACK_SMOKE_PROGRESS_FD + pass_fds.
    The child writes UTF-8 JSONL, exactly {"phase": str, "state": str}.
    Detailed phases distinguish individual SDK calls and local validation;
    persistence omits creation/logging. No duplicate, skipped or reversed events.
    Limits: 64 events, 512 bytes per record including LF, 32768 bytes total.
    No stdout fallback. Commands expose progress={events: [...], reason: enum};
    reasons: COMPLETE, EMPTY, INVALID_RECORD, INVALID_SEQUENCE, TRUNCATED,
    RECORD_LIMIT, TOTAL_LIMIT, EVENT_LIMIT. COMPLETE means a valid journal,
    not functional success. Preserve the valid prefix on record/sequence errors.
    A complete valid prefix may end at any event after timeout/nonzero exit.

    Only the process boundary is adapted: installed stdlib Python replaces the
    ML interpreter and a real timeout is shortened AFTER checking the 90s budget.
    The production Harness.consumer/run and future collector stay unmocked.
    Tests exercise the production collector through real local children.
    """

    PREFIX = [{"phase": "sdk_import", "state": "before"}]
    CANARY = "S1_PRIVATE_CANARY_8e140a"
    PHASES = ("sdk_import", "helper_import", "configuration", "configure",
              "workspace_create", "workspace_select", "client", "experiment",
              "run_start", "artifact_source", "artifact_log", "run_end",
              "run_query", "artifact_download", "local_validation", "receipt")

    def events(self, previous=False):
        omitted = {"workspace_create", "experiment", "run_start", "artifact_source", "artifact_log", "run_end"}
        events = [{"phase": phase, "state": state} for phase in self.PHASES
                  if not previous or phase not in omitted for state in ("before", "after")]
        events[-1:-1] = [{"phase": "workspace_metadata", "state": state} for state in ("before", "after")]
        return events

    def setUp(self):
        self.harness = smoke.Harness(PROJECT, 15432)
        self.harness.env.update(MLFLOW_AUTH_ADMIN_USERNAME="synthetic-user",
                                MLFLOW_AUTH_ADMIN_PASSWORD=self.CANARY)
        self.directory = tempfile.TemporaryDirectory(prefix="stack01-progress-test-", dir=smoke.TEMP_ROOT)
        self.addCleanup(self.directory.cleanup)
        self.launch_file = Path(self.directory.name) / "launches"
        self.pid_file = Path(self.directory.name) / "child-pid"
        self.launches = []
        self.descriptors = []

    def child(self, payload, ending="", consumer_source=None):
        # The launch marker proves execution even when the collector is absent.
        program = (
            "import os, sys, time\n"
            f"with open({str(self.launch_file)!r}, 'a') as marker: marker.write('child\\n')\n"
            f"with open({str(self.pid_file)!r}, 'w') as marker: marker.write(str(os.getpid()))\n"
            "fd = os.environ.get('STACK_SMOKE_PROGRESS_FD')\n"
            "assert fd is not None and os.fstat(int(fd)).st_mode & 0o777 == 0o600\n"
            f"os.write(int(fd), {payload!r})\n"
            f"print({self.CANARY!r}, flush=True)\n"
            f"print('https://private.invalid/' + {self.CANARY!r}, file=sys.stderr, flush=True)\n"
            # Even a protocol-looking stdout line must not become evidence.
            "print('STACK01_PROGRESS={\"phase\":\"receipt\",\"state\":\"after\"}', flush=True)\n"
        )
        if consumer_source is not None:
            program += (
                "import builtins\n"
                "original_import = builtins.__import__\n"
                "def stop_sdk(name, *args, **kwargs):\n"
                "    if name == 'mlflow': raise SystemExit(13)\n"
                "    return original_import(name, *args, **kwargs)\n"
                "builtins.__import__ = stop_sdk\n"
                f"exec(compile({consumer_source!r}, '<consumer>', 'exec'))\n"
            )
        return program + ending

    def invoke(self, program, previous=None, timeout=False):
        real_run = subprocess.run
        launches_before = len(self.launches)

        def local_process(args, **kwargs):
            # No Docker, MLflow imports, network or ambient interpreter is used.
            self.assertEqual(args[0], str(smoke.ML / ".venv/bin/python"))
            self.assertEqual(args[1:3], ["-B", "-c"])
            self.assertEqual(kwargs["timeout"], 90)
            self.assertEqual(kwargs["env"]["MLFLOW_HTTP_REQUEST_TIMEOUT"], "10")
            self.assertEqual(kwargs["env"]["MLFLOW_HTTP_REQUEST_MAX_RETRIES"], "0")
            self.assertEqual(kwargs["env"].get("STACK_SMOKE_RUN_ID"),
                             previous["run_id"] if previous else None)
            fd = kwargs["env"].get("STACK_SMOKE_PROGRESS_FD")
            self.assertIsNotNone(fd, "Instrumented consumer must receive its private descriptor")
            fd = int(fd)
            self.assertEqual(kwargs.get("pass_fds"), (fd,))
            self.assertEqual(os.fstat(fd).st_mode & 0o777, 0o600)
            self.descriptors.append(fd)
            kwargs["timeout"] = 1 if timeout else 5
            self.launches.append(1)
            return real_run([sys.executable, "-B", "-c", program], **kwargs)

        error = None
        started = time.monotonic()
        with patch.object(smoke, "CONSUMER", program), patch.object(smoke.subprocess, "run", side_effect=local_process):
            try:
                result = self.harness.consumer(previous)
            except smoke.SmokeError as caught:
                error = str(caught)
                result = None
        self.assertLess(time.monotonic() - started, 8, "Synthetic child exceeded its finite budget")
        count = len(self.launches)
        self.assertEqual(count - launches_before, 1, "No automatic retry is allowed")
        self.assertEqual(len(self.launch_file.read_text().splitlines()), count)
        with self.assertRaises(ProcessLookupError, msg="Child was not reaped after exit/timeout"):
            os.kill(int(self.pid_file.read_text()), 0)
        self.assertEqual(len(self.harness.report["commands"]), count)
        record = self.harness.report["commands"][-1]
        self.assertEqual(record["stage"], "consumer_after" if previous else "consumer_before")
        self.assertEqual(record["timeout_seconds"], 90)
        for fd in self.descriptors:
            with self.assertRaises(OSError, msg="Parent journal descriptor leaked"):
                os.fstat(fd)
        serialized = json.dumps(self.harness.report) + (error or "")
        for forbidden in (self.CANARY, "https://private.invalid/", "STACK01_PROGRESS=",
                          "MLFLOW_TRACKING_PASSWORD", "Traceback", program):
            self.assertNotIn(forbidden, serialized, "Private child content escaped suppression")
        return record, result, error

    def assert_progress(self, record, events, reason):
        self.assertIn("progress", record, "Missing parent-retained private progress evidence")
        self.assertEqual(record["progress"], {"events": events, "reason": reason})
        self.assertEqual(self.harness.report["status"], "FAIL", "Progress alone cannot prove functional PASS")

    def test_real_consumer_reports_before_sdk_import_without_importing_mlflow(self):
        # AC01: execute actual CONSUMER up to the intercepted first SDK import.
        record, _, error = self.invoke(self.child(b"", consumer_source=smoke.CONSUMER))
        self.assertIn("exit_13", error)
        self.assert_progress(record, self.PREFIX, "COMPLETE")

    def test_success_without_receipt_retains_progress_in_both_consumer_stages(self):
        # AC02/AC05: successful subprocess is still not a functional receipt.
        payload = (json.dumps(self.PREFIX[0]) + "\n").encode()
        for previous in (None, SmokeTests().valid_receipt()):
            with self.subTest(stage="after" if previous else "before"):
                record, _, error = self.invoke(self.child(payload), previous)
                self.assertEqual(error, "invalid_receipt_count")
                self.assertEqual(record["outcome"], "PASS")
                self.assert_progress(record, self.PREFIX, "COMPLETE")

    def test_nonzero_exit_retains_progress_without_receipt_or_private_output(self):
        # AC02/AC04: real failure, stdout/stderr/exception canaries suppressed.
        payload = (json.dumps(self.PREFIX[0]) + "\n").encode()
        for previous in (None, SmokeTests().valid_receipt()):
            with self.subTest(stage="after" if previous else "before"):
                ending = f"raise RuntimeError({self.CANARY!r})\n"
                record, _, error = self.invoke(self.child(payload, ending), previous)
                self.assertIn("exit_1_output_suppressed", error)
                self.assertEqual(record["outcome"], "FAIL")
                self.assert_progress(record, self.PREFIX, "COMPLETE")

    def test_real_timeout_retains_progress_and_never_claims_backend_cancellation(self):
        # AC02/AC06: a real sleeping process is killed/reaped by subprocess.run.
        payload = (json.dumps(self.PREFIX[0]) + "\n").encode()
        for previous in (None, SmokeTests().valid_receipt()):
            with self.subTest(stage="after" if previous else "before"):
                record, _, error = self.invoke(self.child(payload, "time.sleep(30)\n"), previous, timeout=True)
                self.assertIn("cancellation_not_proven", error)
                self.assertEqual(record["outcome"], "TIMEOUT")
                self.assertEqual(record["backend_cancelled"], "NOT_PROVEN")
                self.assert_progress(record, self.PREFIX, "COMPLETE")

    def test_invalid_journal_retains_only_valid_prefix_with_closed_reason(self):
        # AC03/AC04: fixed reasons only; malformed fields must never be echoed.
        prefix = (json.dumps(self.PREFIX[0]) + "\n").encode()
        cases = [
            (b"not-json\n", "INVALID_RECORD"),
            (b"[]\n", "INVALID_RECORD"),
            (b'{"phase":true,"state":"after"}\n', "INVALID_RECORD"),
            (b'{"phase":"sdk_import","state":1}\n', "INVALID_RECORD"),
            (json.dumps(dict(self.PREFIX[0], secret=self.CANARY)).encode() + b"\n", "INVALID_RECORD"),
            (b'{"phase":"sdk_import","phase":"sdk_import","state":"after"}\n', "INVALID_RECORD"),
            (json.dumps({"phase": self.CANARY, "state": "after"}).encode() + b"\n", "INVALID_RECORD"),
            (prefix, "INVALID_SEQUENCE"),
            (b'{"phase":"workspace_select","state":"after"}\n', "INVALID_SEQUENCE"),
            (b'{"phase":"sdk_import","state":"after"}', "TRUNCATED"),
            (b"\xff\n", "INVALID_RECORD"),
            (b"x" * 512 + b"\n", "RECORD_LIMIT"),
        ]
        for index, (suffix, reason) in enumerate(cases):
            with self.subTest(case=index, reason=reason):
                record, _, error = self.invoke(self.child(prefix + suffix, "sys.exit(7)\n"))
                self.assertIn("exit_7", error)
                self.assert_progress(record, self.PREFIX, reason)

    def test_stdout_cannot_supply_progress_when_private_journal_is_empty(self):
        # AC03/AC04: untrusted stdout can never act as the progress channel.
        record, _, error = self.invoke(self.child(b"", "sys.exit(7)\n"))
        self.assertIn("exit_7", error)
        self.assert_progress(record, [], "EMPTY")

    def test_complete_progress_does_not_replace_or_change_final_consumer_receipt(self):
        # AC01/AC05: both stages still use the existing workspace/hash receipt.
        expected = SmokeTests().valid_receipt()
        ending = f"print({('STACK01_RECEIPT=' + json.dumps(expected))!r})\n"
        for previous in (None, expected):
            with self.subTest(stage="after" if previous else "before"):
                events = self.events(bool(previous))
                payload = "".join(json.dumps(event) + "\n" for event in events).encode()
                record, result, error = self.invoke(self.child(payload, ending), previous)
                self.assertIsNone(error)
                self.assertEqual(result, expected)
                self.assert_progress(record, events, "COMPLETE")

    def test_exact_record_event_and_total_boundaries(self):
        first = json.dumps(self.PREFIX[0]).encode()
        exact = first + b" " * (511 - len(first)) + b"\n"
        record, _, _ = self.invoke(self.child(exact, "sys.exit(7)\n"))
        self.assert_progress(record, self.PREFIX, "COMPLETE")
        record, _, _ = self.invoke(self.child(exact[:-1] + b" \n", "sys.exit(7)\n"))
        self.assert_progress(record, [], "RECORD_LIMIT")
        line = first + b"\n"
        for count, reason in ((64, "INVALID_SEQUENCE"), (65, "EVENT_LIMIT")):
            with self.subTest(events=count):
                record, _, _ = self.invoke(self.child(line * count, "sys.exit(7)\n"))
                self.assert_progress(record, self.PREFIX, reason)
        # A valid sequence cannot fill 32 KiB with <=512-byte records; exact
        # total size is not TOTAL_LIMIT, but still rejects the oversized suffix.
        for size, reason in ((32768, "RECORD_LIMIT"), (32769, "TOTAL_LIMIT")):
            with self.subTest(bytes=size):
                payload = line + b"x" * (size - len(line))
                record, _, _ = self.invoke(self.child(payload, "sys.exit(7)\n"))
                self.assert_progress(record, self.PREFIX, reason)

    def test_malformed_or_incomplete_progress_cannot_validate_a_receipt(self):
        expected = SmokeTests().valid_receipt()
        ending = f"print({('STACK01_RECEIPT=' + json.dumps(expected))!r})\n"
        for payload in (b"", b"not-json\n", (json.dumps(self.PREFIX[0]) + "\n").encode()):
            with self.subTest(payload=payload):
                _, result, error = self.invoke(self.child(payload, ending))
                self.assertIsNone(result)
                self.assertEqual(error, "consumer_progress_incomplete")

    def actual_consumer(self, fail=None):
        # Only SDK/helper boundaries are doubled. The entire real consumer,
        # its os.write calls, real filesystem and parent collector execute.
        setup = r'''
import tempfile, types
from types import SimpleNamespace as NS
tempfile.tempdir = TEMP_DIRECTORY
def call(phase):
    if phase == FAIL_PHASE:
        raise RuntimeError(PRIVATE_CANARY)
workspace = os.environ["MLFLOW_WORKSPACE"]
info = NS(run_id="a" * 32, status="FINISHED", artifact_uri="mlflow-artifacts:/workspaces/test/1/run/artifacts")
class Run:
    def __enter__(self):
        call("run_start")
        return NS(info=info)
    def __exit__(self, *args):
        call("run_end")
class Client:
    def __init__(self): call("client")
    def get_run(self, run_id):
        call("run_query")
        assert run_id == info.run_id
        return NS(info=info)
    def download_artifacts(self, run_id, path, directory):
        call("artifact_download")
        target = Path(directory) / "download.txt"
        target.write_bytes(b"STACK01 synthetic artifact\n")
        return str(target)
def get_workspace(name):
    call("workspace_metadata")
    assert name == workspace
    return NS(name=name)
def configuration():
    call("configuration")
    return NS(tracking_username="synthetic-user", tracking_password=PRIVATE_CANARY, workspace=workspace)
sdk = types.ModuleType("mlflow")
sdk.__version__ = "3.16.0"
sdk.create_workspace = lambda name: call("workspace_create")
sdk.set_workspace = lambda name: call("workspace_select")
sdk.MlflowClient = Client
sdk.create_experiment = lambda name: (call("experiment"), "1")[1]
sdk.start_run = lambda **kwargs: Run()
sdk.log_artifact = lambda *args: call("artifact_log")
sdk.get_workspace = get_workspace
helper = types.ModuleType("invoiceops_ml.mlflow")
helper.mlflow_config_from_env = configuration
helper.configure_mlflow = lambda config: call("configure")
sys.modules["mlflow"] = sdk
sys.modules["invoiceops_ml"] = types.ModuleType("invoiceops_ml")
sys.modules["invoiceops_ml.mlflow"] = helper
'''
        setup = (f"TEMP_DIRECTORY = {self.directory.name!r}\nFAIL_PHASE = {fail!r}\n"
                 f"PRIVATE_CANARY = {self.CANARY!r}\nfrom pathlib import Path\n" + setup)
        return self.child(b"") + setup + f"exec(compile({smoke.CONSUMER!r}, '<consumer>', 'exec'))\n"

    def test_actual_consumer_full_creation_and_persistence_sequences(self):
        expected = SmokeTests().valid_receipt()
        for previous in (None, expected):
            with self.subTest(persistence=bool(previous)):
                record, result, error = self.invoke(self.actual_consumer(), previous)
                self.assertIsNone(error)
                self.assertEqual(result, expected)
                self.assert_progress(record, self.events(bool(previous)), "COMPLETE")

    def test_actual_consumer_locates_each_external_operation_failure(self):
        for phase in ("configuration", "configure", "workspace_create", "workspace_select",
                      "client", "experiment", "run_start", "artifact_log", "run_end",
                      "run_query", "artifact_download", "workspace_metadata"):
            with self.subTest(operation=phase):
                record, _, error = self.invoke(self.actual_consumer(phase))
                events = self.events()
                stop = events.index({"phase": phase, "state": "before"}) + 1
                self.assertIn("exit_1", error)
                self.assert_progress(record, events[:stop], "COMPLETE")

    def test_collector_read_failure_preserves_primary_failure_and_closes_fd(self):
        with patch.object(smoke, "collect_progress", side_effect=OSError(self.CANARY)):
            record, _, error = self.invoke(self.child(b"", "sys.exit(7)\n"))
        self.assertIn("exit_7", error)
        self.assert_progress(record, [], "INVALID_RECORD")

    def test_collector_failure_on_timeout_preserves_cancellation_uncertainty(self):
        with patch.object(smoke, "collect_progress", side_effect=OSError(self.CANARY)):
            record, _, error = self.invoke(self.child(b"", "time.sleep(30)\n"), timeout=True)
        self.assertIn("cancellation_not_proven", error)
        self.assertEqual(record["backend_cancelled"], "NOT_PROVEN")
        self.assert_progress(record, [], "INVALID_RECORD")

    def test_journal_creation_failure_does_not_launch_a_child(self):
        with patch.object(smoke.tempfile, "TemporaryFile", side_effect=OSError(self.CANARY)), \
                patch.object(smoke.subprocess, "run") as process:
            with self.assertRaisesRegex(smoke.SmokeError, "command_unavailable"):
                self.harness.consumer()
        process.assert_not_called()
        self.assertEqual(self.harness.report["commands"][-1]["outcome"], "UNAVAILABLE")
        self.assertNotIn(self.CANARY, json.dumps(self.harness.report))

    def test_launch_error_closes_journal_without_retry(self):
        descriptors = []
        def unavailable(args, **kwargs):
            descriptors.extend(kwargs["pass_fds"])
            raise OSError(self.CANARY)
        with patch.object(smoke.subprocess, "run", side_effect=unavailable) as process:
            with self.assertRaisesRegex(smoke.SmokeError, "command_unavailable"):
                self.harness.consumer()
        self.assertEqual(process.call_count, 1)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assertNotIn(self.CANARY, json.dumps(self.harness.report))

    def test_journal_close_failure_does_not_replace_primary_exit(self):
        real_file = smoke.tempfile.TemporaryFile
        canary = self.CANARY
        class CloseFailure:
            def __init__(self, *args, **kwargs):
                self.file = real_file(*args, **kwargs)
            def fileno(self):
                return self.file.fileno()
            def seek(self, *args):
                return self.file.seek(*args)
            def read(self, *args):
                if args != (32769,):
                    raise AssertionError("Collector read must remain bounded")
                return self.file.read(*args)
            def close(self):
                self.file.close()
                raise OSError(canary)
        with patch.object(smoke.tempfile, "TemporaryFile", CloseFailure):
            record, _, error = self.invoke(self.child(b"", "sys.exit(7)\n"))
        self.assertIn("exit_7", error)
        self.assert_progress(record, [], "INVALID_RECORD")

    def test_main_diagnostic_failure_still_runs_ownership_safe_shutdown(self):
        report = Path(self.directory.name) / "report.json"
        owned = {"containers": [], "networks": [], "volumes": [{"id": "own", "name": "own"}]}
        def execute(harness):
            harness.started = True
            harness.env.update(MLFLOW_AUTH_ADMIN_USERNAME="synthetic-user", MLFLOW_AUTH_ADMIN_PASSWORD=self.CANARY)
            harness.consumer()
        real_run = subprocess.run
        commands = []
        def boundary(args, **kwargs):
            if args[0] == str(smoke.ML / ".venv/bin/python"):
                return real_run([sys.executable, "-B", "-c", "raise SystemExit(7)"], **kwargs)
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        for mismatch in (False, True):
            target = report.with_name("mismatch.json") if mismatch else report
            def snapshot(harness, label):
                if mismatch:
                    raise smoke.SmokeError("resource_ownership_mismatch")
                harness.report["snapshots"][label] = owned
                return owned
            commands.clear()
            with patch.object(smoke.Harness, "execute", execute), patch.object(smoke.Harness, "snapshot", snapshot), \
                    patch.object(smoke, "validate_inputs"), patch.object(smoke, "collect_progress", side_effect=OSError(self.CANARY)), \
                    patch.object(smoke.subprocess, "run", side_effect=boundary), patch("builtins.print"):
                code = smoke.main(["--project", PROJECT, "--port", "15432", "--report", str(target)])
            result = json.loads(target.read_text())
            self.assertEqual(code, 1)
            self.assertIn("exit_7", result["failure"])
            self.assertNotIn(self.CANARY, json.dumps(result))
            if mismatch:
                self.assertEqual(commands, [])
                self.assertEqual(result["shutdown"], "FAILED_REQUIRES_REVIEW")
            else:
                self.assertEqual(result["shutdown"], "PASS_VOLUMES_PRESERVED")
                self.assertEqual(len(commands), 1)
                self.assertIn("down", commands[0])
                self.assertNotIn("-v", commands[0])
                self.assertNotIn("--volumes", commands[0])


if __name__ == "__main__":
    unittest.main()
