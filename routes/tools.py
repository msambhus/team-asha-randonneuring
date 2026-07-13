"""Tools routes: self-hosted FIT file merge (/tools/merge-fit).

A donor-gate-free replacement for GoToes' merge-gps-files FIT output. Uploads are
read into memory, merged, and streamed back as a download — nothing is written to
disk or the database, so no personal GPS data is ever persisted.

Upload cap: this blueprint raises the per-request body limit to
``FIT_MERGE_MAX_BYTES`` for its own routes only (via ``request.max_content_length``
in ``before_request``). The global ``MAX_CONTENT_LENGTH`` stays at 2 MB, so the
disk-writing rider-photo upload path it guards is not broadened by this feature.
"""
import io
import time
import logging

from flask import (
    Blueprint, render_template, request, send_file, current_app,
)

from services.fit_merge import merge_fit_files, FitMergeError, MERGE_MODES

logger = logging.getLogger(__name__)

tools_bp = Blueprint('tools', __name__)


@tools_bp.before_request
def _raise_upload_limit():
    """Scope the larger FIT upload cap to this blueprint; global stays 2 MB."""
    request.max_content_length = current_app.config['FIT_MERGE_MAX_BYTES']


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

    # Reject oversize by declared length before reading the body. Werkzeug's
    # per-request max_content_length (set above) is the backstop for clients
    # that omit Content-Length.
    if request.content_length and request.content_length > max_bytes:
        return _render_form(
            error=f"Upload is too large. Keep the total under {max_bytes // (1024 * 1024)} MB.",
            status=413)

    mode = _normalize_mode(request.form.get('mode'))
    if mode not in MERGE_MODES:
        return _render_form(error="Please choose a valid merge mode.", status=400)

    uploads = [f for f in request.files.getlist('files') if f and f.filename]
    if len(uploads) < 2:
        return _render_form(error="Select at least two .fit files to merge.", status=400)
    if len(uploads) > max_files:
        return _render_form(
            error=f"Too many files — you can merge up to {max_files} at once.",
            status=400)

    blobs, total = [], 0
    for f in uploads:
        data = f.read()
        total += len(data)
        blobs.append(data)
    if total == 0:
        return _render_form(error="The uploaded files are empty.", status=400)
    if total > max_bytes:
        return _render_form(
            error=f"Upload is too large. Keep the total under {max_bytes // (1024 * 1024)} MB.",
            status=413)

    try:
        merged = merge_fit_files(blobs, mode=mode)
    except FitMergeError as exc:
        return _render_form(error=f"Could not merge those files: {exc}", status=400)

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
