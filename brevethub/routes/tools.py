"""BrevetHub's in-memory FIT merge tool."""
import io
import logging
import time

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.formparser import parse_form_data

from shared.fit_merge import FitMergeError, MERGE_MODES, merge_fit_files

tools_bp = Blueprint('tools', __name__)
logger = logging.getLogger(__name__)


def _memory_stream_factory(total_content_length, content_type, filename=None,
                           content_length=None):
    return io.BytesIO()


def _normalize_mode(raw):
    value = (raw or 'overlay').strip().lower()
    if value.startswith('concat'):
        return 'concat'
    if value.startswith('overlay'):
        return 'overlay'
    return value


def _render_form(error=None, status=200):
    max_files = current_app.config['FIT_MERGE_MAX_FILES']
    max_bytes = current_app.config['FIT_MERGE_MAX_BYTES']
    html = render_template('merge_fit.html', error=error, max_files=max_files,
                           max_mb=max_bytes // (1024 * 1024))
    return (html, status) if status != 200 else html


def _fail(message, status):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'error': message}), status
    return _render_form(message, status)


@tools_bp.get('/merge-fit')
def merge_fit_page():
    return _render_form()


@tools_bp.post('/merge-fit')
def merge_fit_submit():
    started = time.monotonic()
    max_files = current_app.config['FIT_MERGE_MAX_FILES']
    max_bytes = current_app.config['FIT_MERGE_MAX_BYTES']
    try:
        _stream, form, files = parse_form_data(
            request.environ,
            stream_factory=_memory_stream_factory,
            max_content_length=max_bytes,
        )
    except RequestEntityTooLarge:
        return _fail(f'Upload is too large. Keep the total under {max_bytes // (1024 * 1024)} MB.', 413)

    mode = _normalize_mode(form.get('mode'))
    if mode not in MERGE_MODES:
        return _fail('Please choose a valid merge mode.', 400)

    uploads = [upload for upload in files.getlist('files') if upload and upload.filename]
    if len(uploads) < 2:
        return _fail('Select at least two .fit files to merge.', 400)
    if len(uploads) > max_files:
        return _fail(f'Too many files - you can merge up to {max_files} at once.', 400)

    blobs = [upload.read() for upload in uploads]
    if not any(blobs):
        return _fail('The uploaded files are empty.', 400)
    try:
        merged = merge_fit_files(blobs, mode=mode)
    except FitMergeError as exc:
        return _fail(f'Could not merge those files: {exc}', 400)

    logger.info('brevethub merge-fit mode=%s files=%d in_bytes=%d out_bytes=%d elapsed=%.2fs',
                mode, len(blobs), sum(map(len, blobs)), len(merged),
                time.monotonic() - started)
    response = send_file(io.BytesIO(merged), mimetype='application/octet-stream',
                         as_attachment=True, download_name='merged.fit')
    response.headers['Cache-Control'] = 'no-store'
    return response
