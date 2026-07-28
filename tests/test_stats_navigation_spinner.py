"""Regression contract for Stats navigation feedback."""
from pathlib import Path


def test_back_navigation_clears_stats_loading_state():
    template = (
        Path(__file__).parents[1] / "templates" / "base.html"
    ).read_text()

    assert "window.addEventListener('pageshow'" in template
    assert "link.classList.remove('analysis-nav-loading')" in template
    assert "link.removeAttribute('aria-busy')" in template
    assert "link.querySelectorAll('.analysis-nav-spinner')" in template
    assert "spinner.remove()" in template
