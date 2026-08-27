from pathlib import Path


TEMPLATES = Path(__file__).parents[2] / "brevethub" / "templates"


def test_validation_form_styles_make_fields_distinct():
    source = (TEMPLATES / "validation_submit.html").read_text()

    assert ".evidence-section .form-input" in source
    assert "border:2px solid #94a3b8" in source
    assert ".evidence-section .form-input:focus" in source
    assert "Choose one or more evidence sources below" in source
    assert source.count("evidence-choice-number") >= 3


def test_manual_proof_is_rendered_only_for_controls():
    source = (TEMPLATES / "validation_submit.html").read_text()

    assert "for control in controls if control.stop_type == 'control'" in source
    assert "Other brevet-card / receipt files" not in source


def test_validation_navigation_has_loading_feedback():
    base = (TEMPLATES / "base.html").read_text()
    for name in ("calendar.html", "dashboard.html", "my_validations.html"):
        assert "validation-submit-link" in (TEMPLATES / name).read_text()
    assert "Loading evidence" in base
    assert "aria-busy" in base
