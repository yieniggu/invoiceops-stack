"""Daemon-free contract tests for the opt-in STACK-08 runtime path."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stack_integrated_smoke.py"
SPEC = importlib.util.spec_from_file_location("stack_integrated_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class StackIntegratedSmokeRuntimeTests(unittest.TestCase):
    project = "stack08-proof-runtime01"
    ports = (18580, 18581, 18582, 18583, 18584)

    def receipt_path(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name) / "receipt.json"

    def restricted_environment(self):
        return {"PATH": "/trusted/bin", "NO_PROXY": "*", "STACK_MLFLOW_PORT": "18580"}

    def test_project_is_isolated_and_compose_never_loads_an_env_file(self):
        command = smoke.compose_command(self.project, "up", "--no-build")
        self.assertIn("--project-name", command)
        self.assertEqual(command[command.index("--project-name") + 1], self.project)
        self.assertEqual(command[command.index("--env-file") + 1], "/dev/null")
        self.assertEqual(command[command.index("--project-directory") + 1], str(smoke.MLFLOW))
        with self.assertRaises(smoke.Failure):
            smoke.validate_project("invoiceops-stack")

    def test_offline_preflight_rejects_a_missing_cached_base_before_build(self):
        with patch.object(smoke, "dockerfile_bases", return_value=["cached:latest"]), \
             patch.object(smoke, "local_image_available", return_value=False):
            with self.assertRaisesRegex(smoke.Failure, "offline_build_cache_miss"):
                smoke.offline_build_prerequisites(self.restricted_environment())

    def test_network_build_opt_in_skips_only_the_offline_cache_preflight(self):
        offline = smoke.Harness(self.project, self.ports, self.receipt_path())
        network_opt_in = smoke.Harness(self.project, self.ports, self.receipt_path(), allow_network_build=True)
        with patch.object(smoke, "offline_build_prerequisites") as prerequisites, \
             patch.object(smoke, "local_image_available", return_value=False) as local_image:
            offline.preflight()
            prerequisites.assert_called_once_with(offline.environment)
            network_opt_in.preflight()
        self.assertEqual(local_image.call_count, len(smoke.BUILD_SERVICES) * 2)

    def test_generated_environment_resolves_docker_from_usr_local_bin(self):
        foundry = smoke.FOUNDRY_EXECUTABLE_DIRECTORIES[0]
        with patch.object(smoke.os.path, "isfile", side_effect=lambda path: path in {"/usr/local/bin/docker", f"{foundry}/cast", f"{foundry}/forge"}), \
              patch.object(smoke.os, "access", return_value=True):
            environment = smoke.generated_environment(*self.ports)

        self.assertEqual(environment["PATH"], f"/usr/local/bin:{foundry}:/usr/bin:/bin:/usr/sbin:/sbin")

    def test_generated_environment_fails_closed_when_docker_is_not_localizable(self):
        with patch.object(smoke.os.path, "isfile", return_value=False):
            with self.assertRaisesRegex(smoke.Failure, "docker_cli_not_found"):
                smoke.generated_environment(*self.ports)

    def test_generated_environment_requires_executable_cast_and_forge_in_the_same_trusted_directory(self):
        foundry = smoke.FOUNDRY_EXECUTABLE_DIRECTORIES[0]
        with patch.object(smoke.os.path, "isfile", side_effect=lambda path: path in {"/usr/local/bin/docker", f"{foundry}/cast"}), \
              patch.object(smoke.os, "access", return_value=True):
            with self.assertRaisesRegex(smoke.Failure, "foundry_cli_not_found"):
                smoke.generated_environment(*self.ports)

    def test_generated_environment_fails_closed_when_foundry_executables_are_not_executable(self):
        foundry = smoke.FOUNDRY_EXECUTABLE_DIRECTORIES[0]
        with patch.object(smoke.os.path, "isfile", side_effect=lambda path: path in {"/usr/local/bin/docker", f"{foundry}/cast", f"{foundry}/forge"}), \
              patch.object(smoke.os, "access", side_effect=lambda path, mode: path == "/usr/local/bin/docker"):
            with self.assertRaisesRegex(smoke.Failure, "foundry_cli_not_found"):
                smoke.generated_environment(*self.ports)

    def test_generated_environment_preserves_the_restricted_path(self):
        foundry = smoke.FOUNDRY_EXECUTABLE_DIRECTORIES[0]
        with patch.dict(smoke.os.environ, {"PATH": "/sensitive/bin", "AWS_SECRET_ACCESS_KEY": "secret"}, clear=True), \
              patch.object(smoke.os.path, "isfile", side_effect=lambda path: path in {"/usr/local/bin/docker", f"{foundry}/cast", f"{foundry}/forge"}), \
              patch.object(smoke.os, "access", return_value=True):
            environment = smoke.generated_environment(*self.ports)

        self.assertEqual(environment["PATH"], f"/usr/local/bin:{foundry}:/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)

    def test_preflight_ownership_and_diagnostic_docker_helpers_use_the_restricted_environment(self):
        environment = self.restricted_environment()

        with patch.dict(smoke.os.environ, {"PATH": "/sensitive/bin", "AWS_SECRET_ACCESS_KEY": "secret"}, clear=True), \
             patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process:
            self.assertTrue(smoke.local_image_available("cached:latest", environment))
        self.assertIs(process.call_args.kwargs["env"], environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", process.call_args.kwargs["env"])

        def inspect_with_label(command, **kwargs):
            kwargs["stdout"].write((self.project + "\n").encode("utf-8"))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(smoke.subprocess, "run", side_effect=inspect_with_label) as process:
            self.assertEqual(smoke.owned_label("containers", "container-a", environment), self.project)
        self.assertIs(process.call_args.kwargs["env"], environment)

        def inspect_with_id(command, **kwargs):
            kwargs["stdout"].write(b"sha256:abc\n")
            return subprocess.CompletedProcess(command, 0)

        with patch.object(smoke.subprocess, "run", side_effect=inspect_with_id) as process:
            self.assertEqual(smoke.bounded_docker_output(smoke.docker_command("image", "inspect", "image-a"), environment),
                             "sha256:abc\n")
        self.assertIs(process.call_args.kwargs["env"], environment)

    def test_cli_requires_an_explicit_flag_to_forward_network_build_authorization(self):
        for allow_network_build in (False, True):
            path = self.receipt_path()
            arguments = ["--execute", "--project", self.project, "--receipt", str(path)]
            if allow_network_build:
                arguments.append("--allow-network-build")
            with self.subTest(allow_network_build=allow_network_build), \
                 patch.object(smoke, "Harness") as harness:
                harness.return_value.finish.return_value = "PASS"
                self.assertEqual(smoke.main(arguments), 0)
                harness.assert_called_once_with(self.project, self.ports, path, allow_network_build)

    def test_build_is_explicit_and_every_up_disables_build_and_pull(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        calls = []
        with patch.object(smoke, "suppressed", side_effect=lambda command, *args, **kwargs: calls.append(command)):
            harness.build()
            harness.up("mlflow", wait=42)
        self.assertIn("build", calls[0])
        self.assertEqual(calls[0][-len(smoke.BUILD_SERVICES):], list(smoke.BUILD_SERVICES))
        self.assertIn("--no-build", calls[1])
        self.assertEqual(calls[1][calls[1].index("--pull") + 1], "never")
        self.assertIn("--wait", calls[1])

    def test_execute_requires_health_fixture_prediction_and_owned_evidence_in_order(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        events = []
        requests = []

        def model_fixture():
            events.append("fixture")
            return {"features": {}}

        def request(url, *args):
            requests.append(url)
            return [(200, {}), (200, {}), (200, {"probability": 0.5})][len(requests) - 1]

        with patch.object(harness, "preflight", side_effect=lambda: events.append("preflight")), \
              patch.object(harness, "build", side_effect=lambda: events.append("build")), \
              patch.object(harness, "up", side_effect=lambda *services, **kwargs: events.append("up:" + ",".join(services))), \
              patch.object(harness, "deploy_contract", side_effect=lambda: events.append("contract")), \
              patch.object(harness, "bootstrap_app", side_effect=lambda: events.append("bootstrap-app")), \
              patch.object(harness, "model_fixture", side_effect=model_fixture), \
              patch.object(harness, "prediction_evidence", side_effect=lambda: {"batchId": "batch", "rootHash": "0x" + "a" * 64}), \
              patch.object(harness, "anchor_and_reconcile", side_effect=lambda batch: events.append("anchor-reconcile")), \
              patch.object(harness, "collect_owned", side_effect=lambda: events.append("evidence")), \
              patch.object(smoke, "http_json", side_effect=request):
            harness.execute()
        self.assertEqual(events[0:4], ["preflight", "build", "up:anvil", "contract"])
        self.assertIn("up:postgres,minio,minio-init,mlflow,db", events)
        self.assertLess(events.index("fixture"), events.index("up:model-api"))
        self.assertIn("up:backend,frontend", events)
        self.assertIn("bootstrap-app", events)
        self.assertIn("anchor-reconcile", events)
        self.assertEqual(events[-1], "evidence")
        self.assertEqual(harness.phases, ["preflight", "build", "anvil", "contract", "mlflow", "model_api", "app_schema", "app", "prediction", "evidence", "anchor_reconcile"])
        self.assertEqual(requests[0], f"http://127.0.0.1:{self.ports[1]}/models/invoice-review/health")
        self.assertEqual(requests[1], f"http://127.0.0.1:{self.ports[2]}/health/live")

    def test_model_fixture_provisions_with_a_one_off_model_api_before_service_start(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        module = type("FixtureModule", (), {
            "SDK": "fixture-sdk",
            "receipt": staticmethod(lambda raw: {}),
            "Harness": type("FixtureHarness", (), {"predict_payload": staticmethod(lambda: {"features": {}})}),
        })

        with patch.object(smoke, "model_smoke_module", return_value=module), \
              patch.object(smoke, "bounded_child_receipt", side_effect=[{"experiment": "42"}, {"run_id": "r", "version": "1"}]) as receipt:
            harness.model_fixture()

        fixture_command = receipt.call_args_list[1].args[0]
        self.assertIn("run", fixture_command)
        self.assertEqual(fixture_command[-5:], ["model-api", "-B", "-c", "fixture-sdk", "fixture"])
        self.assertIn("--no-deps", fixture_command)

    def test_model_fixture_forwards_mlflow_reader_credentials_only_to_bootstrap(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        username, password = "reader-secret-username", "reader-secret-password"
        harness.environment.update(STACK_MODEL_API_MLFLOW_USERNAME=username,
                                   STACK_MODEL_API_MLFLOW_PASSWORD=password)
        module = type("FixtureModule", (), {
            "SDK": "fixture-sdk",
            "receipt": staticmethod(lambda raw: {}),
            "Harness": type("FixtureHarness", (), {"predict_payload": staticmethod(lambda: {"features": {}})}),
        })

        with patch.object(smoke, "model_smoke_module", return_value=module), \
              patch.object(smoke, "bounded_child_receipt", side_effect=[{"experiment": "42"},
                                                                           {"run_id": "r", "version": "1"}]) as receipt:
            result = harness.model_fixture()

        bootstrap_command, bootstrap_environment = receipt.call_args_list[0].args[:2]
        fixture_command = receipt.call_args_list[1].args[0]
        bootstrap_keys = [bootstrap_command[index + 1] for index, value in enumerate(bootstrap_command[:-1])
                          if value == "-e"]
        self.assertIn("STACK_MODEL_API_MLFLOW_USERNAME", bootstrap_keys)
        self.assertIn("STACK_MODEL_API_MLFLOW_PASSWORD", bootstrap_keys)
        self.assertNotIn(username, bootstrap_command)
        self.assertNotIn(password, bootstrap_command)
        self.assertNotIn("STACK_MODEL_API_MLFLOW_USERNAME", fixture_command)
        self.assertNotIn("STACK_MODEL_API_MLFLOW_PASSWORD", fixture_command)
        self.assertEqual(bootstrap_environment["STACK_MODEL_API_MLFLOW_USERNAME"], username)
        self.assertEqual(bootstrap_environment["STACK_MODEL_API_MLFLOW_PASSWORD"], password)
        self.assertEqual(result, {"features": {}})
        self.assertNotIn(username, repr(result))
        self.assertNotIn(password, repr(result))

    def test_model_fixture_uses_admin_credentials_only_for_the_write_fixture(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        module = type("FixtureModule", (), {
            "SDK": "fixture-sdk", "receipt": staticmethod(lambda raw: {}),
            "Harness": type("FixtureHarness", (), {"predict_payload": staticmethod(lambda: {"features": {}})}),
        })
        with patch.object(smoke, "model_smoke_module", return_value=module), \
              patch.object(smoke, "bounded_child_receipt", side_effect=[{"experiment": "42"}, {"run_id": "r", "version": "1"}]) as receipt:
            harness.model_fixture()

        fixture_command, fixture_environment = receipt.call_args_list[1].args[:2]
        fixture_keys = [fixture_command[index + 1] for index, value in enumerate(fixture_command[:-1]) if value == "-e"]
        self.assertIn("MLFLOW_TRACKING_USERNAME", fixture_keys)
        self.assertIn("MLFLOW_TRACKING_PASSWORD", fixture_keys)
        self.assertEqual(fixture_environment["MLFLOW_TRACKING_USERNAME"], harness.environment["MLFLOW_AUTH_ADMIN_USERNAME"])
        self.assertEqual(fixture_environment["MLFLOW_TRACKING_PASSWORD"], harness.environment["MLFLOW_AUTH_ADMIN_PASSWORD"])
        self.assertNotIn(harness.environment["MLFLOW_AUTH_ADMIN_USERNAME"], fixture_command)
        self.assertNotIn(harness.environment["MLFLOW_AUTH_ADMIN_PASSWORD"], fixture_command)

    def test_prediction_evidence_creates_a_batch_after_a_model_api_decision(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        cookie = MagicMock(value="session-token")
        jar = MagicMock()
        jar.__len__.return_value = 1
        jar.__iter__.return_value = iter([cookie])
        responses = iter([
            (200, {"user": {}}),
            (200, {"profile": {"id": "user-id", "memberships": [{"organization": {"id": "organization-id"}}]}}),
            (200, {"invoices": [{"invoiceId": "invoice-id"}]}),
            (200, {"auditEvent": {"correlationId": "correlation-id", "policyProbabilitySource": "MODEL_API",
                                    "modelId": "invoice-review", "modelVersion": "1", "modelRunId": "run-id"}}),
        ])

        with patch.object(smoke, "CookieJar", return_value=jar), \
             patch.object(smoke, "HTTPCookieProcessor"), \
             patch.object(smoke, "build_opener", return_value=MagicMock()), \
             patch.object(smoke, "http_json", side_effect=lambda *args, **kwargs: next(responses)), \
             patch.object(smoke, "bounded_json_child", return_value={"batchId": "batch-id", "rootHash": "0x" + "a" * 64}) as child:
            batch = harness.prediction_evidence()

        self.assertEqual(batch["batchId"], "batch-id")
        self.assertEqual(harness.environment["STACK08_CORRELATION_ID"], "correlation-id")
        child.assert_called_once()

    def test_prediction_evidence_validates_model_api_lineage_after_the_evidence_batch_lookup(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        cookie = MagicMock(value="session-token")
        jar = MagicMock()
        jar.__len__.return_value = 1
        jar.__iter__.return_value = iter([cookie])
        responses = iter([
            (200, {"user": {}}),
            (200, {"profile": {"id": "user-id", "memberships": [{"organization": {"id": "organization-id"}}]}}),
            (200, {"invoices": [{"invoiceId": "invoice-id"}]}),
            (200, {"auditEvent": {"correlationId": "correlation-id", "policyProbabilitySource": "MODEL_API_FALLBACK"}}),
        ])

        with patch.object(smoke, "CookieJar", return_value=jar), \
             patch.object(smoke, "HTTPCookieProcessor"), \
             patch.object(smoke, "build_opener", return_value=MagicMock()), \
             patch.object(smoke, "http_json", side_effect=lambda *args, **kwargs: next(responses)), \
             patch.object(smoke, "bounded_json_child", return_value={"batchId": "batch-id", "rootHash": "0x" + "a" * 64}) as child:
            with self.assertRaisesRegex(smoke.Failure, "prediction_model_lineage_missing"):
                harness.prediction_evidence()

        child.assert_called_once()

    def test_failed_fixture_retains_only_a_validated_diagnostic_stage(self):
        command = ["fixture-child"]
        diagnostic = lambda raw: {"stage": "bootstrap_user"} if raw == "child-output" else None
        def failed_child(*args, **kwargs):
            kwargs["stdout"].write(b"child-output")
            return subprocess.CompletedProcess(command, 1)
        with patch.object(smoke.subprocess, "run", side_effect=failed_child):
            with self.assertRaisesRegex(smoke.Failure, "fixture_child_failed_at_bootstrap_user"):
                smoke.bounded_child_receipt(command, self.restricted_environment(), {}, lambda raw: {}, diagnostic)

    def test_failed_fixture_suppresses_unrecognized_child_output(self):
        command = ["fixture-child"]
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess(command, 1)):
            with self.assertRaisesRegex(smoke.Failure, "fixture_child_failed_output_suppressed"):
                smoke.bounded_child_receipt(command, self.restricted_environment(), {}, lambda raw: {}, lambda raw: None)

    def test_failed_local_child_retains_only_a_validated_anchor_diagnostic(self):
        command = ["anchor-child"]

        def failed_child(*args, **kwargs):
            kwargs["stderr"].write(
                b"EVIDENCE_ANCHOR_DIAGNOSTIC phase=execute operation=anchor reason=configuration outcome=failed\n"
            )
            return subprocess.CompletedProcess(command, 1)

        with patch.object(smoke.subprocess, "run", side_effect=failed_child):
            with self.assertRaisesRegex(smoke.Failure, "local_child_failed_at_execute_anchor_configuration"):
                smoke.bounded_json_child(command, self.restricted_environment(), smoke.anchor_diagnostic)

    def test_cleanup_discovers_only_label_verified_resources_after_an_early_failure(self):
        harness = smoke.Harness(self.project, self.ports, self.receipt_path())
        harness.started = True
        owned = {"containers": ["container-a"]}
        with patch.object(smoke, "listed_owned_resources", return_value=owned), \
              patch.object(smoke, "remove_owned_resources") as remove, \
              patch.object(smoke, "remove_derived_images") as images:
            harness.cleanup()
        remove.assert_called_once_with(self.project, owned, harness.environment)
        images.assert_called_once_with(self.project, harness.environment)

    def test_listed_owned_resources_uses_only_the_exact_project_label(self):
        environment = self.restricted_environment()
        outputs = iter(("container-a\n", "network-a\n", "volume-a\n"))
        with patch.object(smoke, "bounded_docker_output", side_effect=lambda command, actual: next(outputs)) as output:
            self.assertEqual(smoke.listed_owned_resources(self.project, environment), {
                "containers": ["container-a"], "networks": ["network-a"], "volumes": ["volume-a"],
            })
        for command, actual_environment in (call.args for call in output.call_args_list):
            self.assertIn(smoke.label_filter(self.project), command)
            self.assertIs(actual_environment, environment)

    def test_cleanup_is_fail_closed_and_the_receipt_is_sanitized(self):
        path = self.receipt_path()
        harness = smoke.Harness(self.project, self.ports, path)
        harness.started = True
        harness.owned = {"containers": ["owned-container"]}
        with patch.object(smoke, "remove_owned_resources", side_effect=smoke.Failure("ownership_changed")):
            with self.assertRaisesRegex(smoke.Failure, "ownership_changed"):
                harness.cleanup()

    def test_remove_owned_resources_reinspects_every_resource_immediately_after_removal(self):
        owned = {"containers": ["container-a"], "networks": ["network-a"],
                 "volumes": ["volume-a"]}
        events = []

        environment = self.restricted_environment()

        def verify(project, expected, actual_environment):
            self.assertIs(actual_environment, environment)
            events.append(("verify", next(iter(expected.values()))[0]))
            return expected

        def remove(command, actual_environment):
            self.assertIs(actual_environment, environment)
            events.append(("remove", command[-1]))

        def verify_absent(kind, identity):
            events.append(("absent", identity))

        with patch.object(smoke, "verify_owned", side_effect=verify), \
             patch.object(smoke, "suppressed", side_effect=remove), \
             patch.object(smoke, "verify_removed_resource", side_effect=verify_absent) as absence_check:
            smoke.remove_owned_resources(self.project, owned, environment)

        self.assertEqual(events, [
            ("verify", "container-a"), ("verify", "container-a"), ("remove", "container-a"),
            ("absent", "container-a"), ("verify", "network-a"), ("remove", "network-a"),
            ("absent", "network-a"), ("verify", "volume-a"), ("remove", "volume-a"),
            ("absent", "volume-a"),
        ])
        self.assertEqual(absence_check.call_args_list, [
            call("containers", "container-a"),
            call("networks", "network-a"),
            call("volumes", "volume-a"),
        ])

    def test_post_removal_inspection_accepts_only_docker_404(self):
        with patch.object(smoke, "docker_resource_status", return_value=404):
            smoke.verify_removed_resource("containers", "container-a")
        with patch.object(smoke, "docker_resource_status", return_value=200):
            with self.assertRaisesRegex(smoke.Failure, "post_removal_inspection_failed_closed"):
                smoke.verify_removed_resource("containers", "container-a")

    def test_finish_records_cleanup_failure_without_child_output(self):
        path = self.receipt_path()
        harness = smoke.Harness(self.project, self.ports, path)
        with patch.object(harness, "execute"), patch.object(harness, "cleanup", side_effect=smoke.Failure("ownership_changed")):
            self.assertEqual(harness.finish(), "FAIL")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["failure"], "cleanup_failed_closed")
        self.assertEqual(receipt["output_bounds"]["child_output"], "NOT_RETAINED")
        self.assertNotIn("stdout", receipt)
        self.assertNotIn("stderr", receipt)


if __name__ == "__main__":
    unittest.main()
