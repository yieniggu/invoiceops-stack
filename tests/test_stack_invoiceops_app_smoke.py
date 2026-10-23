"""Daemon-free contract tests for the opt-in STACK-03 harness."""

import importlib.util
import json
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stack_invoiceops_app_smoke.py"
spec = importlib.util.spec_from_file_location("stack03_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class StackInvoiceopsAppSmokeTests(unittest.TestCase):
    def report(self, directory):
        return Path(directory) / "proof.json"

    def rendered_services(self, names, app_password):
        services = {name: {} for name in names}
        services["backend"] = {"ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 3000,
                                             "published": "18100", "protocol": "tcp"}], "environment": {}}
        services["frontend"] = {"ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 80,
                                              "published": "18101", "protocol": "tcp"}]}
        services["db"] = {"environment": {"POSTGRES_PASSWORD": app_password}}
        return json.dumps({"services": services})

    def test_static_check_never_invokes_subprocess(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke.subprocess, "run") as run:
            result = smoke.main(["--check", "--project", "stack03-proof-unit123", "--backend-port", "18100",
                                 "--frontend-port", "18101", "--report", str(self.report(directory))])
        self.assertEqual(result, 0)
        run.assert_not_called()

    def test_input_contract_rejects_reused_project_ports_and_unsafe_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory)
            for project, ports, path in (("stack03", (18100, 18101), report),
                                         ("stack03-proof-unit123", (18100, 18100), report),
                                         ("stack03-proof-unit123", (18100, 18101), Path("relative.json"))):
                with self.subTest(project=project, ports=ports, path=path):
                    with self.assertRaises(smoke.Failure):
                        smoke.validate_inputs(project, *ports, path)

    def test_cleanup_refuses_changed_or_unverified_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.started = True
            harness.owned_ids = {"containers": {"stack03-proof-unit123-backend-1": "one"},
                                  "networks": {"stack03-proof-unit123_default": "two"}, "volumes": {}, "images": {}}
            with patch.object(harness, "verify_owned", side_effect=smoke.Failure("ownership_changed")), \
                  patch.object(harness, "cleanup_command") as cleanup:
                with self.assertRaises(smoke.Failure):
                    harness.shutdown()
            cleanup.assert_not_called()

    def test_cleanup_removes_only_verified_containers_networks_and_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.started = True
            before = {
                "containers": {"stack03-proof-unit123-backend-1": "container-id"},
                "networks": {"stack03-proof-unit123_default": "network-id"},
                "volumes": {"stack03-proof-unit123_app_postgres_data": "stack03-proof-unit123_app_postgres_data"},
                "images": {"sha256:" + "a" * 64: "sha256:" + "a" * 64},
            }
            with patch.object(harness, "verify_owned", side_effect=[before, before, before, before, before, before,
                                                                        before, before,
                                                                        {"containers": {}, "networks": {}, "volumes": {}, "images": {}}]), \
                  patch.object(harness, "cleanup_command", return_value=True) as cleanup:
                harness.shutdown()
            self.assertEqual(cleanup.call_args_list, [
                (("rm", "-f", "container-id"), {}),
                (("network", "rm", "network-id"), {}),
                (("volume", "rm", "stack03-proof-unit123_app_postgres_data"), {}),
                (("image", "rm", "sha256:" + "a" * 64), {}),
            ])
            self.assertEqual(harness.report["shutdown"], "PASS_ALL_OWNED_RESOURCES_REMOVED")

    def test_cleanup_runs_after_a_started_stage_fails_before_inventory_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory)
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, report)
            harness.started = True
            owned = {"containers": {"stack03-proof-unit123-db-1": "db-id"}, "networks": {}, "volumes": {}, "images": {}}
            with patch.object(harness, "execute", side_effect=smoke.Failure("command_unavailable_or_timed_out")), \
                  patch.object(harness, "verify_owned", side_effect=[owned, owned, owned,
                                                                        {"containers": {}, "networks": {}, "volumes": {}, "images": {}}]), \
                 patch.object(harness, "cleanup_command", return_value=True) as cleanup:
                result = harness.finish()
            self.assertEqual(result["status"], "FAIL")
            cleanup.assert_called_once_with("rm", "-f", "db-id")

    def test_preflight_accepts_exact_rendered_service_set(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            with patch.object(harness, "inventory", return_value={"containers": {}, "networks": {}, "volumes": {}}), \
                 patch.object(harness, "compose", side_effect=lambda *_args, **_kwargs: self.rendered_services(
                     smoke.SERVICES, harness.env["STACK_APP_POSTGRES_PASSWORD"]
                 )):
                harness.preflight()

    def test_preflight_rejects_unspecified_rendered_service(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            names = smoke.SERVICES | {"unspecified-service"}
            with patch.object(harness, "inventory", return_value={"containers": {}, "networks": {}, "volumes": {}}), \
                 patch.object(harness, "compose", side_effect=lambda *_args, **_kwargs: self.rendered_services(
                     names, harness.env["STACK_APP_POSTGRES_PASSWORD"]
                 )):
                with self.assertRaisesRegex(smoke.Failure, "unexpected_services"):
                    harness.preflight()

    def test_receipt_is_bounded_sanitized_and_retained_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory)
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, report)
            harness.env = {"STACK_POSTGRES_PASSWORD": "must-not-appear"}
            harness.report["raw_env"] = "must-not-appear"
            harness.report["cookie"] = "session-cookie-must-not-appear"
            harness.report["payload"] = "body-must-not-appear"
            harness.report["private_path"] = "/Users/operator/private-must-not-appear"
            harness.finish()
            value = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(value["status"], "FAIL")
            self.assertNotIn("must-not-appear", report.read_text(encoding="utf-8"))
            self.assertNotIn("session-cookie-must-not-appear", report.read_text(encoding="utf-8"))
            self.assertNotIn("body-must-not-appear", report.read_text(encoding="utf-8"))
            self.assertNotIn("private-must-not-appear", report.read_text(encoding="utf-8"))
            self.assertEqual(value["schema"], "STACK03-receipt-v3")
            self.assertRegex(value["timestamp_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(value["input_source"]["revision"], smoke.git_revision())
            self.assertRegex(value["input_source"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(set(value["input_source"]["components"]), {
                "invoiceops-stack", "invoiceops-app", "invoiceops-model-api", "invoiceops-mlflow",
            })
            for component in value["input_source"]["components"].values():
                self.assertRegex(component["revision"], r"^(?:[0-9a-f]{40}|UNAVAILABLE)$")
                self.assertRegex(component["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(component["files"], 0)
            self.assertEqual(value["output_bounds"]["child_output"], "NOT_RETAINED")
            self.assertEqual(value["cleanup"]["status"], "NOT_NEEDED")
            self.assertLessEqual(report.stat().st_size, value["output_bounds"]["receipt_bytes_max"])
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)

    def test_execute_requires_a_new_stack_owned_receipt_path(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory)
            with self.assertRaisesRegex(smoke.Failure, "receipt_path_not_stack_owned"):
                smoke.validate_receipt_path(report)
        self.assertEqual(smoke.RECEIPT_ROOT, SCRIPT.parents[1] / "evidence" / "stack03")

    def test_receipt_provenance_rejects_missing_or_mismatched_built_inputs(self):
        provenance = smoke.source_provenance()
        missing = {**provenance, "components": {"invoiceops-stack": provenance["components"]["invoiceops-stack"]}}
        with self.assertRaisesRegex(smoke.Failure, "missing_source_provenance"):
            smoke.validate_source_provenance(missing)
        mismatched = json.loads(json.dumps(provenance))
        mismatched["components"]["invoiceops-app"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(smoke.Failure, "mismatched_source_provenance"):
            smoke.validate_source_provenance(mismatched)

    def test_inventory_accepts_short_docker_image_ids_only_after_label_filtering(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            responses = iter(["", "", "", '{"ID":"abc123def456"}\n'])
            with patch.object(harness, "docker_command", side_effect=lambda *_args, **_kwargs: next(responses)):
                inventory = harness.inventory()
        self.assertEqual(inventory["images"], {"abc123def456": "abc123def456"})

    def test_command_classifies_fixed_exit_semantics_without_child_output(self):
        self.assertEqual(smoke.command_failure_code(125, "compose_config"),
                         "compose_config_exit_125_output_suppressed")

    def test_build_failure_classification_discards_raw_stderr(self):
        self.assertEqual(
            smoke.build_failure_code("#12 ERROR: process pnpm --filter @invoiceops/backend build failed"),
            "backend_compile_failed",
        )
        self.assertEqual(smoke.build_failure_code("unrecognized failure"), "unspecified_build_failed")

    def test_build_command_classifies_transient_stdout_without_retaining_it(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"PATH": "/usr/bin"}
            result = Mock(returncode=1, stdout="pnpm --filter @invoiceops/frontend build", stderr="")
            with patch.object(smoke.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(smoke.Failure, "frontend_compile_failed"):
                    harness.command(["docker", "compose", "build"], operation="build")

    def test_command_uses_explicit_environment_for_docker_exec_values(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            result = Mock(returncode=0, stdout="", stderr="")
            environment = {"PATH": "/usr/bin", "MLFLOW_TRACKING_URI": "http://127.0.0.1:5000"}
            with patch.object(smoke.subprocess, "run", return_value=result) as run:
                harness.command(["docker", "exec"], environment=environment)
            self.assertIs(run.call_args.kwargs["env"], environment)

    def test_build_uses_compose_default_no_pull_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            with patch.object(harness, "compose") as compose:
                harness.build()
            self.assertEqual(compose.call_args_list, [
                (("build",), {"timeout": 900}),
                (("build", "app-bootstrap"), {"timeout": 900}),
            ])

    def test_model_api_starts_before_fixture_without_waiting_for_unseeded_health(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            with patch.object(harness, "compose") as compose, \
                 patch.object(harness, "wait_for_model_api_http") as wait:
                harness.start_model_api()
            compose.assert_called_once_with("up", "-d", "--no-build", "--no-deps", "model-api", timeout=60)
            wait.assert_called_once_with()

    def test_model_api_http_readiness_accepts_unseeded_health_response(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            with patch.object(harness, "model_http", return_value=(503, {})) as request:
                harness.wait_for_model_api_http()
            request.assert_called_once_with("GET", "/models/invoice-review/health")

    def test_model_refresh_allows_the_configured_server_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"MODEL_API_ADMIN_TOKEN": "synthetic"}
            harness.owned_ids = {"containers": {"stack03-proof-unit123-db-1": "db-id"},
                                 "networks": {}, "volumes": {}, "images": {}}
            with patch.object(harness, "model_sdk", side_effect=[
                     {"experiment": "owned", "reader_nonadmin": True, "exact_read_role": True},
                     {"run_id": "run", "version": "1"},
                 ]), \
                 patch.object(harness, "model_http", return_value=(200, {"model_id": "invoice-review", "run_id": "run", "model_version": "1"})) as request, \
                 patch.object(harness, "verify_owned", side_effect=[harness.owned_ids, harness.owned_ids]), \
                 patch.object(harness, "compose"):
                harness.bootstrap_model_and_app_database()
            self.assertEqual(request.call_args_list[0], (("POST", "/admin/models/invoice-review/refresh"), {
                "token": "synthetic", "timeout": 130,
            }))

    def test_model_http_retries_transport_reset_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"STACK_MODEL_API_PORT": "18102"}
            with patch.object(smoke, "build_opener", return_value=Mock(open=Mock(side_effect=ConnectionResetError))):
                with self.assertRaisesRegex(smoke.Failure, "model_api_unavailable"):
                    harness.model_http("GET", "/models/invoice-review/health")

    def test_authenticated_posts_include_the_loopback_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            response = Mock(status=200, read=Mock(return_value=b"{}"))
            with patch.object(smoke, "build_opener", return_value=Mock(open=Mock(return_value=response))) as build:
                harness.http("POST", "/invoices/id/decision", {"mode": "RULE_V1"})
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Origin"), "http://127.0.0.1:18100")

    def test_bootstrap_run_enables_its_explicit_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"PATH": "/usr/bin"}
            result = Mock(returncode=0, stdout="", stderr="")
            with patch.object(smoke.subprocess, "run", return_value=result) as run:
                harness.compose("run", "--rm", "app-bootstrap")
            self.assertIn("stack03-bootstrap", run.call_args.args[0])

    def test_compose_up_failure_classification_discards_raw_output(self):
        self.assertEqual(
            smoke.compose_up_failure_code("dependency failed to start: service model-api is unhealthy"),
            "dependency_health_failed",
        )
        self.assertEqual(smoke.compose_up_failure_code("unrecognized failure"), "unspecified_compose_up_failed")

    def test_app_bootstrap_failure_classification_discards_raw_output(self):
        self.assertEqual(
            smoke.app_bootstrap_failure_code("Error: Cannot find package 'dotenv' imported from prisma.config.ts"),
            "prisma_config_dependency_missing",
        )
        self.assertEqual(
            smoke.app_bootstrap_failure_code("STACK03_APP_BOOTSTRAP=PRISMA_CONFIG_DEPENDENCY_MISSING"),
            "prisma_config_dependency_missing",
        )
        self.assertEqual(
            smoke.app_bootstrap_failure_code("Failed to load config file prisma.config.ts"),
            "prisma_config_load_failed",
        )
        self.assertEqual(smoke.app_bootstrap_failure_code("", 41), "migration_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("", 42), "fixture_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("STACK03_APP_BOOTSTRAP=MIGRATION_FAILED"), "migration_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("STACK03_APP_BOOTSTRAP=FIXTURE_FAILED"), "fixture_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("pnpm local-demonstration failed"), "fixture_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("STACK03_APP_MIGRATION_DONE"), "fixture_failed")
        self.assertEqual(smoke.app_bootstrap_failure_code("sh: pnpm: command not found"), "runner_command_unavailable")
        self.assertEqual(smoke.app_bootstrap_failure_code("unrecognized failure"), "unspecified_app_bootstrap_failed")

    def test_prisma_migration_diagnostic_keeps_only_category_and_known_migration(self):
        migration = "20261013000000_correct_model_inference_snapshot_guard"
        self.assertEqual(
            smoke.prisma_migration_diagnostic(f"private password P3006 Migration `{migration}` failed"),
            {"prisma_category": "P3006", "migration": migration},
        )
        self.assertEqual(smoke.prisma_migration_diagnostic("private password only"), {"prisma_category": "UNKNOWN"})

    def test_migration_diagnostic_uses_one_captured_bootstrap_process_and_redacts_report(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"PATH": "/usr/bin"}
            result = Mock(returncode=1, stdout="P3006", stderr="Migration `20261013000000_correct_model_inference_snapshot_guard` private")
            with patch.object(smoke.subprocess, "run", return_value=result) as run:
                with self.assertRaisesRegex(smoke.Failure, "migration_prisma_p3006"):
                    harness.diagnose_app_migration()
            run.assert_called_once()
            self.assertEqual(harness.report["migration_diagnostic"], {
                "prisma_category": "P3006", "migration": "20261013000000_correct_model_inference_snapshot_guard",
            })
            self.assertNotIn("private", json.dumps(harness.report))

    def test_migration_diagnostic_reuses_the_service_command_without_override(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"PATH": "/usr/bin"}
            result = Mock(returncode=0, stdout="", stderr="")
            with patch.object(smoke.subprocess, "run", return_value=result) as run:
                harness.diagnose_app_migration()
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ["--rm", "--no-deps", "app-bootstrap"])
            self.assertNotIn("prisma.config.ts", command)

    def test_model_sdk_failure_code_uses_only_bounded_markers(self):
        module = Mock()
        module.reader_diagnostic.return_value = {
            "stage": "bootstrap_experiment", "exception": "RestException", "http_status": 409, "source_line": 180,
        }
        self.assertEqual(
            smoke.model_sdk_failure_code(module, "bootstrap", "private child output"),
            "model_sdk_bootstrap_bootstrap_experiment_RestException_409_line180",
        )
        module.reader_diagnostic.return_value = None
        module.sdk_progress.return_value = "configure"
        self.assertEqual(
            smoke.model_sdk_failure_code(module, "bootstrap", "ConnectionError: private details"),
            "model_sdk_bootstrap_configure_connection_error_failed",
        )

    def test_model_sdk_targets_match_stack02_container_ownership(self):
        self.assertEqual(smoke.model_sdk_target("bootstrap"), ("mlflow", "http://127.0.0.1:5000"))
        self.assertEqual(smoke.model_sdk_target("fixture"), ("model-api", "http://mlflow:5000"))
        with self.assertRaisesRegex(smoke.Failure, "invalid_model_sdk_action"):
            smoke.model_sdk_target("unknown")

    def test_authenticated_invoice_context_uses_the_profile_membership_and_user_id(self):
        context = smoke.authenticated_invoice_context({
            "id": "user-id", "memberships": [{"organization": {"id": "organization-id"}}],
        })
        self.assertEqual(context, {"organizationId": "organization-id", "ownerType": "user", "ownerId": "user-id"})
        with self.assertRaisesRegex(smoke.Failure, "profile_context_missing"):
            smoke.authenticated_invoice_context({"id": "user-id", "memberships": []})

    def test_model_sdk_wrapper_emits_only_exception_class_marker(self):
        source = smoke.wrapped_model_sdk("raise ValueError('private details')")
        self.assertIn("STACK03_EXCEPTION_TYPE=", source)
        self.assertNotIn("private details", source.split("STACK03_EXCEPTION_TYPE=")[1])

    def test_diagnostic_preflight_discards_child_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = smoke.safe_environment(Path(directory), {})
            with patch.object(smoke.subprocess, "run", side_effect=[Mock(returncode=0), Mock(returncode=125)]) as run:
                with self.assertRaisesRegex(smoke.Failure, "compose_config_exit_125_output_suppressed"):
                    harness.diagnostic_preflight()
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertIs(call.kwargs["stdout"], smoke.subprocess.DEVNULL)
                self.assertIs(call.kwargs["stderr"], smoke.subprocess.DEVNULL)

    def test_isolated_docker_config_disables_ambient_credential_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = smoke.safe_environment(Path(directory), {})
            config = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(environment["HOME"], directory)
        self.assertEqual(environment["DOCKER_CONFIG"], directory)
        self.assertEqual(config["auths"], {"https://index.docker.io/v1/": {}})
        self.assertEqual(config["credsStore"], "")
        self.assertEqual(config["credHelpers"], {})
        if "cliPluginsExtraDirs" in config:
            self.assertEqual(config["cliPluginsExtraDirs"], [
                "/Applications/Docker.app/Contents/Resources/cli-plugins"
            ])

    def test_harness_never_contains_global_cleanup_and_deletes_only_owned_volumes(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"prune"', source)
        self.assertNotIn('"down"', source)
        self.assertIn('("volume", "rm")', source)

    def test_bootstrap_is_stack_owned_and_uses_only_the_demonstration_runner(self):
        overlay = (SCRIPT.parents[1] / "compose.invoiceops-app.yml").read_text(encoding="utf-8")
        self.assertIn("app-bootstrap:", overlay)
        self.assertIn("profiles: [\"stack03-bootstrap\"]", overlay)
        self.assertIn("./node_modules/.bin/prisma migrate deploy", overlay)
        self.assertIn("./node_modules/.bin/tsx src/local-demonstration.ts", overlay)
        self.assertIn("NODE_ENV: development", overlay)
        self.assertIn("STACK_APP_POSTGRES_PASSWORD", overlay)
        self.assertIn("STACK03_APP_BOOTSTRAP=MIGRATION_FAILED", overlay)
        self.assertIn("STACK03_APP_BOOTSTRAP=FIXTURE_FAILED", overlay)
        self.assertIn("stack03-prisma-config-$$.mjs", overlay)
        self.assertIn('import { defineConfig, env } from "prisma/config"', overlay)
        self.assertIn('migrations: { path: "prisma/migrations" }', overlay)
        self.assertEqual(overlay.count("$$config"), 3)
        self.assertNotIn("--config prisma.config.ts", overlay)

    def test_bootstrap_uses_the_owned_compose_command_without_an_override(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = smoke.Harness("stack03-proof-unit123", 18100, 18101, self.report(directory))
            harness.env = {"MODEL_API_ADMIN_TOKEN": "synthetic"}
            harness.owned_ids = {
                "containers": {"stack03-proof-unit123-db-1": "db-id"},
                "networks": {"stack03-proof-unit123_default": "network-id"},
                "volumes": {}, "images": {},
            }
            with patch.object(harness, "model_sdk", side_effect=[
                     {"experiment": "owned", "reader_nonadmin": True, "exact_read_role": True},
                     {"run_id": "run", "version": "1"},
                 ]), \
                 patch.object(harness, "model_http", return_value=(200, {"model_id": "invoice-review", "run_id": "run", "model_version": "1"})), \
                 patch.object(harness, "verify_owned", side_effect=[harness.owned_ids, harness.owned_ids]), \
                 patch.object(harness, "compose") as compose:
                harness.bootstrap_model_and_app_database()
            self.assertEqual(compose.call_args_list[-1], (("run", "--rm", "--no-deps", "app-bootstrap"), {"timeout": 180}))

if __name__ == "__main__":
    unittest.main()
