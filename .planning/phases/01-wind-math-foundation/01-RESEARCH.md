# Phase 1: Wind Math Foundation - Research

**Researched:** 2026-03-23
**Domain:** Pure Python wind vector math, color intensity helpers, named constants in `services/weather.py`
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WIND-01 | System calculates crosswind component using sine projection of wind angle relative to rider bearing | `crosswind_component()` mirrors `headwind_component()` but uses `math.sin` instead of `math.cos`; must apply 180-degree "wind from" inversion before projection |
| WIND-02 | System classifies wind at each stop as headwind, tailwind, or crosswind based on 45-degree threshold (abs headwind > abs crosswind → head/tailwind; else → crosswind) | `classify_wind()` takes headwind_kmh + crosswind_kmh, compares absolute values; no external dependency |
| WIND-03 | System returns wind color (green #16A34A tailwind, red #DC2626 headwind, blue #2563EB crosswind) with intensity scaling based on wind speed | `wind_cell_style()` computes `rgba(r,g,b,opacity)` as inline Python string; opacity maps to three speed bands |
| WIND-04 | System returns font size scaling based on wind speed (0-5 km/h = 0.75rem, 5-15 = 0.875rem, 15+ = 1.0rem) | `wind_cell_style()` returns dict with `font_size` key; three discrete bands via if/elif/else |
| WIND-10 | Wind thresholds defined as named constants in services/weather.py (HEAVY_WIND_MAX_KMH=30, HEAVY_WIND_AVG_HEADWIND_KMH=15) | Two module-level constants in the `# Constants` block already present in `services/weather.py` |
</phase_requirements>

## Summary

Phase 1 is entirely pure Python — no API calls, no database, no templates. It adds three functions and two constants to the existing `services/weather.py` file, all of which have established formulas and no external dependencies. The work is scoped narrowly: `crosswind_component()`, `classify_wind()`, `wind_cell_style()`, and the two threshold constants `HEAVY_WIND_MAX_KMH` and `HEAVY_WIND_AVG_HEADWIND_KMH`. Every downstream phase depends on these foundations being correct.

The existing `headwind_component()` function in `services/weather.py` already demonstrates the exact pattern for wind projection including the critical 180-degree "wind from" direction inversion. The crosswind function is structurally identical but uses `math.sin` instead of `math.cos`. The classify function compares absolute values of the two components to determine which dominates, implementing the 45-degree threshold rule. The color/style helper computes Python-side `rgba()` strings because Tailwind's JIT static purging makes dynamic class names impossible — this is already a confirmed project decision in STATE.md.

The test pattern for this phase is entirely unit tests with no fixtures needed — no app context, no database, no mocking. All five functions/constants are pure and deterministic, making them trivially testable. The existing `tests/test_weather.py` file establishes the exact class-per-function pattern with descriptive docstrings for each scenario.

**Primary recommendation:** Add `crosswind_component()`, `classify_wind()`, `wind_cell_style()` to `services/weather.py` in the existing `# Pure functions` block, and add `HEAVY_WIND_MAX_KMH` and `HEAVY_WIND_AVG_HEADWIND_KMH` to the existing `# Constants` block. Write tests in `tests/test_weather.py` using the established class-per-function pattern.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `math` | stdlib | `math.sin()`, `math.cos()`, `math.radians()` for wind projections | Already used in `headwind_component()` and `calculate_bearing()`; zero import cost |
| `services/weather.py` | existing | All wind math lives here | Established project convention; existing functions already here |
| `tests/test_weather.py` | existing | Unit tests for new functions | Established test file for this module |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | existing | Test runner | All test execution |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Python `math` | `numpy` | numpy inflates bundle; research confirms it was explicitly rejected for this project |
| Inline `rgba()` strings | Tailwind classes | Tailwind JIT static purging makes dynamic classes impossible; confirmed in STATE.md and SUMMARY.md |

**Installation:** No new dependencies. All work uses existing imports.

## Architecture Patterns

### Recommended Project Structure
No new files needed. All additions go into existing files:

```
services/
└── weather.py      # Add crosswind_component, classify_wind, wind_cell_style, 2 constants

tests/
└── test_weather.py # Add TestCrosswindComponent, TestClassifyWind, TestWindCellStyle classes
```

### Pattern 1: Wind Projection Function
**What:** Pure function taking wind speed, wind_from direction (meteorological), and rider bearing; returns signed float
**When to use:** Computing either headwind or crosswind projection
**Example:**
```python
# Source: Mirrors existing headwind_component() in services/weather.py
def crosswind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return crosswind component (positive=right crosswind, negative=left crosswind).

    wind_from_deg is meteorological convention (direction wind blows FROM).
    """
    if wind_speed == 0:
        return 0
    # Wind travel direction = wind_from + 180 (CRITICAL: must invert)
    wind_travel_deg = (wind_from_deg + 180) % 360
    # Sine projection: magnitude of component perpendicular to rider direction
    angle = math.radians(wind_travel_deg - rider_bearing_deg)
    return round(wind_speed * math.sin(angle), 1)
```

### Pattern 2: Wind Classification (45-degree threshold)
**What:** Determines wind type from headwind and crosswind components by comparing absolute values
**When to use:** Classifying wind at any control point
**Example:**
```python
# Source: Derived from REQUIREMENTS.md WIND-02 specification
def classify_wind(headwind_kmh, crosswind_kmh):
    """Classify wind type using 45-degree threshold rule.

    Returns 'headwind', 'tailwind', or 'crosswind'.
    When |headwind| > |crosswind|, the wind is primarily head/tail.
    """
    if abs(headwind_kmh) > abs(crosswind_kmh):
        return 'tailwind' if headwind_kmh < 0 else 'headwind'
    return 'crosswind'
```

### Pattern 3: Wind Cell Style Helper
**What:** Returns a dict with `color` (hex), `background` (rgba string), and `font_size` (rem string) for template inline styles
**When to use:** Rendering any wind table cell in Jinja2 templates
**Example:**
```python
# Source: Derived from REQUIREMENTS.md WIND-03, WIND-04 and STATE.md inline-styles decision
_WIND_COLORS = {
    'headwind': (220, 38, 38),   # #DC2626
    'tailwind': (22, 163, 74),   # #16A34A
    'crosswind': (37, 99, 235),  # #2563EB
}

def wind_cell_style(wind_speed_kmh, wind_type):
    """Return inline style dict for a wind table cell.

    wind_speed_kmh: absolute wind speed for intensity scaling
    wind_type: 'headwind', 'tailwind', or 'crosswind'
    """
    r, g, b = _WIND_COLORS.get(wind_type, (37, 99, 235))

    # Opacity bands matching BPLN-03 spec
    if wind_speed_kmh < 5:
        opacity = 0.15
    elif wind_speed_kmh < 15:
        opacity = 0.35
    else:
        opacity = 0.65

    # Font size bands matching WIND-04 spec
    if wind_speed_kmh < 5:
        font_size = '0.75rem'
    elif wind_speed_kmh < 15:
        font_size = '0.875rem'
    else:
        font_size = '1.0rem'

    return {
        'color': f'#{r:02X}{g:02X}{b:02X}',
        'background': f'rgba({r},{g},{b},{opacity})',
        'font_size': font_size,
    }
```

### Pattern 4: Named Constants in the Constants Block
**What:** Module-level constants defined once, imported by all consumers
**When to use:** Any threshold value used in more than one place (warning banner, cell intensity)
**Example:**
```python
# Source: REQUIREMENTS.md WIND-10, WARN-03
# Add to existing # ── Constants ── block in services/weather.py

# Wind warning thresholds (used by warning banner and cell intensity)
HEAVY_WIND_MAX_KMH = 30          # max wind speed that triggers heavy wind warning
HEAVY_WIND_AVG_HEADWIND_KMH = 15 # avg headwind speed that triggers heavy wind warning
```

### Anti-Patterns to Avoid
- **Skipping the 180-degree inversion in crosswind_component:** The meteorological convention gives wind FROM direction; the projection must use wind TRAVEL direction. `headwind_component()` already does this correctly — crosswind must match. Omitting it inverts all wind labels.
- **Using Tailwind dynamic classes for wind colors:** JIT static purging removes any class not present in source at build time. Colors must be computed as Python strings and passed as inline `style=` attributes.
- **Duplicating threshold constants:** Do not put `30` or `15` as magic numbers in route handlers or templates. Define once in `services/weather.py`, import everywhere.
- **Creating a new service file:** All wind math goes in `services/weather.py`. The project convention (per CLAUDE.md and SUMMARY.md) is to extend existing service files, not proliferate new ones.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Trigonometry for projections | Custom angle math | `math.sin` / `math.cos` / `math.radians` | Python stdlib; already used in adjacent functions; no accuracy risk |
| Color hex-to-rgb conversion | Parsing `#DC2626` | Hard-coded RGB tuples in `_WIND_COLORS` dict | Only three colors; static dict is clearer and eliminates parsing errors |

**Key insight:** This phase has no novel engineering problems. The math formulas are established physics; the color mapping is a small lookup table; the constants are two integers.

## Common Pitfalls

### Pitfall 1: Missing 180-Degree Inversion in crosswind_component
**What goes wrong:** `wind_from_deg` is the direction wind blows FROM (e.g., 270 = west wind blowing eastward). The projection must use the travel direction (e.g., 90 = blowing toward east). Without the inversion, a west wind hitting a westbound rider reads as a tailwind when it is actually a headwind.
**Why it happens:** The meteorological convention is counterintuitive. The existing `headwind_component()` already applies `(wind_from_deg + 180) % 360` — this is the single most important thing to replicate in `crosswind_component()`.
**How to avoid:** Copy the exact inversion line from `headwind_component()`. Add a unit test: north wind (from=0°), rider heading east (90°) — crosswind magnitude should be near full wind speed; headwind component should be near zero.
**Warning signs:** If `crosswind_component(20, 270, 90)` returns a non-zero value close to ±20, the inversion is missing (a pure crosswind should have ~0 crosswind component only if you forgot the inversion reversal — re-check the angle reference).

### Pitfall 2: Classify Wind Boundary Cases
**What goes wrong:** When `abs(headwind_kmh) == abs(crosswind_kmh)` exactly (wind at exactly 45 degrees), the classification is ambiguous. The spec says `|headwind| > |crosswind|` → head/tailwind; else → crosswind. Strict greater-than means the exact boundary goes to crosswind.
**Why it happens:** The spec uses strict inequality. Implementing `>=` instead of `>` silently changes boundary behavior.
**How to avoid:** Use `abs(headwind_kmh) > abs(crosswind_kmh)` (strict). Write a test with wind at exactly 45 degrees to confirm it returns `'crosswind'`.
**Warning signs:** A 45-degree wind test returning `'headwind'` indicates `>=` was used instead of `>`.

### Pitfall 3: Font Size String Format
**What goes wrong:** Returning `0.75` (float) instead of `'0.75rem'` (string) from `wind_cell_style()`. The template renders it as `style="font-size: 0.75"` which CSS ignores silently.
**Why it happens:** Easy to forget the unit suffix when returning numeric-looking values.
**How to avoid:** Always return the rem string. Write tests that assert `'rem'` is in the font_size value.
**Warning signs:** Wind cells all render at the same (default) font size in the browser.

### Pitfall 4: Constants Not Importable
**What goes wrong:** Defining constants inside a function or class scope instead of at module level, making `from services.weather import HEAVY_WIND_MAX_KMH` fail with ImportError.
**Why it happens:** Editing inside a function block by accident.
**How to avoid:** Place both constants in the existing `# ── Constants ──` block at module level. Verify with a simple import test.
**Warning signs:** `ImportError` when other phases try to import the constants.

## Code Examples

Verified patterns from the existing codebase:

### Existing headwind_component (template for crosswind_component)
```python
# Source: services/weather.py lines 57-68 (verified by direct read)
def headwind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return headwind component (positive=headwind, negative=tailwind).

    wind_from_deg is meteorological convention (direction wind blows FROM).
    """
    if wind_speed == 0:
        return 0
    # Wind travel direction = wind_from + 180
    wind_travel_deg = (wind_from_deg + 180) % 360
    # Cosine projection: positive when wind opposes rider, negative when assisting
    angle = math.radians(wind_travel_deg - rider_bearing_deg)
    return round(wind_speed * math.cos(angle), 1)
```

### Existing test class structure (template for new test classes)
```python
# Source: tests/test_weather.py lines 137-159 (verified by direct read)
class TestHeadwindComponent:
    def test_pure_headwind(self):
        """West wind (270°), rider heading east (90°) = headwind."""
        from services.weather import headwind_component
        hw = headwind_component(20, 270, 90)
        assert hw > 18  # close to +20

    def test_pure_tailwind(self):
        """East wind (90°), rider heading east (90°) = tailwind."""
        from services.weather import headwind_component
        hw = headwind_component(20, 90, 90)
        assert hw < -18  # close to -20

    def test_pure_crosswind(self):
        """North wind (0°), rider heading east (90°) = crosswind ~= 0."""
        from services.weather import headwind_component
        hw = headwind_component(20, 0, 90)
        assert abs(hw) < 2  # near zero

    def test_no_wind(self):
        from services.weather import headwind_component
        hw = headwind_component(0, 270, 90)
        assert hw == 0
```

### Existing Constants block location
```python
# Source: services/weather.py lines 10-14 (verified by direct read)
# ── Constants ────────────────────────────────────────────────────────

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

ATTRIBUTION = "*Weather data: [Open-Meteo](https://open-meteo.com)*"
```
New constants (`HEAVY_WIND_MAX_KMH`, `HEAVY_WIND_AVG_HEADWIND_KMH`) go immediately after `ATTRIBUTION` in this block.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Magic number thresholds per-feature | Named constants imported everywhere | Phase 1 (now) | Warning banner (Phase 4) and cell intensity (Phase 3) stay in sync automatically |
| No crosswind tracking | Full crosswind component + classification | Phase 1 (now) | Enables proper three-color UI in Phases 3-7 |

**Deprecated/outdated:**
- `wind_label()` (the existing string-based label function): Still useful for the existing route weather chat feature, but Phase 1 replaces it as the primary classification mechanism for wind display columns with `classify_wind()`. Do NOT remove `wind_label()` — it is used by `format_weather_response()` which must remain functional.

## Open Questions

1. **Opacity values for rgba bands**
   - What we know: REQUIREMENTS.md BPLN-03 specifies three speed bands (0-5, 5-15, 15+); the hex colors are specified (WIND-03). The actual opacity numbers are not specified in requirements.
   - What's unclear: Exact opacity values per band (e.g., 0.15 / 0.35 / 0.65) are not in the spec. The values in the Code Examples section above are reasonable defaults.
   - Recommendation: Use 0.15 / 0.35 / 0.65 as the initial implementation. These produce visually distinct light/medium/strong signals. The planner can adjust if the user has a preference.

2. **Sign convention for crosswind_component return value**
   - What we know: The return value sign (positive = right crosswind, negative = left crosswind) matters for future phases but not for Phase 1's `classify_wind()` which only uses absolute value.
   - What's unclear: Whether future phases will use the sign (e.g., for directional arrow rendering).
   - Recommendation: Define positive = wind from rider's right side (standard aviation convention). Document clearly in the docstring. The absolute value for classification is unaffected either way.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, no install needed) |
| Config file | none — run directly |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WIND-01 | crosswind_component returns signed float; pure crosswind = full speed; pure headwind direction = ~0; zero wind = 0; 180-degree inversion applied | unit | `python3 -m pytest tests/test_weather.py::TestCrosswindComponent -x` | Wave 0 |
| WIND-02 | classify_wind returns 'headwind' when abs(hw) > abs(cw); 'tailwind' when negative dominant; 'crosswind' otherwise; exact 45-degree boundary → crosswind | unit | `python3 -m pytest tests/test_weather.py::TestClassifyWind -x` | Wave 0 |
| WIND-03 | wind_cell_style returns correct hex color per wind_type; background is rgba string; opacity correct per speed band | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | Wave 0 |
| WIND-04 | wind_cell_style returns correct font_size string per speed band; includes 'rem' unit | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | Wave 0 |
| WIND-10 | HEAVY_WIND_MAX_KMH == 30; HEAVY_WIND_AVG_HEADWIND_KMH == 15; both importable from services.weather | unit | `python3 -m pytest tests/test_weather.py::TestWindConstants -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_weather.py` — add `TestCrosswindComponent`, `TestClassifyWind`, `TestWindCellStyle`, `TestWindConstants` classes (file exists; these test classes do not yet exist)

*(The test file infrastructure exists. Only the new test classes for Phase 1 functions are missing.)*

## Sources

### Primary (HIGH confidence)
- `services/weather.py` (direct read, lines 1-269) — confirmed existing function signatures, constants block location, import pattern, `headwind_component()` implementation including 180-degree inversion
- `tests/test_weather.py` (direct read, lines 1-409) — confirmed test class structure, import pattern, assertion style
- `.planning/REQUIREMENTS.md` (direct read) — WIND-01 through WIND-04, WIND-10 specifications including exact hex colors, font sizes, threshold values
- `.planning/research/SUMMARY.md` (direct read) — confirmed inline-styles decision, numpy rejection, no-new-service-files convention, Python math stdlib sufficiency
- `.planning/STATE.md` (direct read) — confirmed inline styles architectural decision, threshold constant centralization decision
- `.planning/config.json` (direct read) — confirmed nyquist_validation: true

### Secondary (MEDIUM confidence)
- `.planning/research/SUMMARY.md` Pitfall 3 — "wind from" inversion confirmed as critical; verified independently by reading `headwind_component()` source

### Tertiary (LOW confidence)
- None for this phase.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified directly from existing codebase; all functions use stdlib math already present
- Architecture: HIGH — no ambiguity; three functions added to one existing file using the exact pattern of adjacent functions
- Pitfalls: HIGH — 180-degree inversion pitfall verified by reading production code; other pitfalls are mechanical (type, scope, boundary)

**Research date:** 2026-03-23
**Valid until:** Stable — pure math functions; no external dependency; valid until requirements change
