"""Tests for services/email_normalize.normalize_email — Gmail dot/+tag aliasing.

Must stay in sync with the SQL backfill in migrations/031_add_email_normalized.sql.
"""
import pytest

from services.email_normalize import normalize_email


@pytest.mark.parametrize('raw,expected', [
    # Gmail: dots and +tags in the local part are ignored; googlemail → gmail.
    ('mihir.sambhus@gmail.com', 'mihirsambhus@gmail.com'),
    ('mihirsambhus@gmail.com', 'mihirsambhus@gmail.com'),
    ('Mihir.Sambhus+rando@Gmail.com', 'mihirsambhus@gmail.com'),
    ('m.i.h.i.r@googlemail.com', 'mihir@gmail.com'),
    ('a+b+c@gmail.com', 'a@gmail.com'),
    # Non-Gmail: only lowercased — dots/+tags are NOT stripped (never merge
    # genuinely different addresses).
    ('First.Last@example.com', 'first.last@example.com'),
    ('user+tag@fastmail.com', 'user+tag@fastmail.com'),
    ('RIDER@Team.ORG', 'rider@team.org'),
    # Degenerate inputs — return lowercased, no crash.
    ('not-an-email', 'not-an-email'),
    ('', ''),
    ('  Spaced@Gmail.com  ', 'spaced@gmail.com'),
])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_normalize_email_none():
    assert normalize_email(None) == ''


def test_gmail_variants_collapse_to_one():
    forms = ['mihir.sambhus@gmail.com', 'mihirsambhus@gmail.com',
             'mihir.sambhus+ta@gmail.com', 'MihirSambhus@googlemail.com']
    assert len({normalize_email(f) for f in forms}) == 1
