"""Self-hosted FIT file merge.

Reads two or more activity ``.fit`` files, merges them in one of two modes, and
re-serializes a single valid FIT file that Garmin Connect / Strava accept — with
**no donor gate** (the reason this exists as a self-hosted replacement for
GoToes' merge-gps-files, whose FIT output is paywalled).

Design: a ``normalize -> merge -> serialize`` spine.

* ``read_fit``  — decode a FIT file into a :class:`ParsedActivity`, copying the
  **entire** field map of every ``record`` message (not a hand-picked subset) so
  nothing is silently dropped on the way through.
* ``concat_activities``  — stitch files end-to-end in chronological order (a
  dead-battery handoff), keeping cumulative distance continuous across the seam
  and preserving per-record timestamps.
* ``overlay_activities`` — interleave overlapping recordings time-aligned by
  timestamp, using ONE file as the position source so two near-identical GPS
  tracks do not double-count distance.
* ``reconstruct_speed_distance`` — fill missing speed/distance from position and
  time, never overwriting values the source already carried.
* ``write_fit`` — re-emit every carried field plus valid ``file_id`` / timer
  events / ``lap`` / ``session`` / ``activity`` messages, warning on any field it
  could not serialize rather than dropping it silently.

The module holds **no state** and imports no Flask: every merge lives only in the
caller's memory for the duration of one request. The heavy FIT libraries
(``fitdecode`` for reading, ``fit_tool`` for writing) are imported lazily inside
the read/write functions so importing this module never requires them.
"""
import io
import math
import logging
from dataclasses import dataclass, field
from datetime import timezone

logger = logging.getLogger(__name__)

# User-selectable merge modes (the value the route validates against).
MERGE_MODES = ('concat', 'overlay')

# Fields owned by the position source in overlay mode. Auxiliary files never
# contribute these — that is what stops two overlapping GPS tracks from
# double-counting distance. Everything else (heart_rate, cadence, power,
# temperature, vertical_oscillation, ...) is fair game to overlay.
POSITIONAL_FIELDS = frozenset({
    'position_lat', 'position_long', 'distance',
    'speed', 'enhanced_speed', 'altitude', 'enhanced_altitude',
    'gps_accuracy', 'grade', 'vertical_speed',
})

# Overlay alignment window: an auxiliary record is matched to the nearest
# position-source record within this many seconds, else it is ignored.
OVERLAY_TOLERANCE_S = 3.0

# Semicircle <-> degree conversion (FIT position encoding: int32 semicircles).
_SEMICIRCLE_SCALE = 180.0 / (2 ** 31)
_EARTH_RADIUS_M = 6371000.0


class FitMergeError(Exception):
    """Raised for empty, truncated, non-FIT, or otherwise unmergeable input."""


@dataclass
class Record:
    """One ``record`` message: a UTC timestamp plus its full field map.

    ``fields`` carries every standard field the source record had (position,
    altitude, speed, distance, heart_rate, cadence, power, temperature, and any
    others such as enhanced_* / grade / gps_accuracy / vertical_oscillation).
    ``dev_fields`` holds this record's developer/custom field values keyed by
    field name; they are re-emitted best-effort by :func:`write_fit` against the
    captured definitions, and only a *specific* field the writer cannot serialize
    is warned about — never silently lost.
    """
    timestamp: object                     # timezone-aware datetime (UTC)
    fields: dict = field(default_factory=dict)
    dev_fields: dict = field(default_factory=dict)


@dataclass
class ParsedActivity:
    """A decoded activity: its records plus enough header context to re-emit.

    ``developer_data_ids`` and ``field_descriptions`` are the captured developer
    profile — ``developer_data_id`` and ``field_description`` definition messages
    (as plain field dicts) — carried through so the writer can re-declare them and
    re-emit each record's developer values. ``dev_field_names`` is the convenience
    set of developer field names seen (derivable from ``field_descriptions``).
    """
    records: list = field(default_factory=list)
    sport: object = 'cycling'
    dev_field_names: list = field(default_factory=list)
    developer_data_ids: list = field(default_factory=list)
    field_descriptions: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def semicircles_to_degrees(value):
    """Convert FIT semicircles to degrees."""
    return value * _SEMICIRCLE_SCALE


def degrees_to_semicircles(value):
    """Convert degrees to FIT semicircles (int32)."""
    return int(round(value / _SEMICIRCLE_SCALE))


def _as_utc(dt):
    """Normalize a (possibly naive) FIT datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_millis(dt):
    """Milliseconds since the Unix epoch for a datetime (what fit_tool wants)."""
    return int(round(_as_utc(dt).timestamp() * 1000))


def _haversine(lat1, lng1, lat2, lng2):
    """Great-circle distance in meters between two lat/lng degree pairs."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _segment_distance(a, b):
    """Positional distance in meters between two records, 0 if either lacks GPS."""
    alat, alng = a.fields.get('position_lat'), a.fields.get('position_long')
    blat, blng = b.fields.get('position_lat'), b.fields.get('position_long')
    if None in (alat, alng, blat, blng):
        return 0.0
    return _haversine(alat, alng, blat, blng)


def _clone(rec):
    """Deep-enough copy so merges never mutate the caller's parsed records."""
    return Record(timestamp=rec.timestamp,
                  fields=dict(rec.fields),
                  dev_fields=dict(rec.dev_fields))


def _nearest_index(sorted_times, target, tolerance_s):
    """Index in ``sorted_times`` closest to ``target`` within tolerance, else None."""
    import bisect
    if not sorted_times:
        return None
    pos = bisect.bisect_left(sorted_times, target)
    best_idx, best_delta = None, None
    for cand in (pos - 1, pos, pos + 1):
        if 0 <= cand < len(sorted_times):
            delta = abs((sorted_times[cand] - target).total_seconds())
            if best_delta is None or delta < best_delta:
                best_idx, best_delta = cand, delta
    if best_idx is None or best_delta > tolerance_s:
        return None
    return best_idx


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def _is_dev_field(fld):
    """True if a fitdecode field is a developer/custom field (not standard)."""
    fdef = getattr(fld, 'field_def', None)
    return fdef is not None and hasattr(fdef, 'dev_data_index')


def _frame_field_dict(frame):
    """Snapshot a fitdecode data frame's standard fields into a plain dict."""
    out = {}
    for fld in frame.fields:
        if fld.value is not None:
            out[fld.name] = fld.value
    return out


def read_fit(data):
    """Decode FIT bytes into a :class:`ParsedActivity`.

    Copies the complete field map of each ``record`` message plus the developer
    profile — ``developer_data_id`` / ``field_description`` definitions and each
    record's developer field values — so the writer can re-emit them. Raises
    :class:`FitMergeError` on empty, truncated, or unparseable input, or when the
    file carries no ``record`` messages.
    """
    if not data or len(data) < 12:
        raise FitMergeError("file is empty or too small to be a FIT file")

    import fitdecode  # lazy: only needed when a merge actually runs

    records = []
    sport = None
    dev_field_names = set()
    developer_data_ids = []
    field_descriptions = []
    try:
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                if frame.name in ('sport', 'session') and sport is None:
                    sport = frame.get_value('sport', fallback=None)
                elif frame.name == 'developer_data_id':
                    developer_data_ids.append(_frame_field_dict(frame))
                elif frame.name == 'field_description':
                    field_descriptions.append(_frame_field_dict(frame))
                elif frame.name == 'record':
                    ts = frame.get_value('timestamp', fallback=None)
                    if ts is None:
                        continue
                    fields, devs = {}, {}
                    for fld in frame.fields:
                        if fld.name == 'timestamp' or fld.value is None:
                            continue
                        if _is_dev_field(fld):
                            devs[fld.name] = fld.value
                            dev_field_names.add(fld.name)
                        else:
                            val = fld.value
                            # fitdecode returns position_lat/long as raw int32
                            # semicircles; the IR is degrees (haversine and the
                            # fit_tool writer both expect degrees), so convert on
                            # read. Only raw semicircles are ints — a value already
                            # in degrees would be a float, so leave those alone.
                            if fld.name in ('position_lat', 'position_long') and isinstance(val, int):
                                val = semicircles_to_degrees(val)
                            fields[fld.name] = val
                    records.append(Record(timestamp=_as_utc(ts), fields=fields, dev_fields=devs))
    except FitMergeError:
        raise
    except Exception as exc:  # malformed FIT — fitdecode raises various errors
        raise FitMergeError(f"could not parse FIT file: {exc}") from exc

    if not records:
        raise FitMergeError("no GPS/record data found in FIT file")

    records.sort(key=lambda r: r.timestamp)
    return ParsedActivity(records=records, sport=sport or 'cycling',
                          dev_field_names=sorted(dev_field_names),
                          developer_data_ids=developer_data_ids,
                          field_descriptions=field_descriptions)


# --------------------------------------------------------------------------- #
# reconstruct
# --------------------------------------------------------------------------- #
def reconstruct_speed_distance(records):
    """Fill missing ``distance`` and ``speed`` from position + time, in place.

    Never overwrites a value the source already carried; keeps ``enhanced_speed``
    consistent with any reconstructed ``speed``. Returns the (timestamp-sorted)
    records so cumulative distance is monotonic.
    """
    recs = sorted(records, key=lambda r: r.timestamp)
    if not recs:
        return recs

    running = 0.0
    prev = None
    for r in recs:
        if prev is not None:
            running += _segment_distance(prev, r)
        existing = r.fields.get('distance')
        if existing is None:
            r.fields['distance'] = running
        else:
            # Resync to the authoritative value so any reconstructed neighbors
            # stay continuous with real distances instead of drifting.
            running = existing
        prev = r

    prev = None
    for r in recs:
        if r.fields.get('speed') is None and r.fields.get('enhanced_speed') is None:
            if prev is not None:
                dt = (r.timestamp - prev.timestamp).total_seconds()
                dd = (r.fields.get('distance') or 0.0) - (prev.fields.get('distance') or 0.0)
                if dt > 0 and dd >= 0:
                    speed = dd / dt
                    r.fields['speed'] = speed
                    r.fields.setdefault('enhanced_speed', speed)
        prev = r
    return recs


# --------------------------------------------------------------------------- #
# merge modes
# --------------------------------------------------------------------------- #
def concat_activities(activities):
    """Stitch activities end-to-end in chronological order.

    Each record keeps its **entire** field map and its original timestamp; only
    ``distance`` is offset by the running cumulative total so the merged distance
    is continuous and non-decreasing across every seam. The time gap between
    files is preserved as-is (timestamps are not shifted).
    """
    if not activities:
        raise FitMergeError("nothing to merge")

    ordered = sorted(activities, key=lambda a: a.records[0].timestamp)
    out_records = []
    offset = 0.0
    for act in ordered:
        recs = reconstruct_speed_distance([_clone(r) for r in act.records])
        local_last = 0.0
        for r in recs:
            local = r.fields.get('distance')
            if local is not None:
                r.fields['distance'] = local + offset
                local_last = local
            out_records.append(r)
        offset += local_last

    return ParsedActivity(records=out_records, sport=ordered[0].sport,
                          dev_field_names=_merged_dev_names(ordered),
                          developer_data_ids=_merged_developer_data_ids(ordered),
                          field_descriptions=_merged_field_descriptions(ordered))


def overlay_activities(activities, position_source_idx=0):
    """Interleave overlapping recordings, one file supplying the position track.

    The position source contributes position/altitude/distance/speed (the
    :data:`POSITIONAL_FIELDS` set); auxiliary files contribute any **non**-
    positional field the aligned source record lacks (heart_rate, cadence, power,
    temperature, and anything else they carry). Distance comes solely from the
    position source, so overlapping GPS tracks never double-count.
    """
    if not activities:
        raise FitMergeError("nothing to merge")
    if position_source_idx < 0 or position_source_idx >= len(activities):
        position_source_idx = 0

    source = activities[position_source_idx]
    aux = [a for i, a in enumerate(activities) if i != position_source_idx]

    out = reconstruct_speed_distance([_clone(r) for r in source.records])
    out_times = [r.timestamp for r in out]

    for a in aux:
        for ar in a.records:
            idx = _nearest_index(out_times, ar.timestamp, OVERLAY_TOLERANCE_S)
            if idx is None:
                continue
            target = out[idx]
            for name, val in ar.fields.items():
                if val is None or name in POSITIONAL_FIELDS:
                    continue
                if name not in target.fields:
                    target.fields[name] = val
            # Developer values are non-positional too: overlay any the aligned
            # source record lacks, so auxiliary custom sensors are preserved.
            for name, val in ar.dev_fields.items():
                if val is not None and name not in target.dev_fields:
                    target.dev_fields[name] = val

    return ParsedActivity(records=out, sport=source.sport,
                          dev_field_names=_merged_dev_names(activities),
                          developer_data_ids=_merged_developer_data_ids(activities),
                          field_descriptions=_merged_field_descriptions(activities))


def _merged_dev_names(activities):
    names = set()
    for a in activities:
        names.update(a.dev_field_names)
    return sorted(names)


def _merged_developer_data_ids(activities):
    """Union of developer_data_id definitions, deduped by developer_data_index."""
    seen, out = set(), []
    for a in activities:
        for did in a.developer_data_ids:
            key = did.get('developer_data_index')
            if key in seen:
                continue
            seen.add(key)
            out.append(did)
    return out


def _merged_field_descriptions(activities):
    """Union of field_description definitions, deduped by (dev index, field num)."""
    seen, out = set(), []
    for a in activities:
        for fd in a.field_descriptions:
            key = (fd.get('developer_data_index'), fd.get('field_definition_number'))
            if key in seen:
                continue
            seen.add(key)
            out.append(fd)
    return out


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def _sport_value(sport):
    """Coerce a sport (string / int / None) to a fit_tool Sport enum value."""
    from fit_tool.profile.profile_type import Sport
    if isinstance(sport, str):
        try:
            return Sport[sport.upper()].value
        except KeyError:
            return Sport.CYCLING.value
    if isinstance(sport, int):
        return sport
    return Sport.CYCLING.value


def write_fit(activity):
    """Serialize a :class:`ParsedActivity` to valid FIT bytes.

    Emits ``file_id``, the captured developer profile (``developer_data_id`` /
    ``field_description`` definitions), a timer start/stop event pair, every record
    with its full standard field map **and** its developer field values, and
    ``lap`` / ``session`` / ``activity`` summary messages so the file is accepted
    by Garmin Connect / Strava. Developer values are re-emitted best-effort against
    the captured definitions; any *specific* standard or developer field the writer
    cannot serialize is logged at WARNING by name, never dropped silently.
    """
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.messages.activity_message import ActivityMessage
    from fit_tool.profile.messages.event_message import EventMessage
    from fit_tool.profile.profile_type import (
        FileType, Manufacturer, Event, EventType, Activity,
    )

    records = activity.records
    if not records:
        raise FitMergeError("cannot write an activity with no records")

    start_ms = _to_millis(records[0].timestamp)
    end_ms = _to_millis(records[-1].timestamp)

    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY.value
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.serial_number = 0
    fid.time_created = start_ms
    builder.add(fid)

    # Re-declare the developer profile BEFORE any record that uses it (FIT
    # requires definitions to precede use). desc_by_name maps a developer field
    # name to its emitted field_description so per-record values can be attached.
    desc_by_name, dev_failures = _emit_developer_profile(builder, activity)

    start_evt = EventMessage()
    start_evt.event = Event.TIMER.value
    start_evt.event_type = EventType.START.value
    start_evt.timestamp = start_ms
    builder.add(start_evt)

    dropped = set()
    speeds, hrs, cadences, powers = [], [], [], []
    total_distance = 0.0
    record_msgs = []
    for rec in records:
        msg = RecordMessage()
        msg.timestamp = _to_millis(rec.timestamp)
        for name, val in rec.fields.items():
            if val is None:
                continue
            try:
                setattr(msg, name, val)
            except Exception:  # fit_tool has no property for this field / bad type
                dropped.add(name)

        _attach_developer_values(msg, rec, desc_by_name, dev_failures)

        dist = rec.fields.get('distance')
        if dist is not None:
            total_distance = max(total_distance, dist)
        spd = rec.fields.get('speed', rec.fields.get('enhanced_speed'))
        if spd is not None:
            speeds.append(spd)
        for bucket, key in ((hrs, 'heart_rate'), (cadences, 'cadence'), (powers, 'power')):
            v = rec.fields.get(key)
            if v is not None:
                bucket.append(v)
        record_msgs.append(msg)
    builder.add_all(record_msgs)

    stop_evt = EventMessage()
    stop_evt.event = Event.TIMER.value
    stop_evt.event_type = EventType.STOP_ALL.value
    stop_evt.timestamp = end_ms
    builder.add(stop_evt)

    elapsed = max(0.0, (end_ms - start_ms) / 1000.0)

    lap = LapMessage()
    lap.message_index = 0
    lap.timestamp = end_ms
    lap.start_time = start_ms
    lap.total_elapsed_time = elapsed
    lap.total_timer_time = elapsed
    lap.total_distance = total_distance
    _apply_stats(lap, speeds, hrs, cadences, powers)
    builder.add(lap)

    session = SessionMessage()
    session.message_index = 0
    session.timestamp = end_ms
    session.start_time = start_ms
    session.total_elapsed_time = elapsed
    session.total_timer_time = elapsed
    session.total_distance = total_distance
    session.sport = _sport_value(activity.sport)
    session.first_lap_index = 0
    session.num_laps = 1
    _apply_stats(session, speeds, hrs, cadences, powers)
    builder.add(session)

    act = ActivityMessage()
    act.timestamp = end_ms
    act.total_timer_time = elapsed
    act.num_sessions = 1
    act.type = Activity.MANUAL.value
    act.event = Event.ACTIVITY.value
    act.event_type = EventType.STOP.value
    builder.add(act)

    if dropped:
        logger.warning("write_fit: could not serialize record fields: %s", sorted(dropped))
    if dev_failures:
        # Honesty boundary: only fields that actually failed to round-trip are
        # reported — the rest were re-emitted with their captured definitions.
        logger.warning("write_fit: developer fields not re-emitted: %s", sorted(dev_failures))

    return bytes(builder.build().to_bytes())


def _emit_developer_profile(builder, activity):
    """Re-declare developer_data_id + field_description messages best-effort.

    Returns ``(desc_by_name, dev_failures)`` where ``desc_by_name`` maps a
    developer field name to its emitted ``FieldDescriptionMessage`` (for later
    per-record value attachment) and ``dev_failures`` collects the names of
    definitions that could not be re-emitted, so only those are warned about.
    """
    desc_by_name, dev_failures = {}, set()
    if not activity.developer_data_ids and not activity.field_descriptions:
        return desc_by_name, dev_failures

    try:
        from fit_tool.profile.messages.developer_data_id_message import (
            DeveloperDataIdMessage,
        )
        from fit_tool.profile.messages.field_description_message import (
            FieldDescriptionMessage,
        )
    except Exception:
        # fit_tool build lacks the developer-profile messages — every developer
        # field is a specific failure; name them all rather than lose them silently.
        dev_failures.update(activity.dev_field_names)
        return desc_by_name, dev_failures

    data_ids = activity.developer_data_ids
    if not data_ids and activity.field_descriptions:
        # Synthesize the developer_data_id(s) the field_descriptions reference.
        indexes = {fd.get('developer_data_index', 0) for fd in activity.field_descriptions}
        data_ids = [{'developer_data_index': idx} for idx in sorted(indexes)]

    for did in data_ids:
        try:
            msg = DeveloperDataIdMessage()
            _apply_fields(msg, did)
            builder.add(msg)
        except Exception:
            pass  # a missing id is surfaced per-field via desc lookups below

    for fd in activity.field_descriptions:
        name = fd.get('field_name')
        try:
            msg = FieldDescriptionMessage()
            _apply_fields(msg, fd)
            builder.add(msg)
            if name is not None:
                desc_by_name[name] = msg
        except Exception:
            if name is not None:
                dev_failures.add(name)
    return desc_by_name, dev_failures


def _attach_developer_values(msg, rec, desc_by_name, dev_failures):
    """Attach a record's developer field values to its RecordMessage best-effort.

    Each value is matched to its ``field_description`` by name and appended via
    fit_tool's developer-field API. A value with no definition, or that fit_tool
    cannot serialize, is added to ``dev_failures`` (warned by name) — never lost
    without a trace.
    """
    if not rec.dev_fields:
        return
    for name, value in rec.dev_fields.items():
        if value is None:
            continue
        desc = desc_by_name.get(name)
        if desc is None:
            dev_failures.add(name)
            continue
        try:
            _set_developer_field(msg, desc, value)
        except Exception:
            dev_failures.add(name)


def _set_developer_field(msg, description, value):
    """Append one developer field value to a message via fit_tool's API.

    Isolated so the exact fit_tool construction is the only thing a caller's
    try/except needs to guard.
    """
    from fit_tool.developer_field import DeveloperField
    from fit_tool.developer_field_definition import DeveloperFieldDefinition

    definition = DeveloperFieldDefinition(field_description_message=description)
    field = DeveloperField(definition, value)
    msg.developer_fields.append(field)


def _apply_fields(msg, values):
    """Best-effort setattr of a captured field dict onto a fit_tool message."""
    for key, val in values.items():
        if val is None:
            continue
        try:
            setattr(msg, key, val)
        except Exception:
            pass


def _apply_stats(msg, speeds, hrs, cadences, powers):
    """Best-effort avg/max summary fields on a lap/session message."""
    def _set(attr, value):
        try:
            setattr(msg, attr, value)
        except Exception:
            pass

    if speeds:
        _set('avg_speed', sum(speeds) / len(speeds))
        _set('max_speed', max(speeds))
    if hrs:
        _set('avg_heart_rate', int(round(sum(hrs) / len(hrs))))
        _set('max_heart_rate', int(max(hrs)))
    if cadences:
        _set('avg_cadence', int(round(sum(cadences) / len(cadences))))
        _set('max_cadence', int(max(cadences)))
    if powers:
        _set('avg_power', int(round(sum(powers) / len(powers))))
        _set('max_power', int(max(powers)))


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def merge_fit_files(blobs, mode='concat', position_source_idx=0):
    """Read every FIT blob, run the chosen merge, return serialized FIT bytes.

    ``blobs`` is a list of ``bytes``. Raises :class:`FitMergeError` on fewer than
    two files, an unknown mode, or any unparseable input.
    """
    if len(blobs) < 2:
        raise FitMergeError("at least two FIT files are required to merge")
    if mode not in MERGE_MODES:
        raise FitMergeError(f"unknown merge mode: {mode!r}")

    activities = [read_fit(b) for b in blobs]
    if mode == 'concat':
        merged = concat_activities(activities)
    else:
        merged = overlay_activities(activities, position_source_idx)
    return write_fit(merged)
