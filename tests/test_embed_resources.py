"""Tests for scripts/embed_resources.py — web resource embedding pipeline."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Pure function tests (Task 1)
# ---------------------------------------------------------------------------


class TestUrlToSourceName:
    def test_simple_domain(self):
        from scripts.embed_resources import url_to_source_name

        assert url_to_source_name("https://randonneuring.org/guide") == "web_randonneuring.org"

    def test_strips_www(self):
        from scripts.embed_resources import url_to_source_name

        assert url_to_source_name("https://www.rusa.org/rules") == "web_rusa.org"

    def test_subdomain_kept(self):
        from scripts.embed_resources import url_to_source_name

        assert url_to_source_name("https://blog.example.com/post") == "web_blog.example.com"


class TestContentHash:
    def test_deterministic(self):
        from scripts.embed_resources import content_hash

        expected = hashlib.sha256(b"hello").hexdigest()
        assert content_hash("hello") == expected

    def test_hex_string(self):
        from scripts.embed_resources import content_hash

        result = content_hash("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex length


class TestChunkText:
    def test_splits_paragraphs(self):
        from scripts.embed_resources import chunk_text

        text = "A" * 1200 + "\n\n" + "B" * 1200 + "\n\n" + "C" * 1200
        chunks = chunk_text(text, soft_limit=2000)
        assert len(chunks) >= 2

    def test_single_short(self):
        from scripts.embed_resources import chunk_text

        text = "Short paragraph."
        chunks = chunk_text(text, soft_limit=2000)
        assert len(chunks) == 1
        assert chunks[0] == "Short paragraph."

    def test_empty_text(self):
        from scripts.embed_resources import chunk_text

        assert chunk_text("") == []


class TestExtractUrlContent:
    @patch("scripts.embed_resources.trafilatura")
    def test_quality_filter_short_content(self, mock_traf):
        from scripts.embed_resources import extract_url_content

        mock_traf.fetch_url.return_value = "<html>hi</html>"
        mock_traf.extract.return_value = "too short"  # < 200 chars
        assert extract_url_content("https://example.com") is None

    @patch("scripts.embed_resources.trafilatura")
    def test_returns_text_above_threshold(self, mock_traf):
        from scripts.embed_resources import extract_url_content

        long_text = "A" * 300
        mock_traf.fetch_url.return_value = "<html>content</html>"
        mock_traf.extract.return_value = long_text
        assert extract_url_content("https://example.com") == long_text

    @patch("scripts.embed_resources.trafilatura")
    def test_fetch_failure(self, mock_traf):
        from scripts.embed_resources import extract_url_content

        mock_traf.fetch_url.return_value = None
        assert extract_url_content("https://example.com") is None


class TestFetchSheetUrls:
    @patch("scripts.embed_resources.requests.get")
    def test_parses_url_column(self, mock_get):
        from scripts.embed_resources import fetch_sheet_urls

        csv_text = "Name,URL,Notes\nRUSA,https://rusa.org,good\nSFR,https://sfrandonneurs.org,also good\n"
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        urls = fetch_sheet_urls("https://sheets.example.com/csv")
        assert urls == ["https://rusa.org", "https://sfrandonneurs.org"]

    @patch("scripts.embed_resources.requests.get")
    def test_auto_detect_http_column(self, mock_get):
        """When no known column name matches, auto-detects column with http values."""
        from scripts.embed_resources import fetch_sheet_urls

        csv_text = "Title,Resource Link,Category\nRUSA,https://rusa.org,cycling\nSFR,https://sfrandonneurs.org,cycling\n"
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        urls = fetch_sheet_urls("https://sheets.example.com/csv")
        assert urls == ["https://rusa.org", "https://sfrandonneurs.org"]

    @patch("scripts.embed_resources.requests.get")
    def test_no_url_column_returns_empty(self, mock_get):
        from scripts.embed_resources import fetch_sheet_urls

        csv_text = "Name,Score\nAlice,10\nBob,20\n"
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        urls = fetch_sheet_urls("https://sheets.example.com/csv")
        assert urls == []

    @patch("scripts.embed_resources.requests.get")
    def test_explicit_url_column(self, mock_get):
        from scripts.embed_resources import fetch_sheet_urls

        csv_text = "Title,MyLinks,Category\nRUSA,https://rusa.org,cycling\n"
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        urls = fetch_sheet_urls("https://sheets.example.com/csv", url_column="MyLinks")
        assert urls == ["https://rusa.org"]


# ---------------------------------------------------------------------------
# DB integration tests (Task 2)
# ---------------------------------------------------------------------------


class TestChunkAlreadyExists:
    def test_returns_true_when_found(self):
        from scripts.embed_resources import chunk_already_exists

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        assert chunk_already_exists(mock_conn, "web_rusa.org", "abc123") is True
        mock_cursor.execute.assert_called_once()

    def test_returns_false_when_not_found(self):
        from scripts.embed_resources import chunk_already_exists

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        assert chunk_already_exists(mock_conn, "web_rusa.org", "abc123") is False


class TestBulkInsertWebChunks:
    @patch("psycopg2.extras.execute_values")
    def test_inserts_chunks_with_content_hash(self, mock_exec_values):
        from scripts.embed_resources import bulk_insert_web_chunks

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.rowcount = 2

        chunks = [
            {"content": "chunk one", "embedding": [0.1] * 1536, "content_hash": "hash1"},
            {"content": "chunk two", "embedding": [0.2] * 1536, "content_hash": "hash2"},
        ]

        inserted, skipped = bulk_insert_web_chunks(mock_conn, chunks, "web_rusa.org")

        assert inserted == 2
        assert skipped == 0
        mock_exec_values.assert_called_once()
        sql_arg = mock_exec_values.call_args[0][1]
        assert "content_hash" in sql_arg
        assert "ON CONFLICT" in sql_arg
        mock_conn.commit.assert_called_once()
