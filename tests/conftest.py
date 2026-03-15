"""Shared fixtures for integration tests."""
import os
import pytest
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get('TEST_DATABASE_URL', os.environ.get('DATABASE_URL'))


@pytest.fixture
def app():
    """Create a Flask app configured for testing with DEBUG=False."""
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['DEBUG'] = False
    application.debug = False
    if DB_URL:
        application.config['DATABASE_URL'] = DB_URL
    return application


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def db_conn(app):
    """Direct psycopg2 connection for DB assertions. Rolls back after each test."""
    if not DB_URL:
        pytest.skip('TEST_DATABASE_URL or DATABASE_URL env var required for integration tests')
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()
