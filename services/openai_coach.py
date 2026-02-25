"""OpenAI-powered coaching advice for upcoming rides.

Calls gpt-4o-mini with rider training data and upcoming ride details
to generate personalized, concise coaching advice per ride.
Falls back to rule-based generate_training_advice() if OpenAI is unavailable.
"""
import os
import json
import hashlib
import time
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache  (rider_id + data fingerprint → advice dict, 6-hour TTL)
# ---------------------------------------------------------------------------
_cache = {}
_CACHE_TTL = 6 * 3600  # 6 hours


def _cache_key(rider_id, activities, signups):
    """Deterministic cache key from rider + data fingerprint."""
    act_sig = '|'.join(
        f"{a.get('strava_activity_id', '')}-{a.get('start_date_local', '')}"
        for a in (activities or [])[:30]
    )
    signup_sig = '|'.join(
        f"{s.get('id', '')}-{s.get('signup_status', '')}"
        for s in (signups or [])
    )
    raw = f"{rider_id}:{act_sig}:{signup_sig}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key):
    if key in _cache:
        ts, result = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return result
        del _cache[key]
    return None


def _set_cache(key, result):
    if len(_cache) > 200:
        sorted_keys = sorted(_cache, key=lambda k: _cache[k][0])
        for k in sorted_keys[:50]:
            del _cache[k]
    _cache[key] = (time.time(), result)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a trained professional endurance bicycling coach specializing in \
randonneuring (long-distance unsupported cycling). You are advising a rider \
from Team Asha, a South Asian randonneuring club based in the San Francisco \
Bay Area.

CONTEXT:
- The rider's eventual target is Paris-Brest-Paris (PBP) 2027 in August — \
a 1,200 km brevet with a 90-hour time limit. Every brevet this season is a \
stepping stone toward PBP qualification.
- Brevet cutoff times: 200 km = 13.5 hrs, 300 km = 20 hrs, 400 km = 27 hrs, \
600 km = 40 hrs, 1000 km = 75 hrs, 1200 km = 90 hrs.
- Assume the rider is Indian, aged 40-60, unless bio info states otherwise. \
Consider heat adaptation, nutrition habits (vegetarian-friendly fueling), and \
recovery needs for this demographic.
- Consider that Bay Area terrain includes steep kickers, headwinds along the \
coast, and significant elevation change. Factor this into difficulty assessment.

YOUR TASK:
For each upcoming ride listed, provide a coaching thought — 2-4 concise \
sentences. Be specific and actionable — reference actual numbers from their \
training data. Address the biggest gap first. If they are on track, affirm \
and suggest one refinement.

Previous upcoming rides (earlier dates) should be treated as training building \
blocks for later, harder rides. If a 300 km ride is before a 600 km ride, \
your advice for the 600 km should assume the 300 km will contribute to fitness.

TONE: Direct, encouraging, data-informed. No fluff. Think experienced coach \
talking to a committed amateur.

OUTPUT FORMAT:
Return a JSON object with ride IDs as keys and advice strings as values:
{"<ride_id>": "<2-4 sentence coaching advice>", ...}
Return ONLY valid JSON, no markdown fences, no extra text."""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
_CYCLING_TYPES = ('Ride', 'VirtualRide', 'EBikeRide')


def _build_training_summary(activities, fitness_score):
    """Condense Strava activities into a token-efficient text summary."""
    if not activities:
        return None

    rides = [a for a in activities if a.get('activity_type') in _CYCLING_TYPES]
    if not rides:
        return None

    total_km = sum((r.get('distance') or 0) / 1000 for r in rides)
    total_elev_m = sum(r.get('total_elevation_gain') or 0 for r in rides)
    total_hours = sum((r.get('moving_time') or 0) / 3600 for r in rides)
    longest_km = max((r.get('distance') or 0) / 1000 for r in rides)
    max_elev_m = max(r.get('total_elevation_gain') or 0 for r in rides)

    # HR stats
    hr_rides = [r for r in rides if r.get('has_heartrate') and r.get('average_heartrate')]
    hr_summary = ""
    if hr_rides:
        avg_hr = sum(r['average_heartrate'] for r in hr_rides) / len(hr_rides)
        max_hr = max(r.get('max_heartrate') or 0 for r in hr_rides)
        hr_summary = f"Avg HR: {avg_hr:.0f} bpm, Max HR observed: {max_hr:.0f} bpm. "

    # Power stats
    power_rides = [r for r in rides if r.get('device_watts') and r.get('weighted_average_watts')]
    power_summary = ""
    if power_rides:
        avg_np = sum(r['weighted_average_watts'] for r in power_rides) / len(power_rides)
        power_summary = f"Avg normalized power: {avg_np:.0f}W. "

    # Fitness score line
    fit_line = ""
    if fitness_score:
        fit_line = (
            f"Fitness score: {fitness_score['total']}/100 "
            f"(Freq {fitness_score['frequency']}/25, Vol {fitness_score['volume']}/35, "
            f"Int {fitness_score['intensity']}/25, Rec {fitness_score['recency']}/15). "
        )

    # Top 5 recent rides
    recent_lines = []
    for r in rides[:5]:
        d_km = (r.get('distance') or 0) / 1000
        e_m = r.get('total_elevation_gain') or 0
        t_h = (r.get('moving_time') or 0) / 3600
        dt = str(r.get('start_date_local', ''))[:10]
        line = f"  {dt}: {d_km:.0f}km, {e_m:.0f}m elev, {t_h:.1f}hrs"
        if r.get('average_heartrate'):
            line += f", HR {r['average_heartrate']:.0f}"
        if r.get('weighted_average_watts'):
            line += f", NP {r['weighted_average_watts']}W"
        if r.get('suffer_score'):
            line += f", suffer {r['suffer_score']}"
        recent_lines.append(line)

    weeks = 4
    summary = (
        f"STRAVA DATA (last {weeks} weeks):\n"
        f"{len(rides)} rides, {total_km:.0f} km total, "
        f"{total_elev_m:.0f} m elevation, {total_hours:.0f} hrs riding. "
        f"Longest ride: {longest_km:.0f} km. Max single-ride elevation: {max_elev_m:.0f} m. "
        f"Weekly avg: {total_km / weeks:.0f} km/wk. "
        f"{hr_summary}{power_summary}{fit_line}\n"
        f"Recent rides:\n" + "\n".join(recent_lines)
    )
    return summary


def _build_brevet_history_summary(season_data):
    """Build training signal from completed brevet history (for riders without Strava).

    Uses finish times, distances, and elevation from past brevets to give
    the coach a sense of the rider's endurance capability.
    """
    if not season_data:
        return None

    finished_rides = []
    for season in season_data:
        for p in season.get('participation', []):
            if p.get('status') == 'FINISHED':
                finished_rides.append(p)

    if not finished_rides:
        return None

    # Sort by date, most recent first
    finished_rides.sort(key=lambda r: r.get('date', ''), reverse=True)

    lines = []
    for r in finished_rides[:12]:  # Last 12 brevets max
        name = r.get('ride_name', 'Unknown')
        dist_km = r.get('distance_km') or 0
        elev_ft = r.get('elevation_ft') or 0
        finish_time = r.get('finish_time', '')
        ride_date = str(r.get('date', ''))[:10]
        line = f"  {ride_date}: {name} — {dist_km:.0f}km"
        if elev_ft:
            line += f", {elev_ft:,}ft elev"
        if finish_time:
            line += f", finished in {finish_time}"
        lines.append(line)

    total_km = sum(r.get('distance_km') or 0 for r in finished_rides)
    total_brevets = len(finished_rides)

    summary = (
        f"BREVET HISTORY (no Strava data — using finish records as training signal):\n"
        f"Total completed brevets: {total_brevets}, Total distance: {total_km:.0f} km.\n"
        f"Recent completions:\n" + "\n".join(lines)
    )
    return summary


def _build_user_prompt(rider, activities, fitness_score,
                       upcoming_rides_with_readiness, season_data):
    """Build the user message with all ride-specific data."""
    # Rider info
    rider_info = f"Rider: {rider.get('first_name', '')} {rider.get('last_name', '')}"
    if rider.get('bio'):
        rider_info += f"\nBio: {rider['bio']}"

    # Training data — prefer Strava, fall back to brevet history
    training = _build_training_summary(activities, fitness_score)
    if not training:
        training = _build_brevet_history_summary(season_data)
    if not training:
        training = "No training data or brevet history available."

    # Upcoming rides
    ride_blocks = []
    for ride_data in upcoming_rides_with_readiness:
        ride = ride_data['ride']
        readiness = ride_data.get('readiness')
        weeks_until = ride_data.get('weeks_until', 4)
        ride_id = ride.get('id')

        dist_km = ride.get('distance_km') or 0
        dist_mi = ride.get('distance_miles') or (dist_km * 0.621371 if dist_km else 0)
        elev_ft = ride.get('elevation_ft') or 0
        time_limit = ride.get('time_limit_hours') or ''
        status = ride_data.get('signup_status', 'GOING')

        block = (
            f"Ride ID {ride_id}: {ride.get('name', 'Unknown')}\n"
            f"  Date: {ride.get('date')}, Weeks away: {weeks_until}, Status: {status}\n"
            f"  Distance: {dist_km:.0f} km ({dist_mi:.0f} mi), "
            f"Elevation: {elev_ft:,} ft, Time limit: {time_limit} hrs"
        )

        if readiness:
            block += (
                f"\n  Readiness: {readiness['score']}/100 ({readiness['level']})"
                f" — Distance {readiness['distance']}/{readiness['distance_max']}, "
                f"Elevation {readiness['elevation']}/{readiness['elevation_max']}, "
                f"Volume {readiness['volume']}/{readiness['volume_max']}, "
                f"Fitness {readiness['fitness']}/{readiness['fitness_max']}"
                f"\n  Longest ride: {readiness.get('longest_km', 0):.0f} km, "
                f"Weekly avg: {readiness.get('actual_weekly_km', 0):.0f} km "
                f"(target: {readiness.get('target_weekly_km', 0):.0f} km)"
            )

        ride_blocks.append(block)

    rides_text = "\n\n".join(ride_blocks)

    return (
        f"{rider_info}\n\n"
        f"TRAINING DATA:\n{training}\n\n"
        f"UPCOMING RIDES:\n{rides_text}"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_openai_advice(rider, activities, fitness_score,
                           upcoming_rides_with_readiness, season_data=None):
    """Generate coaching advice for all upcoming rides in one OpenAI call.

    Args:
        rider: dict with first_name, last_name, bio, etc.
        activities: list of strava_activity dicts (last 28 days), may be empty
        fitness_score: dict from calculate_fitness_score() or None
        upcoming_rides_with_readiness: list of dicts, each containing:
            'ride': ride dict, 'readiness': readiness dict or None,
            'weeks_until': int, 'signup_status': str
        season_data: list of season dicts with participation (for brevet history fallback)

    Returns:
        dict mapping ride_id (int) -> advice_string (str).
        Returns empty dict on failure.
    """
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        logger.info("OPENAI_API_KEY not configured, skipping AI coaching")
        return {}

    if not upcoming_rides_with_readiness:
        return {}

    # Check cache
    signups = [r['ride'] for r in upcoming_rides_with_readiness]
    key = _cache_key(rider.get('id', 0), activities, signups)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    user_prompt = _build_user_prompt(
        rider, activities, fitness_score,
        upcoming_rides_with_readiness, season_data
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=600,
            timeout=10,
        )

        raw = response.choices[0].message.content.strip()

        # Handle potential markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result_raw = json.loads(raw)

        # Normalize keys to int ride IDs
        result = {}
        for k, v in result_raw.items():
            try:
                result[int(k)] = str(v)
            except (ValueError, TypeError):
                continue

        _set_cache(key, result)
        return result

    except ImportError:
        logger.warning("openai package not installed, falling back to rule-based advice")
        return {}
    except json.JSONDecodeError as e:
        logger.warning(f"OpenAI returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.warning(f"OpenAI coaching call failed: {e}")
        return {}
