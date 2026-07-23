"""Fixtures for the BrevetHub test suite.

BrevetHub is isolated from Team Asha, so it gets its own test app built from
`brevethub.app.create_app()`. Model functions are monkeypatched per-test so these
tests never open a real DB connection (per repo convention: no real external
calls). Env is set at import time — before any `brevethub` import — so the
config's production guard never trips and OAuth registration has dummy creds.
"""
import os

import pytest

# Set BEFORE importing brevethub so brevethub.config reads these at import time.
os.environ.setdefault('GOOGLE_CLIENT_ID', 'test-client-id')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
os.environ.setdefault('BREVETHUB_SECRET_KEY', 'test-secret-key-that-is-long-enough')


@pytest.fixture
def app():
    from brevethub.app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['SECRET_KEY'] = 'test-secret-key-that-is-long-enough'
    application.debug = False
    return application


@pytest.fixture
def client(app):
    return app.test_client()
