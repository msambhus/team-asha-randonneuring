"""Shared route and ride-plan operations status presentation contract."""


def route_plan_status(record):
    """Normalize product-specific pipeline counts for shared admin presentation."""
    source = dict(record or {})
    return {
        'upcoming_events': int(source.get('upcoming_events') or 0),
        'missing_routes': int(source.get('missing_routes') or 0),
        'routes_missing_plans': int(source.get('routes_missing_plans') or 0),
        'plans_ready': int(source.get('plans_ready') or 0),
    }
