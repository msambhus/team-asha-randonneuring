"""Tests for RUSA.org membership expiry scraping."""
from datetime import date
from unittest.mock import patch

from shared.rusa_validator import (
    _parse_membership_expires,
    get_rusa_membership_status,
)

_SAMPLE_HTML = """
<table>
<tr><th>ID</th><th>Name</th><th>City</th><th>Club</th><th>Membership Expires</th></tr>
<tr>
  <td align=center><a href="/cgi-bin/resultsearch_PF.pl?mid=18463&sortby=date">18463</a></td>
  <td align=left><b>PERIASAMY RAJAGOPALAN, Arjun</b></td>
  <td align=left>San Jose, CA</td>
  <td align=left>San Francisco Randonneurs / 905030</td>
  <td align=center>2026/12/31</td>
</tr>
</table>
"""


def test_parse_membership_expires():
    assert _parse_membership_expires('2026/12/31') == date(2026, 12, 31)
    assert _parse_membership_expires('2025-01-01') == date(2025, 1, 1)


@patch('shared.rusa_validator.requests.get')
def test_get_rusa_membership_status_parses_member_row(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.content = _SAMPLE_HTML.encode()
    mock_get.return_value.raise_for_status = lambda: None

    result = get_rusa_membership_status('18463', today=date(2026, 8, 9))

    assert result['found'] is True
    assert result['rusa_id'] == '18463'
    assert result['rusa_name'] == 'PERIASAMY RAJAGOPALAN, Arjun'
    assert result['city'] == 'San Jose, CA'
    assert 'San Francisco Randonneurs' in result['rusa_club']
    assert result['membership_expires'] == '2026-12-31'
    assert result['current'] is True
    assert result['error'] is None


@patch('shared.rusa_validator.requests.get')
def test_get_rusa_membership_status_expired(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.content = _SAMPLE_HTML.encode()
    mock_get.return_value.raise_for_status = lambda: None

    result = get_rusa_membership_status('18463', today=date(2027, 1, 1))

    assert result['found'] is True
    assert result['current'] is False
