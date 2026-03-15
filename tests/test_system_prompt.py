"""Tests for CHAT_SYSTEM_PROMPT in services/openai_coach.py (KNOW-01 through KNOW-05)."""


def test_randonneuring_content():
    """KNOW-02: System prompt contains ACP/RUSA brevet distances, SR, R-12, PBP."""
    from services.openai_coach import CHAT_SYSTEM_PROMPT

    # Brevet distances and time limits
    assert "200km" in CHAT_SYSTEM_PROMPT or ("200" in CHAT_SYSTEM_PROMPT and "13.5" in CHAT_SYSTEM_PROMPT)
    assert "300km" in CHAT_SYSTEM_PROMPT or ("300" in CHAT_SYSTEM_PROMPT and "20h" in CHAT_SYSTEM_PROMPT)
    assert "400km" in CHAT_SYSTEM_PROMPT or ("400" in CHAT_SYSTEM_PROMPT and "27h" in CHAT_SYSTEM_PROMPT)
    assert "600km" in CHAT_SYSTEM_PROMPT or ("600" in CHAT_SYSTEM_PROMPT and "40h" in CHAT_SYSTEM_PROMPT)

    # SR and R-12
    assert "Super Randonneur" in CHAT_SYSTEM_PROMPT or "SR" in CHAT_SYSTEM_PROMPT
    assert "R-12" in CHAT_SYSTEM_PROMPT

    # PBP and organizations
    assert "PBP" in CHAT_SYSTEM_PROMPT or "Paris-Brest-Paris" in CHAT_SYSTEM_PROMPT
    assert "ACP" in CHAT_SYSTEM_PROMPT
    assert "RUSA" in CHAT_SYSTEM_PROMPT


def test_offtopic_redirect():
    """KNOW-01, KNOW-03: System prompt contains off-topic redirect instruction."""
    from services.openai_coach import CHAT_SYSTEM_PROMPT

    prompt_lower = CHAT_SYSTEM_PROMPT.lower()
    assert "cycling" in prompt_lower
    assert any(word in prompt_lower for word in ["only", "exclusively", "focus"])


def test_maintenance_content():
    """KNOW-04: System prompt contains bike maintenance guidance."""
    from services.openai_coach import CHAT_SYSTEM_PROMPT

    prompt_lower = CHAT_SYSTEM_PROMPT.lower()
    assert any(word in prompt_lower for word in ["maintenance", "repair"])

    maintenance_terms = ["tire", "brake", "chain", "derailleur", "light"]
    matches = sum(1 for term in maintenance_terms if term in prompt_lower)
    assert matches >= 2, f"Expected at least 2 maintenance terms, found {matches}"


def test_nutrition_content():
    """KNOW-05: System prompt contains nutrition guidance."""
    from services.openai_coach import CHAT_SYSTEM_PROMPT

    prompt_lower = CHAT_SYSTEM_PROMPT.lower()
    assert any(word in prompt_lower for word in ["nutrition", "fueling"])

    nutrition_terms = ["calorie", "hydration", "electrolyte", "carbohydrate", "fuel"]
    matches = sum(1 for term in nutrition_terms if term in prompt_lower)
    assert matches >= 1, f"Expected at least 1 nutrition term, found {matches}"


def test_chat_system_prompt_not_placeholder():
    """Guard: CHAT_SYSTEM_PROMPT is the real prompt, not the Plan 02 fallback."""
    from services.openai_coach import CHAT_SYSTEM_PROMPT

    placeholder = "You are a cycling and randonneuring coaching assistant for Team Asha."
    assert CHAT_SYSTEM_PROMPT != placeholder, "CHAT_SYSTEM_PROMPT is still the placeholder"
    assert len(CHAT_SYSTEM_PROMPT) > 500, f"CHAT_SYSTEM_PROMPT too short: {len(CHAT_SYSTEM_PROMPT)} chars"
