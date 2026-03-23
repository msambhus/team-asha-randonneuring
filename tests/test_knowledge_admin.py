"""Tests for knowledge base admin — model functions and routes (Plan 12-02)."""

from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Model function tests
# ---------------------------------------------------------------------------


class TestGetKnowledgeSources:
    @patch("models.get_db")
    def test_returns_web_sources(self, mock_get_db):
        from models import get_knowledge_sources

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"source": "web_rusa.org", "chunk_count": 5, "first_embedded": "2026-01-01", "last_embedded": "2026-01-15"},
            {"source": "web_randonneuring.org", "chunk_count": 3, "first_embedded": "2026-01-02", "last_embedded": "2026-01-10"},
        ]

        result = get_knowledge_sources()
        assert len(result) == 2
        assert result[0]["source"] == "web_rusa.org"
        # Verify the SQL queries web_ sources
        sql_arg = mock_cursor.execute.call_args[0][0]
        assert "web_%" in sql_arg

    @patch("models.get_db")
    def test_returns_empty_list(self, mock_get_db):
        from models import get_knowledge_sources

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        result = get_knowledge_sources()
        assert result == []


class TestDeleteKnowledgeSource:
    @patch("models.get_db")
    def test_deletes_and_returns_count(self, mock_get_db):
        from models import delete_knowledge_source

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

        count = delete_knowledge_source("web_rusa.org")
        assert count == 3
        mock_conn.commit.assert_called_once()
        sql_arg = mock_cursor.execute.call_args[0][0]
        assert "DELETE" in sql_arg

    def test_rejects_non_web_source(self):
        from models import delete_knowledge_source

        with pytest.raises(ValueError, match="web_"):
            delete_knowledge_source("fresh_start")

    def test_rejects_empty_source(self):
        from models import delete_knowledge_source

        with pytest.raises(ValueError):
            delete_knowledge_source("")
