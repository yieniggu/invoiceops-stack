"""Consistency between the docs, the tickets and the code they describe (iteration 3).

Ticket checks are skipped when `dev/tickets` is not next to the repositories (it is not a repository).
"""

import re
import tempfile
import unittest
from pathlib import Path

from support import ROOT, TICKETS, V2, RequiresStackPrerequisites, compose, in_ci, parse_env

README = ROOT / "README.md"
MLFLOW = V2 / "invoiceops-mlflow"
MATRIX = MLFLOW / "scripts" / "gateway-matrix.sh"
PLAN_07 = TICKETS / "MLOPS-07_plan.md"
PLAN_02 = TICKETS / "MLOPS-02_plan.md"
PLAN_06 = TICKETS / "MLOPS-06_plan.md"
SIBLING_NAMES = (
    "invoiceops-app",
    "invoiceops-mlflow",
    "invoiceops-model-api",
    "invoiceops-ml",
    "invoiceops-contracts",
)
MATRIX_VARIABLES = re.compile(r"\b(?:TOKEN|REVOKE)_[A-Z_]+\b")


def text(path):
    return path.read_text(encoding="utf-8")


def publish_order(document):
    """Repositories in the order the publish-order sentence lists them."""
    start = document.index("se publican en este orden") if "se publican en este orden" in document else document.index(
        "se suben a GitHub en este orden"
    )
    sentence = document[start : start + 900]
    found = sorted(
        (sentence.index(f"`{name}`"), name)
        for name in ("invoiceops-app", "invoiceops-mlflow", "invoiceops-ml", "invoiceops-model-api", "invoiceops-stack")
        if f"`{name}`" in sentence
    )
    return [name for _, name in found]


def matrix_section(document):
    start = document.index("**8.2 Matriz del gateway.**")
    return document[start : document.index("**8.3 ", start)]


class PublishOrderTests(unittest.TestCase):
    EXPECTED = ["invoiceops-app", "invoiceops-mlflow", "invoiceops-ml", "invoiceops-model-api", "invoiceops-stack"]

    def test_the_stack_readme_publishes_ml_before_model_api(self):
        self.assertEqual(publish_order(text(README)), self.EXPECTED)

    @unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
    def test_mlops_07_publishes_ml_before_model_api(self):
        self.assertEqual(publish_order(text(PLAN_07)), self.EXPECTED)

    @unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
    def test_both_documents_set_the_ml_ref_before_the_first_model_api_push(self):
        for document in (text(README), text(PLAN_07)):
            self.assertIn("INVOICEOPS_ML_REF=v0.2.0", document)
            self.assertRegex(document, r"antes\s+de\s+su\s+primer\s+push")

    @unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
    def test_mlops_07_says_the_ci_clones_five_siblings_and_names_them_all(self):
        """CR-701: the ticket must agree with the README and ci.yml (five siblings, contracts included)."""
        document = text(PLAN_07)
        start = document.index("El `ci.yml` del stack clona")
        sentence = document[start : start + 400]
        self.assertIn("los otros cinco", sentence)
        for name in SIBLING_NAMES:
            self.assertIn(f"`{name}`", sentence)
        self.assertNotRegex(document, r"(?i)los otros cuatro|los cuatro repositorios hermanos")
        self.assertIn("los cinco repositorios hermanos", document)


@unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
class GatewayMatrixDocTests(unittest.TestCase):
    def test_the_variables_named_in_8_2_are_the_ones_the_script_reads(self):
        in_script = set(MATRIX_VARIABLES.findall(text(MATRIX)))
        in_plan = set(MATRIX_VARIABLES.findall(matrix_section(text(PLAN_07))))
        self.assertEqual(in_plan, in_script)
        self.assertTrue({"TOKEN_A", "TOKEN_B", "TOKEN_A_READ", "TOKEN_REVOKE", "REVOKE_COMMAND"} <= in_plan)

    def test_every_expected_output_line_of_8_2_exists_in_the_script(self):
        script = text(MATRIX)
        section = matrix_section(text(PLAN_07))
        lines = re.findall(r"^ok   (?:\d{3}|<[\d|]+>) (.+)$", section, re.MULTILINE)
        self.assertGreaterEqual(len(lines), 20)
        expanded = [re.sub(r"^[AB]: ", "$who: ", line) for line in lines]
        missing = [line for line in expanded if f'"{line}"' not in script]
        self.assertEqual(missing, [])

    def test_8_2_has_no_manual_cleanup_step(self):
        self.assertNotIn("borre el experimento", matrix_section(text(PLAN_07)))


@unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
class TicketWordingTests(unittest.TestCase):
    def test_the_decided_at_rejection_is_scoped_to_prior_invoices(self):
        for path in (*TICKETS.glob("MLOPS-0*.md"), V2 / "invoiceops-ml" / "README.md"):
            self.assertNotRegex(text(path), r"cualquier\s+`decidedAt`", path.name)
        contract = text(V2 / "invoiceops-ml" / "src" / "invoiceops_ml" / "contract.py")
        self.assertNotRegex(contract, r"rejects any\s+``decidedAt``")

    def test_every_skew_bound_states_the_default_and_the_maximum(self):
        paths = [*TICKETS.glob("MLOPS-0*.md"), V2 / "invoiceops-ml" / "README.md"]
        seen = 0
        for path in paths:
            for hit in re.finditer(r"PREDICTION_MAX_JOB_AGE_MS", text(path)):
                seen += 1
                around = text(path)[max(0, hit.start() - 80) : hit.end() + 80]
                self.assertIn("por defecto", around, path.name)
                self.assertIn("30 días", around, path.name)
        self.assertGreaterEqual(seen, 4)

    def test_the_prior_status_skew_caveat_is_in_every_ticket_that_says_they_equal_created_at(self):
        """CR-678/CR-704: current prior statuses equal those at `createdAt` only approximately."""
        seen = 0
        for path in (*TICKETS.glob("MLOPS-0*.md"), V2 / "invoiceops-ml" / "README.md"):
            document = text(path)
            for hit in re.finditer(r"equival\w+ (?:al estado|a los) (?:en su |de )`createdAt`", document):
                seen += 1
                around = document[hit.start() : hit.end() + 420]
                self.assertIn("aproximad", around, path.name)
                self.assertIn("PREDICTION_MAX_JOB_AGE_MS", around, path.name)
                self.assertIn("24 h", around, path.name)
        self.assertGreaterEqual(seen, 2)
        self.assertIn("solo de forma aproximada", text(PLAN_06))

    def test_cross_organization_access_is_404_or_403_never_200(self):
        self.assertIn("404/403, nunca 200", text(PLAN_07))
        self.assertNotRegex(text(PLAN_07), r"acceso cruzado entre organizaciones devuelve 403;")

    def test_the_gateway_variables_are_documented_in_02_and_07(self):
        for path in (PLAN_02, PLAN_07):
            document = text(path)
            for name in ("GATEWAY_AUDIT_HMAC_KEY", "GATEWAY_HANDOFF_FAILURE_LIMIT", "GATEWAY_AUTH_FAILURE_LIMIT"):
                self.assertIn(name, document, f"{path.name}: {name}")
            self.assertIn("invoiceops-mlflow-gateway/audit-key/v1", document, path.name)
            self.assertNotIn("con el secreto de introspección), que correlaciona", document, path.name)

    def test_the_client_ip_header_doc_describes_unset_versus_empty(self):
        for path in (PLAN_02, PLAN_07):
            document = text(path)
            self.assertNotIn("vacío = IP del peer", document, path.name)
            self.assertIn("sin definir, el compose usa `cf-connecting-ip`", document, path.name)

    def test_the_model_api_deployment_list_is_get_deployments_and_health_is_liveness(self):
        document = text(TICKETS / "MLOPS-04_plan.md")
        self.assertIn("`GET /deployments`", document)
        self.assertNotRegex(document, r"`/health` lista")

    def test_the_url_acceptance_criterion_names_the_default_package_spec_exception(self):
        document = text(TICKETS / "MLOPS-03_plan.md")
        self.assertIn("`DEFAULT_PACKAGE_SPEC`", document)
        self.assertIn("notebooks 00 a 05", document)

    def test_the_token_migration_is_reversible_only_after_purging_mlflow_tokens(self):
        self.assertIn("solo después de purgar los tokens MLflow", text(TICKETS / "MLOPS-01_plan.md"))

    def test_the_backup_section_has_no_off_server_copy(self):
        """CR-698: backups stay on the server (accepted risk); §9 must not describe a remote copy."""
        document = text(PLAN_07)
        match = re.search(r"^### 9\..*?(?=^### |\Z)", document, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "MLOPS-07 has no section 9")
        section = match.group(0)
        self.assertIn("no hay respaldos fuera del servidor", section)
        self.assertNotRegex(section, r"(?i)\b(rsync|rclone)\b|destino remoto")


class OffServerBackupClaimTests(unittest.TestCase):
    """CR-703: no doc may present off-server backup copies as current practice (accepted risk in MLOPS-07 section 9)."""

    QUALIFIERS = ("no ", "riesgo aceptado", "opcional", "recomendaci", "sin copia", "sin respaldos")

    def docs(self):
        files = [*ROOT.glob("*.md"), *MLFLOW.glob("*.md")]
        if TICKETS.is_dir():
            files += [*TICKETS.glob("MLOPS-0*.md")]
        return files

    def test_every_mention_of_off_server_copies_is_a_negation_or_an_optional_recommendation(self):
        seen = 0
        for path in self.docs():
            document = text(path)
            for hit in re.finditer(r"fuera del servidor", document):
                seen += 1
                around = document[max(0, hit.start() - 400) : hit.end() + 300].lower()
                self.assertTrue(any(word in around for word in self.QUALIFIERS), f"{path.name}: {around!r}")
                self.assertNotRegex(document[max(0, hit.start() - 20) : hit.end()], r"gu[aá]rd\w+ fuera del servidor", path.name)
        self.assertGreaterEqual(seen, 1)

    def test_the_gateway_backup_section_is_optional_and_states_the_accepted_risk(self):
        document = text(MLFLOW / "GATEWAY.md")
        start = document.index("contienen datos de estudiantes")
        paragraph = document[start : start + 500]
        self.assertIn("riesgo aceptado", paragraph)
        self.assertIn("recomendación opcional", paragraph)


@unittest.skipUnless(TICKETS.is_dir(), "dev/tickets is not next to the repositories")
class ImplementedLedgerTests(unittest.TestCase):
    """CR-705: Implementado.md follows prompt.md (what was implemented, the next ticket, the percentages)."""

    def ledger(self):
        document = text(TICKETS / "Implementado.md")
        return document[: document.index("\n## CONTRACT-03")]

    def test_it_mentions_every_mlops_plan(self):
        document = self.ledger()
        for plan in sorted(TICKETS.glob("MLOPS-0[1-7]_plan.md")):
            self.assertIn(plan.name, document)
        self.assertIn("Siguiente ticket", document)
        self.assertIn("sin commitear", document)

    def test_no_superseded_ticket_is_left_pending(self):
        superseded = [path.name.removesuffix("_plan.md") for path in (TICKETS / "superseded").glob("*_plan.md")]
        self.assertGreaterEqual(len(superseded), 10)
        for paragraph in re.split(r"\n\s*\n", self.ledger()):
            for ticket in superseded:
                if re.search(rf"`{re.escape(ticket)}`", paragraph) or re.search(rf"`[A-Z]+-[0-9.]+`.*{re.escape(ticket)}\b", paragraph):
                    self.assertRegex(paragraph, r"(?i)reemplazad|supersed", f"{ticket}: {paragraph[:120]!r}")


class ReadmeTests(unittest.TestCase):
    def test_the_before_committing_checklist_names_the_local_test_command(self):
        document = text(README)
        self.assertIn("### Antes de commitear", document)
        section = document[document.index("### Antes de commitear") :].split("###", 2)[1]
        self.assertIn("python3 -m unittest discover -s tests", section)
        self.assertIn("CI", section)

    def test_the_ci_checks_out_the_contracts_repository_and_the_readme_says_five_siblings(self):
        document = text(README)
        self.assertIn("los otros cinco", document)
        self.assertIn("sobre los cinco hermanos", document)
        self.assertIn("path: invoiceops-contracts", text(ROOT / ".github" / "workflows" / "ci.yml"))

    def test_the_contracts_readme_points_to_an_existing_stack_04_heading(self):
        contracts_readme = V2 / "invoiceops-contracts" / "README.md"
        if not contracts_readme.is_file():
            message = "invoiceops-contracts is not next to the stack (ci.yml must check it out)"
            if in_ci():
                self.fail(message)
            self.skipTest(message)
        contracts = text(contracts_readme)
        self.assertNotIn("el guía", contracts)
        anchor = re.search(r"invoiceops-stack/README\.md#([^)\s]+)", contracts)
        self.assertIsNotNone(anchor)

        def slug(heading):
            return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")

        slugs = {slug(h) for h in re.findall(r"^#{1,6} (.+)$", text(README), re.MULTILINE)}
        self.assertIn(anchor.group(1), slugs)

    def test_the_mlflow_root_gateway_doc_points_to_the_variable_list(self):
        root = text(MLFLOW / "GATEWAY.md")
        self.assertIn("gateway/GATEWAY.md", root)
        self.assertTrue((MLFLOW / "gateway" / "GATEWAY.md").is_file())


class ClientIpHeaderComposeTests(RequiresStackPrerequisites):
    """Behaviour the docs describe: unset -> cf-connecting-ip; set but empty -> the TCP peer address.

    Compose needs `${GATEWAY_CLIENT_IP_HEADER-cf-connecting-ip}` (no colon) for the empty case.
    """

    def header(self, extra):
        from test_stack_config import DUMMY
        from support import SIBLINGS

        lines = {**DUMMY, **{k: str(v) for k, v in SIBLINGS.items()}, **extra}
        with tempfile.TemporaryDirectory(prefix="stack-ip-header-") as tmp:
            env = Path(tmp) / "production.env"
            env.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n", encoding="utf-8")
            assert parse_env(env)
            result = compose(ROOT, env, "config", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        import json

        return json.loads(result.stdout)["services"]["gateway"]["environment"]["GATEWAY_CLIENT_IP_HEADER"]

    def test_unset_defaults_to_the_cloudflare_header(self):
        self.assertEqual(self.header({}), "cf-connecting-ip")

    def test_set_but_empty_means_the_tcp_peer(self):
        self.assertEqual(self.header({"GATEWAY_CLIENT_IP_HEADER": ""}), "")

    def test_an_explicit_value_wins(self):
        self.assertEqual(self.header({"GATEWAY_CLIENT_IP_HEADER": "x-real-ip"}), "x-real-ip")


if __name__ == "__main__":
    unittest.main()
