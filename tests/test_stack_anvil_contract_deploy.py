"""Daemon-free STACK-04 AC-02 contract deployment wiring tests."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stack_anvil_contract_deploy.py"
SPEC = importlib.util.spec_from_file_location("stack_anvil_contract_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)

DEPLOYER = "0x1111111111111111111111111111111111111111"
SIGNER = "0x2222222222222222222222222222222222222222"


class StackAnvilContractDeployTests(unittest.TestCase):
    def test_missing_endpoint_or_address_is_rejected_before_any_execution(self):
        with patch.object(deploy.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                deploy.main(["--deployer", DEPLOYER, "--signer", SIGNER])
            with self.assertRaises(SystemExit):
                deploy.main(
                    ["--rpc-url", "http://127.0.0.1:8545", "--signer", SIGNER]
                )
            with self.assertRaises(SystemExit):
                deploy.main(
                    ["--rpc-url", "http://127.0.0.1:8545", "--deployer", DEPLOYER]
                )
        run.assert_not_called()

    def test_invalid_signers_are_rejected_before_any_execution(self):
        with patch.object(deploy.subprocess, "run") as run:
            for signer in ("", "0x" + "0" * 40, "not-an-address"):
                with self.subTest(signer=signer):
                    with self.assertRaises(SystemExit):
                        deploy.main(
                            [
                                "--rpc-url",
                                "http://127.0.0.1:8545",
                                "--deployer",
                                DEPLOYER,
                                "--signer",
                                signer,
                                "--execute",
                            ]
                        )
        run.assert_not_called()

    def test_missing_deploy_script_is_rejected_before_any_execution(self):
        with patch.object(deploy, "DEPLOY_SCRIPT", ROOT / "missing-deploy-local.sh"):
            with patch.object(deploy.subprocess, "run") as run:
                with self.assertRaises(SystemExit):
                    deploy.main(
                        [
                            "--rpc-url",
                            "http://127.0.0.1:8545",
                            "--deployer",
                            DEPLOYER,
                            "--signer",
                            SIGNER,
                            "--execute",
                        ]
                    )
        run.assert_not_called()

    def test_only_an_explicit_loopback_rpc_and_public_addresses_are_accepted(self):
        deploy.validate_local_inputs("http://127.0.0.1:8545", DEPLOYER, SIGNER)
        for url in (
            "https://rpc.chiadochain.net",
            "http://localhost:8545",
            "http://127.0.0.1:8545/path",
            "http://127.0.0.1:0",
        ):
            with self.subTest(url=url):
                with self.assertRaises(deploy.ConfigurationError):
                    deploy.validate_local_inputs(url, DEPLOYER, SIGNER)
        for address in ("", "0x" + "0" * 40, "not-an-address"):
            with self.subTest(address=address):
                with self.assertRaises(deploy.ConfigurationError):
                    deploy.validate_local_inputs("http://127.0.0.1:8545", address, SIGNER)

    def test_plan_mode_never_invokes_the_contracts_deployment_script(self):
        with patch.object(deploy.subprocess, "run") as run:
            result = deploy.main(
                [
                    "--rpc-url",
                    "http://127.0.0.1:18545",
                    "--deployer",
                    DEPLOYER,
                    "--signer",
                    SIGNER,
                ]
            )
        self.assertEqual(result, 0)
        run.assert_not_called()

    def test_execute_mode_rejects_an_invalid_chain_id_before_deployment(self):
        with patch.object(deploy.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "31338\n"
            with self.assertRaises(SystemExit):
                deploy.main(
                    [
                        "--rpc-url",
                        "http://127.0.0.1:8545",
                        "--deployer",
                        DEPLOYER,
                        "--signer",
                        SIGNER,
                        "--execute",
                    ]
                )
        run.assert_called_once_with(
            ["cast", "chain-id", "--rpc-url", "http://127.0.0.1:8545"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_execute_mode_fails_closed_for_malformed_chain_id_without_delegating(self):
        with patch.object(deploy.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "not-a-chain-id\n"
            with self.assertRaises(SystemExit):
                deploy.main(
                    [
                        "--rpc-url",
                        "http://127.0.0.1:8545",
                        "--deployer",
                        DEPLOYER,
                        "--signer",
                        SIGNER,
                        "--execute",
                    ]
                )
        run.assert_called_once_with(
            ["cast", "chain-id", "--rpc-url", "http://127.0.0.1:8545"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_execute_mode_rejects_an_invalid_chain_id_query_before_deployment(self):
        with patch.object(deploy.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            with self.assertRaises(SystemExit):
                deploy.main(
                    [
                        "--rpc-url",
                        "http://127.0.0.1:8545",
                        "--deployer",
                        DEPLOYER,
                        "--signer",
                        SIGNER,
                        "--execute",
                    ]
                )
        run.assert_called_once()

    def test_execution_delegates_only_to_the_existing_local_contracts_interface(self):
        with patch.object(deploy.subprocess, "run") as run:
            run.side_effect = [
                type("Result", (), {"returncode": 0, "stdout": "31337\n"})(),
                type("Result", (), {"returncode": 17})(),
            ]
            result = deploy.main(
                [
                    "--rpc-url",
                    "http://127.0.0.1:8545",
                    "--deployer",
                    DEPLOYER,
                    "--signer",
                    SIGNER,
                    "--execute",
                ]
            )
        self.assertEqual(result, 17)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args,
            (["cast", "chain-id", "--rpc-url", "http://127.0.0.1:8545"],),
        )
        self.assertEqual(
            run.call_args_list[0].kwargs,
            {"capture_output": True, "text": True, "check": False},
        )
        run.assert_called_with(
            [
                "bash",
                str(deploy.DEPLOY_SCRIPT),
                "--rpc-url",
                "http://127.0.0.1:8545",
                "--deployer",
                DEPLOYER,
            ],
            cwd=deploy.CONTRACTS,
            env={
                "PATH": deploy.os.environ.get("PATH", deploy.os.defpath),
                "EVIDENCE_ROOT_ANCHOR_SIGNER": SIGNER,
                "FOUNDRY_PROFILE": "stack04",
            },
            check=False,
        )

    def test_wiring_contains_no_remote_target_or_secret_input(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("https://", source)
        self.assertNotIn("PRIVATE_KEY", source)
        self.assertNotIn("env-file", source)
        self.assertNotIn("dotenv", source)
        self.assertIn("http://127\\.0\\.0\\.1", source)


if __name__ == "__main__":
    unittest.main()
