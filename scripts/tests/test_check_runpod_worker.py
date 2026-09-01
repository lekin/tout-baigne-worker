#!/usr/bin/env python3
"""Unit tests for the RunPod Load Balancer readiness probe."""
import itertools
import json
import os
import sys
import unittest
from unittest import mock

import requests as requests_lib

# Ensure the project root is on sys.path so `scripts.check_runpod_worker` can import.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from check_runpod_worker import RunPodReadinessProbe, _should_fail_fast, _should_retry


class FakeResponse:
    def __init__(self, status_code: int, body: dict, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text or json.dumps(body)

    def json(self):
        return self._body


class TestStatusClassifiers(unittest.TestCase):
    def test_retryable_statuses(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(_should_retry(status), f"{status} should be retryable")
            self.assertFalse(_should_fail_fast(status), f"{status} should not fail fast")

    def test_auth_failures(self):
        for status in (401, 403):
            self.assertTrue(_should_fail_fast(status), f"{status} should fail fast")
            self.assertFalse(_should_retry(status), f"{status} should not be retried")

    def test_other_client_errors_fail_fast(self):
        for status in (400, 404, 405, 422):
            self.assertTrue(_should_fail_fast(status), f"{status} should fail fast")
            self.assertFalse(_should_retry(status), f"{status} should not be retried")


class TestReadinessProbe(unittest.TestCase):
    def setUp(self):
        os.environ["RUNPOD_API_KEY"] = "test-key"

    @mock.patch("check_runpod_worker.requests.get")
    @mock.patch("check_runpod_worker.time.sleep")
    @mock.patch("check_runpod_worker.time.monotonic")
    def test_503_then_503_then_200(self, mock_mono, mock_sleep, mock_get):
        """A cold worker that starts after two 503s."""
        mock_get.side_effect = [
            FakeResponse(503, {}),
            FakeResponse(503, {}),
            FakeResponse(200, {"status": "ok"}),
        ]
        mock_sleep.side_effect = lambda _s: None
        # Time starts at 0 and advances by 2.5s each sleep
        times = [0.0]
        for attempt in range(1, 4):
            times.extend([
                2.5 * (attempt - 1) + 0.2,  # attempt start
                2.5 * (attempt - 1) + 0.5,  # attempt end
                2.5 * (attempt - 1) + 0.5,  # post-sleep (start of next loop)
            ])
        # Append a generous number of values for time.monotonic calls inside log/print.
        times.extend(list(itertools.islice(itertools.count(10, 0.1), 50)))
        mock_mono.side_effect = times

        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            overall_timeout=30,
            poll_interval=2.5,
        )
        result = probe.probe()
        self.assertTrue(result.ready)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.status, 200)
        self.assertIsNotNone(result.ready_seconds)
        self.assertGreater(result.ready_seconds, 0)

    @mock.patch("check_runpod_worker.requests.get")
    def test_401_fails_fast(self, mock_get):
        mock_get.return_value = FakeResponse(401, {}, text="Unauthorized")
        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            overall_timeout=30,
        )
        result = probe.probe()
        self.assertFalse(result.ready)
        self.assertEqual(result.error_type, "authentication_failed")
        self.assertEqual(result.attempts, 1)

    @mock.patch("check_runpod_worker.requests.get")
    def test_403_fails_fast(self, mock_get):
        mock_get.return_value = FakeResponse(403, {}, text="Forbidden")
        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            overall_timeout=30,
        )
        result = probe.probe()
        self.assertFalse(result.ready)
        self.assertEqual(result.error_type, "authentication_failed")

    @mock.patch("check_runpod_worker.requests.get")
    @mock.patch("check_runpod_worker.time.sleep")
    @mock.patch("check_runpod_worker.time.monotonic")
    def test_network_timeout_then_200(self, mock_mono, mock_sleep, mock_get):
        """A network timeout should be retried, then the worker becomes ready."""
        mock_get.side_effect = [
            requests_lib.exceptions.ConnectTimeout("connection timed out"),
            FakeResponse(200, {"status": "ok"}),
        ]
        mock_sleep.side_effect = lambda _s: None
        mock_mono.side_effect = list(itertools.islice(itertools.count(0, 0.1), 100))

        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            overall_timeout=30,
            poll_interval=2.5,
        )
        result = probe.probe()
        self.assertTrue(result.ready)
        self.assertEqual(result.attempts, 2)

    @mock.patch("check_runpod_worker.requests.get")
    @mock.patch("check_runpod_worker.time.sleep")
    @mock.patch("check_runpod_worker.time.monotonic")
    def test_503_until_deadline(self, mock_mono, mock_sleep, mock_get):
        """Continuous 503s until the deadline should timeout."""
        mock_get.return_value = FakeResponse(503, {})
        mock_sleep.side_effect = lambda _s: None
        mock_mono.side_effect = list(itertools.islice(itertools.count(0, 0.5), 1000))

        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            overall_timeout=10,
            poll_interval=2.5,
        )
        result = probe.probe()
        self.assertFalse(result.ready)
        self.assertEqual(result.error_type, "readiness_timeout")
        self.assertEqual(result.status, 503)

    @mock.patch("check_runpod_worker.requests.get")
    def test_version_mismatch(self, mock_get):
        mock_get.return_value = FakeResponse(
            200,
            {"status": "ok", "version": "wrong-sha"},
        )
        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            expected_version="expected-sha",
            overall_timeout=30,
        )
        result = probe.probe()
        self.assertFalse(result.ready)
        self.assertEqual(result.error_type, "version_mismatch")
        self.assertEqual(result.version, "wrong-sha")

    @mock.patch("check_runpod_worker.requests.get")
    def test_version_match(self, mock_get):
        mock_get.return_value = FakeResponse(
            200,
            {"status": "ok", "version": "expected-sha"},
        )
        probe = RunPodReadinessProbe(
            endpoint_id="ylkhb72ej3hijz",
            expected_version="expected-sha",
            overall_timeout=30,
        )
        result = probe.probe()
        self.assertTrue(result.ready)
        self.assertEqual(result.version, "expected-sha")


if __name__ == "__main__":
    unittest.main()
