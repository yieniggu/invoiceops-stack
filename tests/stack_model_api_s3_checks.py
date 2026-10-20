"""Explicit offline gate for an already-installed boto3/botocore runtime.

Input is JSON containing the actual embedded SDK source. No dependencies are
installed. Each check runs the real source in a fresh Python subprocess, replacing
only external S3 transport and forbidding socket connections. This separate gate
does not make the host's stdlib test suite require boto3 or a daemon.
"""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from importlib.metadata import version

import boto3

SOURCE = None
RUN_ID = "a" * 32
FILES = {
    name: ("synthetic-" + name).encode()
    for name in (
        "MLmodel",
        "model.skops",
        "conda.yaml",
        "python_env.yaml",
        "requirements.txt",
    )
}
MANIFEST = {
    name: hashlib.sha256(payload).hexdigest() for name, payload in FILES.items()
}

# The subprocess executes SOURCE directly after installing these I/O boundaries;
# none of the action, hashing, exception hook or receipt code is replaced.
BOUNDARY = r"""
print('S3_TEST_PROGRESS=boundary_imports', flush=True)
import builtins, io, os, socket, sys
from botocore.client import BaseClient
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.response import StreamingBody
mode = os.environ['STACK02_S3_TEST_MODE']
prefix = 'workspaces/default/1/' + 'a' * 32 + '/artifacts/model/'
files = {name: ('synthetic-' + name).encode() for name in
         ('MLmodel','model.skops','conda.yaml','python_env.yaml','requirements.txt')}
def transport(client, operation, params):
    assert params['Bucket'] == 'mlflow-artifacts'
    if operation == 'ListObjectsV2':
        if mode == 'list_timeout':
            raise ReadTimeoutError(endpoint_url='http://private-secret', error='private-message')
        assert params == {'Bucket':'mlflow-artifacts','Prefix':prefix,'MaxKeys':64}
        return {'IsTruncated':False,'Contents':[{'Key':prefix+name} for name in files]}
    assert operation == 'GetObject' and params['Key'].startswith(prefix)
    if mode == 'get_denied':
        raise ClientError({'Error':{'Code':'AccessDenied','Message':'private-message'},
                           'ResponseMetadata':{'HTTPStatusCode':403,'RequestId':'private-id'}},'GetObject')
    payload = files[params['Key'][len(prefix):]]
    return {'Body':StreamingBody(io.BytesIO(payload),len(payload))}
original_read = StreamingBody.read
def read(body, *args, **kwargs):
    if mode == 'body_timeout':
        raise ReadTimeoutError(endpoint_url='http://private-secret', error='private-message')
    return original_read(body, *args, **kwargs)
def reject_network(*args, **kwargs):
    raise AssertionError('network_forbidden')
original_import = builtins.__import__
def imports(name, *args, **kwargs):
    if name == 'mlflow' or name.startswith('mlflow.'):
        raise AssertionError('objects_imported_mlflow')
    return original_import(name, *args, **kwargs)
BaseClient._make_api_call = transport
StreamingBody.read = read
socket.socket.connect = reject_network
builtins.__import__ = imports
sys.argv = ['sdk', 'objects']
print('S3_TEST_PROGRESS=boundary_ready', flush=True)
"""


class S3BoundaryTests(unittest.TestCase):
    def child(self, program, data, environment=None):
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", program],
                input=json.dumps(data),
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            allowed = {
                "S3_TEST_PROGRESS=boundary_imports",
                "S3_TEST_PROGRESS=boundary_ready",
                "STACK02_PROGRESS=process_start",
                "STACK02_PROGRESS=stdin_decode",
                "STACK02_PROGRESS=imports",
                "STACK02_PROGRESS=configure",
                "STACK02_PROGRESS=objects",
                "STACK02_PROGRESS=s3_client",
                "STACK02_PROGRESS=s3_list",
                "STACK02_PROGRESS=s3_get_headers",
                "STACK02_PROGRESS=s3_body_read",
                "STACK02_PROGRESS=s3_manifest",
            }
            stages = [line for line in output.splitlines() if line in allowed]
            self.fail(
                "S3 test child timeout; last constant stage: "
                + (stages[-1] if stages else "unknown")
            )
        self.assertEqual(result.stderr, "")
        self.assertNotIn("private-", result.stdout)
        return result

    @staticmethod
    def markers(result, marker):
        return [
            json.loads(line[len(marker) :])
            for line in result.stdout.splitlines()
            if line.startswith(marker)
        ]

    def objects(self, mode="success", corrupt=False):
        data = {
            "run_id": RUN_ID,
            "manifest": dict(MANIFEST),
            "artifact_uri": "mlflow-artifacts:/workspaces/default/1/"
            + RUN_ID
            + "/artifacts",
        }
        if corrupt:
            data["manifest"]["MLmodel"] = "0" * 64
        environment = dict(
            os.environ,
            STACK02_S3_TEST_MODE=mode,
            MLFLOW_S3_ENDPOINT_URL="http://s3-boundary.invalid",
            AWS_ACCESS_KEY_ID="synthetic",
            AWS_SECRET_ACCESS_KEY="synthetic",
            AWS_EC2_METADATA_DISABLED="true",
        )
        return self.child(BOUNDARY + "\n" + SOURCE, data, environment)

    def test_actual_botocore_exception_hook(self):
        prefix = SOURCE.split('stage = progress("imports")', 1)[0]
        cases = [
            (
                'ReadTimeoutError(endpoint_url="http://private-secret", error="private-message")',
                "ReadTimeoutError",
                0,
                "other",
            ),
            (
                'ConnectTimeoutError(endpoint_url="http://private-secret", error="private-message")',
                "ConnectTimeoutError",
                0,
                "other",
            ),
            (
                'EndpointConnectionError(endpoint_url="http://private-secret")',
                "EndpointConnectionError",
                0,
                "other",
            ),
            (
                'ClientError({"Error":{"Code":"AccessDenied","Message":"private-message"},"ResponseMetadata":{"HTTPStatusCode":403,"RequestId":"private-id"}},"GetObject")',
                "ClientError",
                403,
                "AccessDenied",
            ),
            (
                'ClientError({"Error":{"Code":"NoSuchKey"},"ResponseMetadata":{"HTTPStatusCode":404}},"GetObject")',
                "ClientError",
                404,
                "NoSuchKey",
            ),
            (
                'ClientError({"Error":{"Code":"private-code"},"ResponseMetadata":{"HTTPStatusCode":True}},"GetObject")',
                "ClientError",
                0,
                "other",
            ),
        ]
        for expression, kind, status, code in cases:
            with self.subTest(kind=kind, status=status, code=code):
                program = (
                    prefix
                    + '\nfrom botocore.exceptions import *\nstage="objects"\nraise '
                    + expression
                )
                result = self.child(program, {})
                self.assertNotEqual(result.returncode, 0)
                values = self.markers(result, "STACK02_DIAGNOSTIC=")
                self.assertEqual(len(values), 1)
                self.assertEqual(
                    (
                        values[0]["exception"],
                        values[0]["http_status"],
                        values[0].get("error_code"),
                    ),
                    (kind, status, code),
                )

    def test_actual_read_failures_keep_stage_and_fail_without_receipt(self):
        cases = [
            ("list_timeout", "s3_list", "ReadTimeoutError", 0),
            ("get_denied", "s3_get_headers", "ClientError", 403),
            ("body_timeout", "s3_body_read", "ReadTimeoutError", 0),
        ]
        for mode, stage, kind, status in cases:
            with self.subTest(mode=mode):
                result = self.objects(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.markers(result, "STACK02_RECEIPT="), [])
                values = self.markers(result, "STACK02_DIAGNOSTIC=")
                self.assertEqual(len(values), 1)
                self.assertEqual(
                    (
                        values[0]["stage"],
                        values[0]["exception"],
                        values[0]["http_status"],
                    ),
                    (stage, kind, status),
                )
                self.assertTrue(
                    1
                    <= values[0]["source_line"]
                    <= len((BOUNDARY + "\n" + SOURCE).splitlines())
                )

    def test_actual_s3_service_exception_normalizes_to_client_error(self):
        prefix = SOURCE.split('stage = progress("imports")', 1)[0]
        program = (
            prefix
            + r"""
import boto3, socket
def reject(*args, **kwargs):
    raise AssertionError('network_forbidden')
socket.socket.connect = reject
client = boto3.client('s3', endpoint_url='http://s3-boundary.invalid',
                       aws_access_key_id='synthetic', aws_secret_access_key='synthetic')
stage = 's3_get_headers'
raise client.exceptions.NoSuchKey({'Error':{'Code':'NoSuchKey','Message':'private-message'},
                                   'ResponseMetadata':{'HTTPStatusCode':404,'RequestId':'private-id'}},'GetObject')
"""
        )
        result = self.child(program, {})
        self.assertNotEqual(result.returncode, 0)
        values = self.markers(result, "STACK02_DIAGNOSTIC=")
        self.assertEqual(len(values), 1)
        self.assertEqual(
            (values[0]["exception"], values[0]["http_status"], values[0]["error_code"]),
            ("ClientError", 404, "NoSuchKey"),
        )

    def test_real_boto3_branch_without_mlflow_imports_requires_all_five_hashes(self):
        result = self.objects()
        self.assertEqual(result.returncode, 0)
        receipts = self.markers(result, "STACK02_RECEIPT=")
        self.assertEqual(len(receipts), 1)
        value = receipts[0]
        self.assertEqual(value["transport"], "direct_s3")
        self.assertEqual(value["mlflow_installed"], version("mlflow"))
        self.assertEqual(value["boto3"], boto3.__version__)
        self.assertNotIn("sdk", value)
        self.assertEqual(value["manifest"], MANIFEST)
        self.assertEqual(len(value["manifest"]), 5)
        corrupt = self.objects(corrupt=True)
        self.assertNotEqual(corrupt.returncode, 0)
        self.assertEqual(self.markers(corrupt, "STACK02_RECEIPT="), [])
        self.assertEqual(
            self.markers(corrupt, "STACK02_DIAGNOSTIC=")[0]["exception"],
            "AssertionError",
        )


if __name__ == "__main__":
    SOURCE = json.load(sys.stdin)["source"]
    unittest.main(verbosity=2)
