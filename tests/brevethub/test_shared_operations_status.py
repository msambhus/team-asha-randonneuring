from pathlib import Path

from shared.operations_status import route_plan_status


ROOT = Path(__file__).resolve().parents[2]


def test_brevethub_vendored_operations_status_matches_shared_source():
    assert (
        ROOT.joinpath('shared/operations_status.py').read_bytes()
        == ROOT.joinpath('brevethub/shared/operations_status.py').read_bytes()
    )


def test_route_plan_status_normalizes_missing_and_database_values():
    assert route_plan_status(None) == {
        'upcoming_events': 0,
        'missing_routes': 0,
        'routes_missing_plans': 0,
        'plans_ready': 0,
    }
    assert route_plan_status({
        'upcoming_events': 12,
        'missing_routes': '3',
        'routes_missing_plans': 2,
        'plans_ready': 7,
    }) == {
        'upcoming_events': 12,
        'missing_routes': 3,
        'routes_missing_plans': 2,
        'plans_ready': 7,
    }
