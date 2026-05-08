"""Read-only API contract checks adapted from the legacy Solr NameResolution tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION_TESTS = os.getenv("NAMERES_RUN_INTEGRATION_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION_TESTS,
        reason="set NAMERES_RUN_INTEGRATION_TESTS=1 to run local Elasticsearch-backed API contract tests",
    ),
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(base_url: str, path: str, *, method: str = "GET", body=None, headers=None):
    url = f"{base_url}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    request_headers = dict(headers or {})
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def _request_json(base_url: str, path: str, *, method: str = "GET", body=None, headers=None):
    status, response_headers, response_body = _request(base_url, path, method=method, body=body, headers=headers)
    return status, response_headers, json.loads(response_body.decode("utf-8"))


def _assert_solr_compatible_cors_headers(headers, origin: str, allowed_headers: str = "*"):
    assert headers["Access-Control-Allow-Origin"] == origin
    assert headers["Access-Control-Allow-Credentials"] == "true"
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert headers["Access-Control-Allow-Headers"] == allowed_headers
    assert headers["Access-Control-Max-Age"] == "600"
    assert headers["Vary"] == "Origin"


@pytest.fixture(scope="session")
def nameres_server():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    process = subprocess.Popen(
        [sys.executable, "-m", "nameres", "--host=127.0.0.1", f"--port={port}"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + 45
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"NameResolution server exited during startup:\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.25)
    else:
        process.terminate()
        output = process.stdout.read() if process.stdout else ""
        raise RuntimeError(f"NameResolution server did not start in time:\n{output}")

    yield base_url

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def test_status_reports_ok(nameres_server):
    status, _, body = _request_json(nameres_server, "/status")

    assert status == 200
    assert body["status"] == "ok"
    assert body["message"]
    assert "biolink_model_toolkit_version" in body
    assert "numDocs" in body
    assert "deletedDocs" in body
    assert "segmentCount" in body
    assert "size" in body


def test_lookup_get_returns_result_shape(nameres_server):
    status, _, body = _request_json(nameres_server, "/lookup?string=aspirin&limit=1")

    assert status == 200
    assert isinstance(body, list)
    assert body

    first_result = body[0]
    assert first_result["curie"]
    assert first_result["label"]
    assert isinstance(first_result["synonyms"], list)
    assert isinstance(first_result["types"], list)
    assert "score" in first_result


def test_lookup_accepts_issue_8_query_shape(nameres_server):
    query = urlencode(
        {
            "string": "diabetes",
            "limit": 5,
            "autocomplete": "true",
            "biolink_type": "DiseaseOrPhenotypicFeature",
            "only_prefixes": "MONDO|HP",
        }
    )

    status, _, body = _request_json(nameres_server, f"/lookup?{query}")

    assert status == 200
    assert isinstance(body, list)


def test_bulk_lookup_post_returns_results_by_input_string(nameres_server):
    status, _, body = _request_json(
        nameres_server,
        "/bulk-lookup?limit=1",
        method="POST",
        body={"strings": ["aspirin", "diabetes"]},
    )

    assert status == 200
    assert set(body) == {"aspirin", "diabetes"}
    assert isinstance(body["aspirin"], list)
    assert isinstance(body["diabetes"], list)


def test_synonyms_post_returns_known_and_missing_curies(nameres_server):
    lookup_status, _, lookup_body = _request_json(nameres_server, "/lookup?string=aspirin&limit=1")
    assert lookup_status == 200
    known_curie = lookup_body[0]["curie"]

    status, _, body = _request_json(
        nameres_server,
        "/synonyms",
        method="POST",
        body={"preferred_curies": [known_curie, "NONE:1234"]},
    )

    assert status == 200
    assert body[known_curie]["curie"] == known_curie
    assert body["NONE:1234"] == {}


def test_get_response_includes_solr_compatible_cors_headers(nameres_server):
    origin = "https://translatorsri.github.io"
    status, headers, _ = _request(nameres_server, "/version", headers={"Origin": origin})

    assert status == 200
    _assert_solr_compatible_cors_headers(headers, origin)


@pytest.mark.parametrize(
    ("path", "requested_method", "requested_headers"),
    [
        ("/lookup", "GET", None),
        ("/bulk-lookup", "POST", "content-type"),
        ("/synonyms", "POST", "content-type"),
        ("/status", "GET", None),
    ],
)
def test_preflight_returns_solr_compatible_cors_headers(
    nameres_server, path: str, requested_method: str, requested_headers: str | None
):
    origin = "https://translatorsri.github.io"
    request_headers = {
        "Origin": origin,
        "Access-Control-Request-Method": requested_method,
    }
    if requested_headers is not None:
        request_headers["Access-Control-Request-Headers"] = requested_headers

    status, headers, body = _request(nameres_server, path, method="OPTIONS", headers=request_headers)

    assert status == 200
    assert body == b""
    _assert_solr_compatible_cors_headers(headers, origin, requested_headers or "*")
