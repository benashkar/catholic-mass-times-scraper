"""Tests for src/utils/http.py — mocked HTTP request behavior."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.utils.http import fetch_page


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset rate-limiting state between tests."""
    import src.utils.http as http_mod

    http_mod._last_request_time = 0.0
    yield


@pytest.fixture(autouse=True)
def _no_sleep():
    """Patch time.sleep so tests run instantly."""
    with patch("src.utils.http.time.sleep"):
        yield


class TestFetchPageSuccess:
    @patch("src.utils.http.requests.get")
    def test_returns_html_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>Hello</html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_page("https://example.com")
        assert result == "<html>Hello</html>"


class TestFetchPage404:
    @patch("src.utils.http.requests.get")
    def test_404_returns_none_without_retry(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        result = fetch_page("https://example.com/missing")
        assert result is None
        assert mock_get.call_count == 1  # No retry on 404


class TestFetchPage500:
    @patch("src.utils.http.requests.get")
    def test_500_retries_then_fails(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        result = fetch_page("https://example.com/error")
        assert result is None
        assert mock_get.call_count == 3  # MAX_RETRIES = 3


class TestFetchPageConnectionError:
    @patch("src.utils.http.requests.get")
    def test_connection_error_retries(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = fetch_page("https://example.com/down")
        assert result is None
        assert mock_get.call_count == 3


class TestFetchPageRateLimiting:
    @patch("src.utils.http.requests.get")
    @patch("src.utils.http.time.sleep")
    def test_rate_limiting_calls_sleep(self, mock_sleep, mock_get):
        import time as real_time

        import src.utils.http as http_mod

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Simulate a recent request (just happened)
        http_mod._last_request_time = real_time.time()

        fetch_page("https://example.com")
        # Should have called sleep for rate limiting
        assert mock_sleep.called


class TestFetchPageTimeout:
    @patch("src.utils.http.requests.get")
    def test_timeout_retries(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        result = fetch_page("https://example.com/slow")
        assert result is None
        assert mock_get.call_count == 3
