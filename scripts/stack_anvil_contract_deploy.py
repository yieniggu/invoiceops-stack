#!/usr/bin/env python3
"""Plan or explicitly execute the Contracts local deployment through Stack Anvil only."""

import argparse
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT.parent / "invoiceops-contracts"
DEPLOY_SCRIPT = CONTRACTS / "scripts" / "deploy-local.sh"
LOCAL_RPC = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})\Z")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
ZERO_ADDRESS = "0x" + "0" * 40
STACK04_CHAIN_ID = "31337"
STACK04_PROFILE = "stack04"


class ConfigurationError(ValueError):
    """A deployment input does not satisfy the Stack local-only contract."""


def validate_local_inputs(rpc_url, deployer, signer):
    match = LOCAL_RPC.fullmatch(rpc_url)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ConfigurationError("RPC URL must be exactly http://127.0.0.1:<port>")
    for name, address in (("deployer", deployer), ("signer", signer)):
        if not ADDRESS.fullmatch(address) or address.lower() == ZERO_ADDRESS:
            raise ConfigurationError(f"{name} must be a nonzero public address")
    if not DEPLOY_SCRIPT.is_file():
        raise ConfigurationError("Contracts local deployment script is unavailable")


def deployment_command(rpc_url, deployer):
    return ["bash", str(DEPLOY_SCRIPT), "--rpc-url", rpc_url, "--deployer", deployer]


def execution_environment(signer):
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "EVIDENCE_ROOT_ANCHOR_SIGNER": signer,
        "FOUNDRY_PROFILE": STACK04_PROFILE,
    }


def read_chain_id(rpc_url, run):
    """Return the local RPC chain ID only when Cast reports the expected identity."""
    result = run(
        ["cast", "chain-id", "--rpc-url", rpc_url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != STACK04_CHAIN_ID:
        raise ConfigurationError("RPC chain ID must be 31337")
    return STACK04_CHAIN_ID


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", required=True, help="Stack Anvil loopback endpoint")
    parser.add_argument("--deployer", required=True, help="Unlocked public Anvil account")
    parser.add_argument("--signer", required=True, help="Public EvidenceRootAnchor signer")
    parser.add_argument("--execute", action="store_true", help="perform the local contract deployment")
    args = parser.parse_args(argv)

    try:
        validate_local_inputs(args.rpc_url, args.deployer, args.signer)
    except ConfigurationError as error:
        parser.error(str(error))

    if not args.execute:
        print("Local deployment plan validated; pass --execute to invoke Contracts.")
        return 0

    try:
        read_chain_id(args.rpc_url, subprocess.run)
    except ConfigurationError as error:
        parser.error(str(error))

    return subprocess.run(
        deployment_command(args.rpc_url, args.deployer),
        cwd=CONTRACTS,
        env=execution_environment(args.signer),
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
