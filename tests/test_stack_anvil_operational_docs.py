"""Daemon-free checks for the STACK-04 AC-05 operational runbook."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SECTION = "## STACK-04 AC-05 — operación futura de Anvil y despliegue local"


class StackAnvilOperationalDocsTests(unittest.TestCase):
    def section(self):
        source = README.read_text(encoding="utf-8")
        self.assertIn(SECTION, source)
        return source.split(SECTION, 1)[1]

    def test_runbook_preserves_and_supersedes_the_historical_unexecuted_smoke(self):
        source = self.section()
        for expected in (
            "**Estado histórico\ndel slice estático (sustituido):** el smoke real **no fue ejecutado**",
            "**Estado posterior vigente:** el\nsmoke local posterior quedó **CLOSED / COMPLETED_LOCAL_VALIDATED**",
            "sustituye aquel estado histórico de no ejecución",
            "El cierre no acredita CI, remoto, publicación o despliegue fuera del\nhost local.",
            "slice; no se inició Anvil, no se consultó RPC, no se desplegó contrato",
            "no acredita disponibilidad del daemon",
            "Tampoco autoriza red,\ninstalaciones, migraciones, broadcasts, despliegues remotos o cambios de secretos.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_runbook_forces_one_nonsecret_proof_project_for_preflight_execution_and_cleanup(self):
        source = self.section()
        invocation = 'docker --host unix:///var/run/docker.sock compose -p "$STACK04_PROJECT"'
        self.assertIn("STACK04_PROJECT=stack04-proof-example01", source)
        self.assertIn("`-p \"$STACK04_PROJECT\"`", source)
        self.assertIn("La bandera tiene prioridad sobre `name:`", source)
        self.assertNotIn("COMPOSE_PROJECT_NAME=", source)
        self.assertEqual(source.count(invocation), 3)
        for action in (
            "ps --all",
            "up -d",
            "down",
        ):
            with self.subTest(action=action):
                self.assertRegex(
                    source,
                    re.escape(invocation)
                    + r" \\\n\s+--env-file /dev/null -f ./compose\.anvil\.yml "
                    + re.escape(action),
                )

    def test_runbook_requires_independent_explicit_operational_authorization(self):
        source = self.section()
        for expected in (
            "Antes de una autorización operacional independiente",
            "Con autorización explícita y variables no secretas entregadas sólo al proceso\nque las necesite",
            "Sólo\n   después, y en la misma autorización, usar `--execute`",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_runbook_defines_complete_preflight_and_blocking_conditions(self):
        source = self.section()
        for expected in (
            "### Preflight manual",
            "Docker/Compose, Foundry (`cast`), Python 3",
            "repositorios\nhermanos `invoiceops-stack` e `invoiceops-contracts` en las rutas esperadas",
            "stack04-proof-...",
            "`http://127.0.0.1:<puerto>`",
            "`31337`",
            "las dos direcciones públicas no nulas que exige el wrapper",
            "No usar\n`.env`, `.env.*`, claves privadas, cuentas remotas ni endpoints distintos de",
            "Una colisión, un puerto ocupado,\nla falta de un binario o una identidad de cadena distinta de `31337` bloquean el\nprocedimiento",
            "no se cambia de proyecto, puerto, RPC ni contrato de forma\nsilenciosa.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_runbook_preserves_the_future_manual_order_and_cleanup_boundary(self):
        source = self.section()
        steps = (
            "1. Iniciar exclusivamente `compose.anvil.yml`",
            "2. Esperar la salud de Anvil",
            "3. Ejecutar primero el wrapper sin `--execute`",
            "4. Registrar el resultado operativo",
        )
        positions = [source.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        for expected in (
            "### Límites, cleanup y rollback",
            "propios son exclusivamente",
            "La imagen base de Foundry, cachés, recursos de otros\nproyectos y cualquier recurso sin esa etiqueta no pertenecen a este procedimiento",
            "el inventario\ndel proyecto exacto confirma que los recursos propios esperados fueron retirados",
            "Ante inventario incompleto, etiquetas\ninconsistentes, timeout, error de Compose o duda de propiedad, detenerse y\nconservar la evidencia para revisión",
            "no usar `prune`, borrado forzado, `-v`",
            "eliminación de imágenes compartidas ni limpieza por prefijos amplios.",
            "rollback operacional consiste en detener y retirar únicamente",
            "sin borrar volúmenes ni imágenes.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)


if __name__ == "__main__":
    unittest.main()
