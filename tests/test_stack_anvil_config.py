"""STACK-04 AC-01/AC-04 source-level Compose contract; it never invokes Compose."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "compose.anvil.yml"
README = ROOT / "README.md"


class StackAnvilConfigTests(unittest.TestCase):
    def source(self):
        self.assertTrue(OVERLAY.is_file(), "STACK-04 Anvil Compose layer is absent")
        return OVERLAY.read_text(encoding="utf-8")

    def test_anvil_is_a_standalone_loopback_only_local_evm(self):
        source = self.source()
        self.assertIn("name: invoiceops-anvil", source)
        self.assertIn("services:\n  anvil:", source)
        self.assertIn("image: ghcr.io/foundry-rs/foundry:stable", source)
        self.assertIn("entrypoint: [\"anvil\"]", source)
        self.assertIn(
            'command: ["--quiet", "--host", "0.0.0.0", "--port", "8545", "--chain-id", "31337", "--hardfork", "cancun"]',
            source,
        )
        self.assertIn('"127.0.0.1:${STACK_ANVIL_PORT:-8545}:8545"', source)
        for forbidden in ("environment:", "secrets:", "volumes:", "container_name:", "network_mode:"):
            self.assertNotIn(forbidden, source)

    def test_healthcheck_proves_the_local_anvil_chain_identity(self):
        source = self.source()
        self.assertIn(
            'test: ["CMD-SHELL", "test \\"$(cast chain-id --rpc-url http://127.0.0.1:8545)\\" = \\"31337\\""]',
            source,
        )
        for setting in ("interval: 2s", "timeout: 2s", "retries: 15", "start_period: 2s"):
            self.assertIn(setting, source)

    def test_no_secrets_or_remote_configuration_are_declared(self):
        source = self.source()
        self.assertNotIn("environment:", source)
        self.assertEqual(source.count("${STACK_ANVIL_PORT:-8545}"), 1)
        self.assertNotIn("--fork-url", source)
        self.assertNotIn("http://", source.replace("http://127.0.0.1:8545", ""))
        self.assertNotIn("https://", source)

    def test_compose_default_name_is_explicitly_overridden_by_the_proof_project(self):
        self.assertIn("name: invoiceops-anvil", self.source())
        runbook = README.read_text(encoding="utf-8")
        self.assertIn("STACK04_PROJECT=stack04-proof-example01", runbook)
        self.assertIn('`-p "$STACK04_PROJECT"`', runbook)
        self.assertNotIn("COMPOSE_PROJECT_NAME=", runbook)

if __name__ == "__main__":
    unittest.main()
