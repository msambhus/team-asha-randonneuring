"""Tests for the self-hosted FIT merge tool (services/fit_merge.py + /tools/merge-fit).

All fixtures are SYNTHETIC — generated in-test with known values. No real personal
FIT files are committed or read. The FIT libraries are optional at import time, so
the suite skips cleanly where they are not installed and runs for real where they
are (encode/decode is exercised end-to-end, nothing is mocked).
"""
import io
import os
from datetime import datetime, timedelta, timezone

import pytest

# The merge tool depends on fitdecode (read) + fit_tool (write). Skip the whole
# module if either is missing so `pytest -q` stays green without them.
pytest.importorskip("fitdecode")
pytest.importorskip("fit_tool")

from services import fit_merge  # noqa: E402
from services.fit_merge import (  # noqa: E402
    Record, ParsedActivity, read_fit, write_fit,
    concat_activities, overlay_activities, reconstruct_speed_distance,
    merge_fit_files, FitMergeError,
)

_T0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# synthetic fixtures
# --------------------------------------------------------------------------- #
def _make_records(n, *, start=_T0, step_s=1, start_lat=37.0, start_lng=-122.0,
                  start_dist=0.0, dist_step=10.0, extra=None):
    """Build a list of Record objects with a known GPS track and distance."""
    recs = []
    for i in range(n):
        fields = {
            'position_lat': start_lat + i * 0.0005,
            'position_long': start_lng + i * 0.0005,
            'altitude': 100.0 + i,
            'distance': start_dist + i * dist_step,
            'speed': 5.0,
            'heart_rate': 120 + i,
            'cadence': 85,
            'power': 200 + i,
            'temperature': 22,
        }
        if extra:
            fields.update(extra(i))
        recs.append(Record(timestamp=start + timedelta(seconds=i * step_s), fields=fields))
    return recs


def _make_synthetic_fit(records, *, sport='cycling'):
    """Serialize a list of Record objects to valid FIT bytes via the writer."""
    act = ParsedActivity(records=sorted(records, key=lambda r: r.timestamp), sport=sport)
    return write_fit(act)


# --------------------------------------------------------------------------- #
# 1. concat ordering + distance continuity across the seam
# --------------------------------------------------------------------------- #
def test_concat_orders_and_keeps_distance_continuous():
    a = ParsedActivity(records=_make_records(5, start=_T0, start_dist=0.0, dist_step=10.0))
    # File B recorded an hour later, its own distance restarts at 0.
    b = ParsedActivity(records=_make_records(
        5, start=_T0 + timedelta(hours=1), start_lat=37.5, start_lng=-122.5,
        start_dist=0.0, dist_step=10.0))

    # Pass out of order to prove chronological sorting.
    merged = concat_activities([b, a])
    dists = [r.fields['distance'] for r in merged.records]
    times = [r.timestamp for r in merged.records]

    assert times == sorted(times)                       # chronological
    assert all(y >= x for x, y in zip(dists, dists[1:]))  # non-decreasing
    # A ends at 40 m; B's records are offset above that (continuous, no reset).
    assert dists[len(a.records)] >= dists[len(a.records) - 1]
    assert max(dists) >= 80.0                            # 40 (A) + 40 (B) offset


def test_concat_preserves_per_record_timestamps():
    a_recs = _make_records(3, start=_T0)
    b_recs = _make_records(3, start=_T0 + timedelta(hours=2))
    merged = concat_activities([ParsedActivity(records=a_recs),
                                ParsedActivity(records=b_recs)])
    merged_times = {r.timestamp for r in merged.records}
    for r in a_recs + b_recs:
        assert r.timestamp in merged_times


# --------------------------------------------------------------------------- #
# 2. overlay alignment + no distance double-count
# --------------------------------------------------------------------------- #
def test_overlay_uses_position_source_distance_and_adds_aux_fields():
    # Position source: full GPS + distance, no power.
    src = ParsedActivity(records=_make_records(
        10, start_dist=0.0, dist_step=100.0,
        extra=lambda i: {'power': None}))  # drop power from source
    for r in src.records:
        r.fields.pop('power', None)

    # Auxiliary: same timestamps, its OWN (different) distance track + power.
    aux_recs = _make_records(10, start_lat=37.001, start_lng=-122.001,
                             start_dist=0.0, dist_step=99.0)
    for i, r in enumerate(aux_recs):
        r.fields['power'] = 250 + i
    aux = ParsedActivity(records=aux_recs)

    merged = overlay_activities([src, aux], position_source_idx=0)
    dists = [r.fields['distance'] for r in merged.records]

    # Distance is the position source's alone — NOT src + aux.
    assert max(dists) == pytest.approx(900.0)   # 9 * 100 from source only
    # Aux power was overlaid onto the source track.
    assert any(r.fields.get('power') is not None for r in merged.records)


def test_overlay_does_not_copy_positional_field_from_aux():
    src_recs = _make_records(5, start_lat=37.0, start_lng=-122.0)
    src = ParsedActivity(records=src_recs)
    aux_recs = _make_records(5, start_lat=40.0, start_lng=-73.0)  # far-away track
    aux = ParsedActivity(records=aux_recs)

    merged = overlay_activities([src, aux], position_source_idx=0)
    # Positions stay the source's — the aux's New-York coords never leak in.
    assert all(abs(r.fields['position_lat'] - 37.0) < 1.0 for r in merged.records)


def test_overlay_generalizes_to_any_non_positional_field():
    src_recs = _make_records(4)
    for r in src_recs:
        r.fields.pop('vertical_oscillation', None)
    src = ParsedActivity(records=src_recs)
    aux_recs = _make_records(4, start_lat=99.0)  # positional differs, ignored
    for r in aux_recs:
        r.fields['vertical_oscillation'] = 85.0   # non-positional -> overlaid
    aux = ParsedActivity(records=aux_recs)

    merged = overlay_activities([src, aux], position_source_idx=0)
    assert any(r.fields.get('vertical_oscillation') == 85.0 for r in merged.records)
    # But the aux's positional field did not overwrite the source position.
    assert all(r.fields['position_lat'] < 90.0 for r in merged.records)


# --------------------------------------------------------------------------- #
# 3-6. field preservation + round-trip through real encode/decode
# --------------------------------------------------------------------------- #
_RICH_FIELDS = {
    'enhanced_speed': 6.25, 'enhanced_altitude': 150.0, 'grade': 3.5,
    'gps_accuracy': 4, 'vertical_oscillation': 90.0, 'left_right_balance': 51,
}


def test_full_field_preservation_round_trip():
    recs = _make_records(6, extra=lambda i: dict(_RICH_FIELDS))
    data = _make_synthetic_fit(recs)
    parsed = read_fit(data)

    # Every named + extra field survived decode within scale tolerance.
    first = parsed.records[0].fields
    for name in ('position_lat', 'position_long', 'altitude', 'distance', 'speed',
                 'heart_rate', 'cadence', 'power', 'temperature',
                 'enhanced_speed', 'enhanced_altitude', 'grade',
                 'gps_accuracy', 'vertical_oscillation', 'left_right_balance'):
        assert name in first, f"{name} was dropped"


def test_writer_emits_no_fewer_fields_than_received():
    recs = _make_records(3, extra=lambda i: dict(_RICH_FIELDS))
    sent = set(recs[0].fields)
    parsed = read_fit(_make_synthetic_fit(recs))
    got = set(parsed.records[0].fields)
    missing = sent - got
    assert not missing, f"writer silently dropped fields: {missing}"


def test_encode_decode_round_trip_equivalence():
    recs = _make_records(8)
    parsed = read_fit(_make_synthetic_fit(recs))
    assert len(parsed.records) == len(recs)
    for original, decoded in zip(recs, parsed.records):
        assert decoded.timestamp == original.timestamp
        assert decoded.fields['position_lat'] == pytest.approx(
            original.fields['position_lat'], abs=1e-4)
        assert decoded.fields['heart_rate'] == original.fields['heart_rate']


def test_merge_round_trip_via_public_api():
    a = _make_synthetic_fit(_make_records(5, start=_T0))
    b = _make_synthetic_fit(_make_records(5, start=_T0 + timedelta(hours=1)))
    out = merge_fit_files([a, b], mode='concat')
    parsed = read_fit(out)
    assert len(parsed.records) == 10
    dists = [r.fields['distance'] for r in parsed.records]
    assert all(y >= x for x, y in zip(dists, dists[1:]))


# --------------------------------------------------------------------------- #
# 4. developer-field handling (round-trips OR warns — never silent loss)
# --------------------------------------------------------------------------- #
def test_developer_field_is_never_silently_lost(caplog):
    """A file carrying a developer field must round-trip it OR warn naming it."""
    try:
        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.record_message import RecordMessage
        from fit_tool.profile.messages.developer_data_id_message import (
            DeveloperDataIdMessage,
        )
        from fit_tool.profile.messages.field_description_message import (
            FieldDescriptionMessage,
        )
        from fit_tool.profile.profile_type import FileType, Manufacturer, BaseType
    except Exception as exc:  # pragma: no cover - depends on fit_tool internals
        pytest.skip(f"fit_tool developer-field API unavailable: {exc}")

    try:
        builder = FitFileBuilder(auto_define=True, min_string_size=50)
        fid = FileIdMessage()
        fid.type = FileType.ACTIVITY.value
        fid.manufacturer = Manufacturer.DEVELOPMENT.value
        fid.product = 0
        fid.time_created = fit_merge._to_millis(_T0)
        builder.add(fid)

        dev_id = DeveloperDataIdMessage()
        dev_id.developer_data_index = 0
        builder.add(dev_id)

        desc = FieldDescriptionMessage()
        desc.developer_data_index = 0
        desc.field_definition_number = 0
        desc.fit_base_type_id = BaseType.UINT8.value
        desc.field_name = 'custom_metric'
        builder.add(desc)

        rec = RecordMessage()
        rec.timestamp = fit_merge._to_millis(_T0)
        rec.position_lat = 37.0
        rec.position_long = -122.0
        rec.distance = 0.0
        builder.add(rec)
        data = bytes(builder.build().to_bytes())
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"could not build a developer-field fixture: {exc}")

    # Read must capture the developer profile into the IR (definitions + values),
    # not merely note the field name.
    parsed = read_fit(data)
    assert parsed.field_descriptions, "field_description definition was dropped on read"
    assert any('custom_metric' in r.dev_fields for r in parsed.records), \
        "per-record developer value was dropped on read"

    # Write must re-emit best-effort: the value either round-trips through
    # decode->encode->decode OR write_fit warns naming that specific field.
    import logging
    with caplog.at_level(logging.WARNING, logger='services.fit_merge'):
        out = write_fit(parsed)
    reparsed = read_fit(out)

    round_tripped = any('custom_metric' in r.dev_fields for r in reparsed.records)
    warned = any('custom_metric' in rec.getMessage() for rec in caplog.records)
    assert round_tripped or warned, "developer field was silently lost"


# --------------------------------------------------------------------------- #
# 7. speed/distance reconstruction
# --------------------------------------------------------------------------- #
def test_reconstruct_fills_missing_speed_and_distance():
    recs = []
    for i in range(6):
        recs.append(Record(
            timestamp=_T0 + timedelta(seconds=i * 2),
            fields={'position_lat': 37.0 + i * 0.001, 'position_long': -122.0}))
    out = reconstruct_speed_distance(recs)
    dists = [r.fields['distance'] for r in out]
    assert dists[0] == 0.0
    assert all(y >= x for x, y in zip(dists, dists[1:]))   # monotonic
    assert all(y > x for x, y in zip(dists[:1], dists[1:2]))  # actually moved
    speeds = [r.fields.get('speed') for r in out[1:]]
    assert all(s is not None and s > 0 for s in speeds)


def test_reconstruct_never_overwrites_existing_values():
    recs = _make_records(4, dist_step=10.0)   # already has distance + speed
    original = [(r.fields['distance'], r.fields['speed']) for r in recs]
    reconstruct_speed_distance(recs)
    after = [(r.fields['distance'], r.fields['speed']) for r in recs]
    assert original == after


# --------------------------------------------------------------------------- #
# 8. malformed / empty / single-file input
# --------------------------------------------------------------------------- #
def test_read_fit_rejects_empty_input():
    with pytest.raises(FitMergeError):
        read_fit(b'')


def test_read_fit_rejects_garbage_input():
    with pytest.raises(FitMergeError):
        read_fit(b'this is definitely not a FIT file, at all, nope')


def test_merge_requires_at_least_two_files():
    one = _make_synthetic_fit(_make_records(3))
    with pytest.raises(FitMergeError):
        merge_fit_files([one], mode='concat')


def test_merge_rejects_unknown_mode():
    a = _make_synthetic_fit(_make_records(3))
    b = _make_synthetic_fit(_make_records(3))
    with pytest.raises(FitMergeError):
        merge_fit_files([a, b], mode='bogus')


# --------------------------------------------------------------------------- #
# route tests (9-11): validation, happy path, privacy, global-cap invariant
# --------------------------------------------------------------------------- #
def _upload(client, blobs, mode='concat', filename='ride.fit'):
    files = [(io.BytesIO(b), f"{i}_{filename}") for i, b in enumerate(blobs)]
    return client.post('/tools/merge-fit',
                       data={'mode': mode, 'files': files},
                       content_type='multipart/form-data')


def test_get_merge_fit_page_renders_form(client):
    resp = client.get('/tools/merge-fit')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="files"' in body
    assert 'value="concat"' in body
    assert 'value="overlay"' in body


def test_post_happy_path_returns_merged_fit(client):
    a = _make_synthetic_fit(_make_records(5, start=_T0))
    b = _make_synthetic_fit(_make_records(5, start=_T0 + timedelta(hours=1)))
    resp = _upload(client, [a, b], mode='concat')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/octet-stream'
    assert 'attachment' in resp.headers['Content-Disposition']
    assert 'merged.fit' in resp.headers['Content-Disposition']
    assert resp.headers.get('Cache-Control') == 'no-store'
    # The download parses back into a valid activity.
    parsed = read_fit(resp.get_data())
    assert len(parsed.records) == 10


def test_post_single_file_rejected(client):
    a = _make_synthetic_fit(_make_records(3))
    resp = _upload(client, [a])
    assert resp.status_code == 400


def test_ajax_error_returns_json(client):
    """An AJAX submit (X-Requested-With) gets a JSON error, not an HTML page, so the
    page's progress script can show it inline."""
    a = _make_synthetic_fit(_make_records(3))
    resp = client.post('/tools/merge-fit',
                       data={'mode': 'concat', 'files': [(io.BytesIO(a), '0_ride.fit')]},
                       content_type='multipart/form-data',
                       headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 400
    assert resp.mimetype == 'application/json'
    assert 'error' in resp.get_json()


def test_ajax_happy_path_still_returns_file(client):
    """A successful AJAX submit still streams the merged FIT (only errors are JSON)."""
    a = _make_synthetic_fit(_make_records(4, start=_T0))
    b = _make_synthetic_fit(_make_records(4, start=_T0 + timedelta(hours=1)))
    resp = client.post('/tools/merge-fit',
                       data={'mode': 'concat',
                             'files': [(io.BytesIO(a), '0.fit'), (io.BytesIO(b), '1.fit')]},
                       content_type='multipart/form-data',
                       headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 200
    assert resp.mimetype == 'application/octet-stream'
    assert len(read_fit(resp.get_data()).records) == 8


def test_post_malformed_file_rejected(client):
    resp = _upload(client, [b'garbage-1', b'garbage-2'])
    assert resp.status_code == 400


def test_post_too_many_files_rejected(client, app):
    app.config['FIT_MERGE_MAX_FILES'] = 2
    blobs = [b'x', b'y', b'z']
    resp = _upload(client, blobs)
    assert resp.status_code == 400
    assert b'up to 2' in resp.get_data()


def test_post_oversize_rejected(client, app):
    app.config['FIT_MERGE_MAX_BYTES'] = 512  # tiny cap for the test
    big = b'0' * 2048
    resp = _upload(client, [big, big])
    assert resp.status_code == 413


def test_global_content_length_cap_unchanged(app):
    """The rider-photo upload surface must not be broadened: global stays 2 MB."""
    assert app.config['MAX_CONTENT_LENGTH'] == 2 * 1024 * 1024


def test_post_accepts_body_larger_than_global_cap(client, app):
    """Route-local cap must let a >2 MB body through (not the global 2 MB).

    Guards the Flask-3.0 fix: request.max_content_length is not a per-request
    setter here, so the route parses the body with its own max_content_length.
    A ≈3 MB body is well over the global 2 MB but under the FIT cap — the request
    must reach the merge stage (rejected as malformed FIT, 400) rather than be
    turned away as too large (413).
    """
    assert app.config['MAX_CONTENT_LENGTH'] == 2 * 1024 * 1024
    assert app.config['FIT_MERGE_MAX_BYTES'] > 2 * 1024 * 1024
    big = b'0' * (1500 * 1024)  # two ~1.5 MB parts -> ~3 MB, > global, < FIT cap
    resp = _upload(client, [big, big])
    assert resp.status_code == 400   # accepted past 2 MB, then failed to parse
    assert resp.status_code != 413


def test_merge_writes_nothing_to_disk(client, app):
    """Privacy: a merge must not persist any file to the upload folder."""
    upload_dir = app.config['UPLOAD_FOLDER']
    before = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    a = _make_synthetic_fit(_make_records(4, start=_T0))
    b = _make_synthetic_fit(_make_records(4, start=_T0 + timedelta(hours=1)))
    resp = _upload(client, [a, b], mode='concat')
    assert resp.status_code == 200
    after = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    assert before == after
