"""Safety of scripts/local_e2e_smoke.py: which compose project it can ever touch.

No container is started: `docker compose config` only, and the stack / docker calls are stubbed.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from support import LOCAL_ENV, ROOT, V2, RequiresStackPrerequisites, parse_env

spec = importlib.util.spec_from_file_location("local_e2e_smoke", ROOT / "scripts" / "local_e2e_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

HOSTILE = {
    "COMPOSE_PROJECT_NAME": "invoiceops",
    "COMPOSE_FILE": "/etc/does-not-exist.yml",
    "COMPOSE_PROFILES": "ops,tools",
}


def e2e_env(project="invoiceops-e2e"):
    return {**parse_env(LOCAL_ENV), "LOCAL_PROJECT": project}


class ProjectNameTests(unittest.TestCase):
    def test_only_names_starting_with_invoiceops_e2e_are_accepted(self):
        for ok in ("invoiceops-e2e", "invoiceops-e2e-2", "invoiceops-e2e-pr42"):
            self.assertEqual(smoke.validate_project(ok), ok)
        for bad in ("invoiceops", "invoiceops-local", "invoiceops-dev", "e2e", "my-invoiceops-e2e", ""):
            with self.assertRaises(smoke.SmokeError, msg=bad):
                smoke.validate_project(bad)

    def test_the_stack_refuses_a_project_it_may_not_own(self):
        with self.assertRaises(smoke.SmokeError):
            smoke.Stack(Path("/dev/null"), e2e_env("invoiceops"))


class ProjectIsolationFromTheShellTests(RequiresStackPrerequisites):
    def effective_project(self, command, env):
        import subprocess

        result = subprocess.run([*command, "config", "--format", "json"], cwd=ROOT, env=env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["name"]

    def test_exported_compose_variables_cannot_change_the_effective_project(self):
        with mock.patch.dict(os.environ, HOSTILE):
            stack = smoke.Stack(LOCAL_ENV, e2e_env())
        for name in HOSTILE:
            self.assertNotIn(name, stack.process_env)
        self.assertEqual(stack.base[2:4], ["-p", "invoiceops-e2e"])
        self.assertEqual(self.effective_project(stack.base, stack.process_env), "invoiceops-e2e")

    def test_control_without_the_dash_p_and_the_stripping_the_shell_variable_wins(self):
        """Why the fix exists: with the old command the exported variable chose the project."""
        stack = smoke.Stack(LOCAL_ENV, e2e_env())
        old_command = [part for index, part in enumerate(stack.base) if index not in (2, 3)]
        env = {**stack.process_env, "COMPOSE_PROJECT_NAME": "invoiceops"}
        self.assertEqual(self.effective_project(old_command, env), "invoiceops")


class VersionPickTests(unittest.TestCase):
    VERSIONS = [{"version": "1"}, {"version": "2"}, {"version": "3"}]

    def test_the_highest_val_auc_wins_over_the_highest_number(self):
        picked = smoke.pick_version(self.VERSIONS, {"1": "0.91", "2": "0.80", "3": "0.85"})
        self.assertEqual(picked["version"], "1")

    def test_ties_and_missing_or_non_finite_tags_fall_back_to_the_highest_number(self):
        self.assertEqual(smoke.pick_version(self.VERSIONS, {"1": "0.9", "2": "0.9", "3": "0.9"})["version"], "3")
        self.assertEqual(smoke.pick_version(self.VERSIONS, {"1": "0.5"})["version"], "1")
        self.assertEqual(smoke.pick_version(self.VERSIONS, {"1": "nan", "2": "x", "3": None})["version"], "3")

    def test_the_val_auc_tags_are_read_from_mlflow_with_the_bearer_token(self):
        class Fake:
            def request(self, method, url, body=None, headers=None, **_):
                self.seen = (method, url, headers)
                payload = {"model_versions": [{"version": 2, "tags": [{"key": "val_auc", "value": "0.8"}]}, {"version": 3}]}
                return 200, json.dumps(payload), url

        fake = Fake()
        aucs = smoke.version_val_aucs(fake, "http://127.0.0.1:1", "iop_x")
        self.assertEqual(aucs, {"2": "0.8", "3": None})
        self.assertEqual(fake.seen[2], {"Authorization": "Bearer iop_x"})
        self.assertIn("model-versions/search", fake.seen[1])

    def test_the_smoke_no_longer_picks_the_highest_number_nor_has_a_vacuous_check(self):
        source = (ROOT / "scripts" / "local_e2e_smoke.py").read_text(encoding="utf-8")
        self.assertNotIn('max(registered["versions"]', source)
        self.assertNotIn("check(True", source)

    def test_the_guide_does_not_say_version_n(self):
        guide = (ROOT / "LOCAL_E2E.md").read_text(encoding="utf-8")
        self.assertNotRegex(guide, r"versión `N`")
        self.assertIn("la versión que verificó el notebook 05 (mayor `val_auc`)", guide)

    def test_the_guide_runs_notebook_05_and_says_where_the_val_auc_tag_is_read(self):
        """CR-702: 05_deploy is run in section 5; section 7 deploys; the tag is readable in the MLflow UI."""
        guide = (ROOT / "LOCAL_E2E.md").read_text(encoding="utf-8")
        self.assertIn("## 5. Estudiante: notebooks 00–05", guide)
        self.assertIn("| `05_deploy` |", guide)
        self.assertIn("pestaña de tags", guide)
        self.assertNotIn("esta guía lo hace en el paso 7", guide)
        self.assertNotRegex(guide, r"notebooks 00–04")

    def test_mlops_07_8_4_does_not_say_register_a_model(self):
        plan = (V2 / "dev" / "tickets" / "MLOPS-07_plan.md")
        if not plan.is_file():
            self.skipTest("dev/tickets is not next to the repositories")
        document = plan.read_text(encoding="utf-8")
        start = document.index("**8.4 Prueba de interfaz")
        section = document[start : start + 1500]
        self.assertNotIn("registre un modelo", section)
        self.assertIn("registra todas las versiones que pasan el gate", section)
        self.assertIn("notebooks 00→05", section)


class PredictionRunTests(unittest.TestCase):
    def test_a_matching_non_null_run_passes(self):
        self.assertTrue(smoke.prediction_run_matches({"runId": "abc"}, {"runId": "abc"}))

    def test_a_different_run_fails(self):
        self.assertFalse(smoke.prediction_run_matches({"runId": "abc"}, {"runId": "def"}))

    def test_a_null_run_on_either_side_fails(self):
        self.assertFalse(smoke.prediction_run_matches({"runId": None}, {"runId": None}))
        self.assertFalse(smoke.prediction_run_matches({"runId": "abc"}, {"runId": None}))
        self.assertFalse(smoke.prediction_run_matches({"runId": None}, {"runId": "abc"}))
        self.assertFalse(smoke.prediction_run_matches({}, {}))


class PriorInvoicesQueryTests(unittest.TestCase):
    def test_the_query_replays_the_worker_lookup(self):
        sql = smoke.prior_invoices_sql("11111111-2222-3333-4444-555555555555")
        for fragment in ('i."supplierKey" is not null', 'p."supplierKey" = i."supplierKey"',
                         'p."scopeKey" = i."scopeKey"', 'p."deletedAt" is null',
                         'p."createdAt" < i."createdAt"'):
            self.assertIn(fragment, sql)

    def test_it_refuses_an_id_that_could_inject_sql(self):
        with self.assertRaises(smoke.SmokeError):
            smoke.prior_invoices_sql("x'; drop table \"Invoice\"; --")


class DockerTargetTests(unittest.TestCase):
    def test_the_context_is_printed_at_start(self):
        lines = []
        smoke.check_docker_target("desktop-linux", None, False, lines.append)
        self.assertEqual(lines, ["docker context: desktop-linux"])

    def test_refusal_names_every_problem(self):
        with self.assertRaises(smoke.SmokeError) as raised:
            smoke.check_docker_target("staging", "tcp://host:2376", False)
        self.assertIn("DOCKER_HOST", str(raised.exception))
        self.assertIn("'staging'", str(raised.exception))

    def test_the_stale_release_tag_comment_is_gone(self):
        self.assertNotIn("v0.1.0", (ROOT / "scripts" / "local_e2e_smoke.py").read_text(encoding="utf-8"))


class AbortOnExistingResourcesTests(unittest.TestCase):
    def run_main(self, argv, leftovers, context="default", docker_host=None):
        calls = {"down": 0, "flow": 0}

        def fake_down(_self):
            calls["down"] += 1

        def fake_flow(*_args):
            calls["flow"] += 1

        leftover_values = iter(leftovers)
        with (
            mock.patch.object(smoke.Stack, "leftovers", lambda _self: next(leftover_values, [])),
            mock.patch.object(smoke.Stack, "down", fake_down),
            mock.patch.object(smoke, "flow", fake_flow),
            mock.patch.object(smoke, "port_is_free", lambda _port: True),
            mock.patch.object(smoke.shutil, "which", lambda _tool: "/usr/bin/docker"),
            mock.patch.object(smoke, "docker_context_name", lambda *_args: context),
            mock.patch.dict(os.environ, {"DOCKER_HOST": docker_host} if docker_host else {}, clear=False),
        ):
            if not docker_host:
                os.environ.pop("DOCKER_HOST", None)
            code = smoke.main(argv)
        return code, calls

    def test_a_remote_context_or_host_stops_the_run_before_anything_starts(self):
        for context, host in (("prod-server", None), ("default", "ssh://deploy@example.com"), ("default", "tcp://10.0.0.5:2375")):
            with self.subTest(context=context, host=host):
                with self.assertRaises(SystemExit) as stopped:
                    self.run_main([], leftovers=[], context=context, docker_host=host)
                self.assertIn("--allow-remote-docker", str(stopped.exception))

    def test_allow_remote_docker_lets_a_remote_target_through(self):
        code, calls = self.run_main(["--allow-remote-docker"], leftovers=[[], []], context="prod-server")
        self.assertEqual(code, 0)
        self.assertEqual(calls["flow"], 1)

    def test_local_contexts_and_a_unix_socket_host_are_accepted(self):
        for context, host in (("default", None), ("desktop-linux", None), ("default", "unix:///var/run/docker.sock")):
            with self.subTest(context=context, host=host):
                code, _ = self.run_main([], leftovers=[[], []], context=context, docker_host=host)
                self.assertEqual(code, 0)

    def test_existing_resources_abort_without_down_v(self):
        code, calls = self.run_main([], leftovers=[["invoiceops-e2e_postgres_data"]])
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, {"down": 0, "flow": 0})

    def test_reset_removes_them_explicitly_and_then_runs(self):
        code, calls = self.run_main(["--reset"], leftovers=[["invoiceops-e2e_postgres_data"], [], []])
        self.assertEqual(code, 0)
        self.assertEqual(calls["flow"], 1)
        self.assertEqual(calls["down"], 2)  # the explicit reset and the final cleanup

    def test_an_empty_project_runs_and_cleans_up_only_what_it_created(self):
        code, calls = self.run_main([], leftovers=[[], []])
        self.assertEqual(code, 0)
        self.assertEqual(calls, {"down": 1, "flow": 1})

    def test_a_project_name_outside_the_prefix_is_rejected_before_anything_runs(self):
        for name in ("invoiceops", "invoiceops-local", "other"):
            with self.assertRaises(SystemExit):
                self.run_main(["--project", name], leftovers=[])


if __name__ == "__main__":
    unittest.main()
