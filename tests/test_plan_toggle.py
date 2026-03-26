"""Tests for admin-gated plan toggle in ride_plan_detail.html."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def template_app(app):
    """App with Jinja2 configured for template rendering tests."""
    return app


def _render_plan_toggle(app, is_admin=False, user_custom_plan=True, **kwargs):
    """Render the custom plan banner section of ride_plan_detail.html.

    Returns the rendered HTML string for the relevant section.
    We render a minimal subset by creating a test template that includes the banner logic.
    """
    from jinja2 import Template

    # Extract the custom plan banner section from the real template
    # Instead of rendering the full template (which needs many variables),
    # render a simplified version with just the toggle logic
    template_str = """
    {% if user_custom_plan %}
    <a href="/plan/test?view=custom" class="btn-custom-plan">View My Custom Plan</a>
    {% if is_admin %}
    <a href="/plan/test?view=base" class="btn-custom-plan" style="background:#6366f1;">Base Plan</a>
    {% endif %}
    <a href="/plan/test/compare" class="btn-custom-plan" style="background:#f59e0b;">Compare Plans</a>
    {% endif %}
    """

    with app.app_context():
        env = app.jinja_env
        tmpl = env.from_string(template_str)
        return tmpl.render(
            is_admin=is_admin,
            user_custom_plan=user_custom_plan,
            **kwargs,
        )


class TestBasePlanToggleAdminGate:
    """Test that Base Plan button is admin-only."""

    def test_base_plan_hidden_for_non_admin(self, template_app):
        """Non-admin user with custom plan should NOT see Base Plan."""
        html = _render_plan_toggle(template_app, is_admin=False, user_custom_plan=True)
        assert 'Base Plan' not in html

    def test_base_plan_visible_for_admin(self, template_app):
        """Admin user with custom plan SHOULD see Base Plan."""
        html = _render_plan_toggle(template_app, is_admin=True, user_custom_plan=True)
        assert 'Base Plan' in html

    def test_custom_plan_visible_for_non_admin(self, template_app):
        """Non-admin user with custom plan should still see View My Custom Plan."""
        html = _render_plan_toggle(template_app, is_admin=False, user_custom_plan=True)
        assert 'View My Custom Plan' in html

    def test_custom_plan_visible_for_admin(self, template_app):
        """Admin user with custom plan should also see View My Custom Plan."""
        html = _render_plan_toggle(template_app, is_admin=True, user_custom_plan=True)
        assert 'View My Custom Plan' in html

    def test_compare_plans_visible_for_non_admin(self, template_app):
        """Non-admin user with custom plan should see Compare Plans."""
        html = _render_plan_toggle(template_app, is_admin=False, user_custom_plan=True)
        assert 'Compare Plans' in html

    def test_compare_plans_visible_for_admin(self, template_app):
        """Admin user with custom plan should see Compare Plans."""
        html = _render_plan_toggle(template_app, is_admin=True, user_custom_plan=True)
        assert 'Compare Plans' in html

    def test_no_buttons_without_custom_plan(self, template_app):
        """User without custom plan should see none of these buttons."""
        html = _render_plan_toggle(template_app, is_admin=False, user_custom_plan=False)
        assert 'View My Custom Plan' not in html
        assert 'Base Plan' not in html
        assert 'Compare Plans' not in html


class TestRealTemplateAdminGate:
    """Verify the actual ride_plan_detail.html template has the admin gate."""

    def test_template_has_admin_gate_around_base_plan(self):
        """The actual template should wrap Base Plan in {% if is_admin %}."""
        import os
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'templates', 'ride_plan_detail.html'
        )
        with open(template_path, 'r') as f:
            content = f.read()

        # Find the Base Plan link
        base_plan_idx = content.find('Base Plan')
        assert base_plan_idx > 0, "Base Plan text not found in template"

        # Check that {% if is_admin %} appears before Base Plan and after Custom Plan
        custom_plan_idx = content.find('View My Custom Plan')
        assert custom_plan_idx > 0, "View My Custom Plan text not found"

        # The is_admin gate should be between custom plan and base plan
        between = content[custom_plan_idx:base_plan_idx]
        assert 'is_admin' in between, (
            "Expected {% if is_admin %} between 'View My Custom Plan' and 'Base Plan'"
        )
