"""The MLOPS-07 runbook (section 3.4) lists every migration the app ships after the first three.

The expected list is derived from `invoiceops-app/apps/backend/prisma/migrations`, so a new
migration without its runbook row (or a wrong count) fails here. Skipped when the app checkout or
`dev/tickets` (a plain folder, not a repository) is missing.
"""

import re
import unittest

from support import TICKETS, V2

MIGRATIONS = V2 / "invoiceops-app" / "apps" / "backend" / "prisma" / "migrations"
RUNBOOK = TICKETS / "MLOPS-07_plan.md"
# Already applied in production before the unified stack: the runbook names them in prose.
ALREADY_APPLIED = [
    "20261026000000_initial_recovery_p0",
    "20261028000000_user_status_activity",
    "20261029000000_invoice_events",
]
NAME = re.compile(r"^\d{14}_\w+$")


class MigrationRunbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MIGRATIONS.is_dir() or not RUNBOOK.is_file():
            raise unittest.SkipTest("app migrations or dev/tickets/MLOPS-07_plan.md not present")
        cls.on_disk = sorted(p.name for p in MIGRATIONS.iterdir() if p.is_dir() and NAME.match(p.name))
        text = RUNBOOK.read_text(encoding="utf-8")
        cls.section = text.split("**3.4 Baje el proyecto actual", 1)[1].split("Backend y web", 1)[0]

    def table_rows(self):
        return re.findall(r"^\| `(\d{14}_\w+)` \|", self.section, re.MULTILINE)

    def test_the_already_applied_migrations_are_the_first_ones_on_disk(self):
        self.assertEqual(self.on_disk[: len(ALREADY_APPLIED)], ALREADY_APPLIED)

    def test_the_table_lists_exactly_the_newer_migrations_in_order(self):
        self.assertEqual(self.table_rows(), self.on_disk[len(ALREADY_APPLIED) :])

    def test_the_counts_in_the_text_match_the_directory(self):
        expected = f"las {len(self.on_disk)} migraciones (las {len(ALREADY_APPLIED)} anteriores y las {len(self.table_rows())} de la tabla)"
        self.assertIn(expected, self.section.replace("\n", " "))

    def test_the_cross_org_integrity_migration_is_explained(self):
        row = next(line for line in self.section.splitlines() if "20261127000000_prediction_integrity_and_audit" in line)
        self.assertIn("integridad referencial entre organizaciones", row)


if __name__ == "__main__":
    unittest.main()
