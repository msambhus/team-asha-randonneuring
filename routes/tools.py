"""Tools routes: self-hosted FIT file merge (/tools/merge-fit).

A donor-gate-free replacement for GoToes' merge-gps-files FIT output. Uploads are
read into memory, merged, and streamed back as a download — nothing is written to
disk or the database, so no personal GPS data is ever persisted.

Upload cap: this route accepts a larger body than the rest of the app WITHOUT
touching the global ``MAX_CONTENT_LENGTH`` (which stays 2 MB, so the disk-writing
rider-photo upload path it guards is not broadened). Flask's per-request
``request.max_content_length`` setter only exists on Flask >= 3.1, and this app
pins Flask 3.0 / Werkzeug 3.0 — so instead of accessing ``request.files`` (which
would be capped at the global 2 MB), the POST parses the multipart body directly
with :func:`werkzeug.formparser.parse_form_data`, passing a route-local
``max_content_length`` of ``FIT_MERGE_MAX_BYTES``. A custom in-memory stream
factory keeps every uploaded part in RAM (never spooled to a temp file).
"""
import io
import time
import logging

from flask import (
    Blueprint, render_template, request, send_file, current_app, jsonify,
)
from werkzeug.formparser import parse_form_data
from werkzeug.exceptions import RequestEntityTooLarge

from services.fit_merge import merge_fit_files, FitMergeError, MERGE_MODES

logger = logging.getLogger(__name__)

tools_bp = Blueprint('tools', __name__)


def _memory_stream_factory(total_content_length, content_type, filename=None,
                           content_length=None):
    """Buffer every uploaded part in memory so nothing is written to disk."""
    return io.BytesIO()


def _normalize_mode(raw):
    """Map the form value to a canonical merge mode ('concat' / 'overlay')."""
    value = (raw or 'concat').strip().lower()
    if value.startswith('concat'):
        return 'concat'
    if value.startswith('overlay'):
        return 'overlay'
    return value


def _render_form(error=None, status=200):
    max_files = current_app.config['FIT_MERGE_MAX_FILES']
    max_bytes = current_app.config['FIT_MERGE_MAX_BYTES']
    html = render_template('merge_fit.html', error=error,
                           max_files=max_files,
                           max_mb=max_bytes // (1024 * 1024))
    return (html, status) if status != 200 else html


def _wants_json():
    """True when the request came from the page's AJAX submit (so errors should be
    JSON the script can show inline, not a re-rendered HTML page)."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _fail(error, status):
    """Return a merge error as JSON (AJAX submit) or a re-rendered form (no-JS)."""
    if _wants_json():
        return jsonify({'error': error}), status
    return _render_form(error=error, status=status)


@tools_bp.route('/merge-fit', methods=['GET'])
def merge_fit_page():
    """Render the upload form with the mode selector."""
    return _render_form()


@tools_bp.route('/merge-fit', methods=['POST'])
def merge_fit_submit():
    """Merge 2–N uploaded .fit files and return one merged.fit download."""
    t0 = time.time()
    max_files = current_app.config['FIT_MERGE_MAX_FILES']
    max_bytes = current_app.config['FIT_MERGE_MAX_BYTES']

    # Parse the multipart body with a route-local cap instead of touching
    # request.files (which Flask 3.0 hard-caps at the global 2 MB). parse_form_data
    # raises RequestEntityTooLarge up front when Content-Length exceeds max_bytes.
    try:
        _stream, form, files = parse_form_data(
            request.environ,
            stream_factory=_memory_stream_factory,
            max_content_length=max_bytes,
        )
    except RequestEntityTooLarge:
        return _fail(
            error=f"Upload is too large. Keep the total under {max_bytes // (1024 * 1024)} MB.",
            status=413)

    mode = _normalize_mode(form.get('mode'))
    if mode not in MERGE_MODES:
        return _fail(error="Please choose a valid merge mode.", status=400)

    uploads = [f for f in files.getlist('files') if f and f.filename]
    if len(uploads) < 2:
        return _fail(error="Select at least two .fit files to merge.", status=400)
    if len(uploads) > max_files:
        return _fail(
            error=f"Too many files — you can merge up to {max_files} at once.",
            status=400)

    blobs, total = [], 0
    for f in uploads:
        data = f.read()
        total += len(data)
        blobs.append(data)
    if total == 0:
        return _fail(error="The uploaded files are empty.", status=400)

    try:
        merged = merge_fit_files(blobs, mode=mode)
    except FitMergeError as exc:
        return _fail(error=f"Could not merge those files: {exc}", status=400)

    # No PII: mode, counts, and byte totals only — never filenames or positions.
    logger.info("merge-fit mode=%s files=%d in_bytes=%d out_bytes=%d elapsed=%.2fs",
                mode, len(uploads), total, len(merged), time.time() - t0)

    response = send_file(
        io.BytesIO(merged),
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name='merged.fit',
    )
    response.headers['Cache-Control'] = 'no-store'
    return response
