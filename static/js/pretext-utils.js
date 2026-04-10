/**
 * PretextUtils — shared utility layer for pretext.js integration.
 * Provides helpers for font detection, card grid balancing,
 * smart text truncation, and nav overflow measurement.
 *
 * Requires: static/js/pretext.js (window.Pretext)
 */
(function() {
    'use strict';

    if (typeof Pretext === 'undefined') {
        console.warn('PretextUtils: Pretext library not loaded');
        window.PretextUtils = {};
        return;
    }

    /**
     * Extract a CSS font string from a DOM element via getComputedStyle.
     * Returns a string like '14.4px "Segoe UI", system-ui, sans-serif'.
     */
    function getFontString(el) {
        var style = getComputedStyle(el);
        return style.font || (style.fontSize + ' ' + style.fontFamily);
    }

    /**
     * Get line-height in pixels from a DOM element.
     * Falls back to fontSize * 1.2 if 'normal'.
     */
    function getLineHeightPx(el) {
        var style = getComputedStyle(el);
        var lh = parseFloat(style.lineHeight);
        if (isNaN(lh)) {
            // 'normal' — approximate with font-size * 1.2
            return parseFloat(style.fontSize) * 1.2;
        }
        return lh;
    }

    /**
     * Measure text using Pretext, deriving font from a reference DOM element.
     * @param {string} text — the text to measure
     * @param {Element} el — a reference DOM element for font/line-height
     * @param {number} containerWidth — available width in px
     * @returns {{ height: number, lineCount: number }}
     */
    function measure(text, el, containerWidth) {
        var font = getFontString(el);
        var lineHeight = getLineHeightPx(el);
        var handle = Pretext.prepare(text, font);
        return Pretext.layout(handle, containerWidth, lineHeight);
    }

    /**
     * Balance a card grid: measure title text in each card and set uniform
     * min-height on the title elements so grid rows align.
     *
     * @param {string} cardSelector — CSS selector for card elements
     * @param {string} titleSelector — CSS selector for the title inside each card
     */
    function balanceCardGrid(cardSelector, titleSelector) {
        var cards = document.querySelectorAll(cardSelector);
        if (cards.length < 2) return;

        var titleEls = [];
        var maxHeight = 0;

        for (var i = 0; i < cards.length; i++) {
            var title = cards[i].querySelector(titleSelector);
            if (!title) continue;

            var text = title.textContent.trim();
            if (!text) continue;

            var font = getFontString(title);
            var lineHeight = getLineHeightPx(title);
            var containerWidth = title.offsetWidth || title.parentElement.offsetWidth;

            if (containerWidth <= 0) continue;

            var handle = Pretext.prepare(text, font);
            var result = Pretext.layout(handle, containerWidth, lineHeight);

            titleEls.push({ el: title, height: result.height });
            if (result.height > maxHeight) maxHeight = result.height;
        }

        // Apply uniform min-height
        for (var j = 0; j < titleEls.length; j++) {
            titleEls[j].el.style.minHeight = Math.ceil(maxHeight) + 'px';
        }
    }

    /**
     * Smart truncate: find the longest prefix that fits within maxWidth
     * on a single line, truncating at word boundaries.
     *
     * @param {string} text — full text
     * @param {string} font — CSS font string
     * @param {number} maxWidth — max width in px
     * @param {string} [ellipsis='…'] — suffix for truncated text
     * @returns {string} — truncated text with ellipsis, or original if it fits
     */
    function smartTruncate(text, font, maxWidth, ellipsis) {
        if (!ellipsis) ellipsis = '\u2026';
        var handle = Pretext.prepare(text, font);
        var naturalWidth = Pretext.measureNaturalWidth(handle);

        if (naturalWidth <= maxWidth) return text;

        // Binary search for the longest fitting prefix at a word boundary
        var words = text.split(/\s+/);
        var lo = 1;
        var hi = words.length;
        var best = '';

        while (lo <= hi) {
            var mid = Math.floor((lo + hi) / 2);
            var candidate = words.slice(0, mid).join(' ') + ellipsis;
            var candidateHandle = Pretext.prepare(candidate, font);
            var candidateWidth = Pretext.measureNaturalWidth(candidateHandle);

            if (candidateWidth <= maxWidth) {
                best = candidate;
                lo = mid + 1;
            } else {
                hi = mid - 1;
            }
        }

        return best || (text.charAt(0) + ellipsis);
    }

    /**
     * Measure nav items and add a 'nav-compact' class to items that would
     * overflow their container.
     *
     * @param {string} navSelector — CSS selector for nav link elements
     * @param {number} [threshold=0.95] — fraction of container width to trigger compact mode
     */
    function fitNavItems(navSelector, threshold) {
        if (!threshold) threshold = 0.95;
        var items = document.querySelectorAll(navSelector);
        if (items.length === 0) return;

        for (var i = 0; i < items.length; i++) {
            var el = items[i];
            var text = el.textContent.trim();
            if (!text) continue;

            var font = getFontString(el);
            var handle = Pretext.prepare(text, font);
            var naturalWidth = Pretext.measureNaturalWidth(handle);
            var containerWidth = el.offsetWidth || el.parentElement.offsetWidth;

            if (containerWidth > 0 && naturalWidth > containerWidth * threshold) {
                el.classList.add('nav-compact');
            } else {
                el.classList.remove('nav-compact');
            }
        }
    }

    /**
     * Auto-fit heading text: reduce font-size if the text would overflow
     * the container at the current font-size.
     *
     * @param {string} selector — CSS selector for heading elements
     * @param {number} [minScale=0.7] — minimum scale factor
     */
    function autoFitHeading(selector, minScale) {
        if (!minScale) minScale = 0.7;
        var headings = document.querySelectorAll(selector);

        for (var i = 0; i < headings.length; i++) {
            var el = headings[i];
            var text = el.textContent.trim();
            if (!text) continue;

            var font = getFontString(el);
            var handle = Pretext.prepare(text, font);
            var naturalWidth = Pretext.measureNaturalWidth(handle);
            var containerWidth = el.parentElement.offsetWidth || el.offsetWidth;

            if (containerWidth > 0 && naturalWidth > containerWidth) {
                var scale = Math.max(minScale, containerWidth / naturalWidth);
                var currentSize = parseFloat(getComputedStyle(el).fontSize);
                el.style.fontSize = (currentSize * scale) + 'px';
            }
        }
    }

    /**
     * Set explicit min-height on flash messages for smooth CSS animations.
     *
     * @param {string} selector — CSS selector for flash message elements
     */
    function measureFlashMessages(selector) {
        var msgs = document.querySelectorAll(selector);
        for (var i = 0; i < msgs.length; i++) {
            var el = msgs[i];
            var text = el.textContent.trim();
            if (!text) continue;

            var font = getFontString(el);
            var lineHeight = getLineHeightPx(el);
            var containerWidth = el.offsetWidth;
            if (containerWidth <= 0) continue;

            var handle = Pretext.prepare(text, font);
            var result = Pretext.layout(handle, containerWidth, lineHeight);

            // Add padding (py-3 = 12px top + 12px bottom)
            var paddingV = parseFloat(getComputedStyle(el).paddingTop) +
                           parseFloat(getComputedStyle(el).paddingBottom);
            el.style.minHeight = Math.ceil(result.height + paddingV) + 'px';
        }
    }

    /**
     * Apply smart truncation to table cells containing text that may overflow.
     *
     * @param {string} cellSelector — CSS selector for table cells
     * @param {number} maxWidth — max width in px for the text
     */
    function truncateTableCells(cellSelector, maxWidth) {
        var cells = document.querySelectorAll(cellSelector);
        for (var i = 0; i < cells.length; i++) {
            var el = cells[i];
            // Get the text-bearing element (may be an <a> inside the <td>)
            var textEl = el.querySelector('a') || el;
            var text = textEl.textContent.trim();
            if (!text) continue;

            var font = getFontString(textEl);
            var handle = Pretext.prepare(text, font);
            var naturalWidth = Pretext.measureNaturalWidth(handle);

            if (naturalWidth > maxWidth) {
                var truncated = smartTruncate(text, font, maxWidth);
                textEl.setAttribute('title', text);
                textEl.textContent = truncated;
            }
        }
    }

    // Expose utilities globally
    window.PretextUtils = {
        getFontString: getFontString,
        getLineHeightPx: getLineHeightPx,
        measure: measure,
        balanceCardGrid: balanceCardGrid,
        smartTruncate: smartTruncate,
        fitNavItems: fitNavItems,
        autoFitHeading: autoFitHeading,
        measureFlashMessages: measureFlashMessages,
        truncateTableCells: truncateTableCells
    };
})();
