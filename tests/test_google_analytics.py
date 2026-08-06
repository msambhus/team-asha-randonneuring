"""Global GA4 installation and matching privacy disclosure."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
MEASUREMENT_ID = "G-LVJ9R7K2Z3"


def test_ga4_tag_is_loaded_and_configured_globally():
    source = (ROOT / "templates" / "base.html").read_text()
    assert f"googletagmanager.com/gtag/js?id={MEASUREMENT_ID}" in source
    assert f"gtag('config', '{MEASUREMENT_ID}')" in source
    assert "window.dataLayer = window.dataLayer || []" in source


def test_privacy_policy_discloses_google_analytics():
    source = (ROOT / "templates" / "privacy.html").read_text()
    assert "Google Analytics 4" in source
    assert "Google Analytics cookies" in source
    assert "We do not use third-party analytics" not in source
