document.addEventListener("DOMContentLoaded", function () {
    restoreSearchState();
    initFavoritesVisibility();
    refreshCycleStatus();
    loadFavorites();

    var filterBtn = document.getElementById('filter-button');
    var clearBtn  = document.getElementById('clear-button');
    if (filterBtn) filterBtn.addEventListener('click', applyFilters);
    if (clearBtn)  clearBtn.addEventListener('click', clearFilters);

    // Background poll so bell glow appears even if changes happened in another
    // tab (test routes, scheduled cycle, etc) without needing a manual refresh
    setInterval(backgroundAlertsPoll, 45000);

    // Also refresh immediately when this tab regains focus/visibility,
    // so switching back from Status or Series Mappings shows fresh state
    // without waiting for the next 45s poll
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') {
            backgroundAlertsPoll();
        }
    });
});

// Some browsers restore a page from the back-forward cache (bfcache) when
// navigating back to it via a normal link/back-button, rather than doing a
// real reload — in that case DOMContentLoaded never fires again, so
// _favsCache (and everything else set up above) would otherwise be frozen
// at whatever it was the moment you navigated away, e.g. showing "already
// in favorites" as stale/wrong in the Save Series modal after visiting
// Status or Series Mappings and coming back. pageshow with
// event.persisted fires reliably in exactly this case, so re-run the same
// refresh a fresh load would have done.
window.addEventListener('pageshow', function(event) {
    if (event.persisted) {
        loadFavorites();
        refreshCycleStatus();
    }
});

function backgroundAlertsPoll() {
    // Skip if the user is mid-edit on a favorite name or the manual-add input,
    // since loadAlertsStatus() can trigger a re-render that would interrupt typing
    var activeEl = document.activeElement;
    if (activeEl && (activeEl.classList.contains('fav-manual-input') || activeEl.classList.contains('mapping-edit-input'))) {
        return;
    }
    loadAlertsStatus();
}

function initializeFilters() {
    populateSelectFilters();
}

// ── Generic confirm / alert modal ───────────────────────────────────────
// Replaces native browser confirm()/alert() popups with an in-app modal
// matching the rest of the UI. Builds its own markup on demand, so no
// static HTML is needed in the page template. Styling lives in style.css
// (.app-modal-*) since it's shared across every page.
function showAppConfirm(message, onConfirm, options) {
    options = options || {};
    var okLabel    = options.okLabel || 'OK';
    var showCancel = options.showCancel !== false; // default true
    var danger     = options.danger || false;

    var overlay = document.createElement('div');
    overlay.className = 'app-modal-overlay';
    overlay.innerHTML =
        '<div class="app-modal-backdrop"></div>' +
        '<div class="app-modal-box">' +
            '<p class="app-modal-message"></p>' +
            '<div class="app-modal-actions">' +
                (showCancel ? '<button class="app-modal-btn-cancel">Cancel</button>' : '') +
                '<button class="app-modal-btn-confirm' + (danger ? ' app-modal-btn-danger' : '') + '">' + okLabel + '</button>' +
            '</div>' +
        '</div>';
    overlay.querySelector('.app-modal-message').textContent = message;

    function close() {
        overlay.remove();
        document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
        if (e.key === 'Escape') close();
    }

    overlay.querySelector('.app-modal-backdrop').onclick = close;
    var cancelBtn = overlay.querySelector('.app-modal-btn-cancel');
    if (cancelBtn) cancelBtn.onclick = close;
    overlay.querySelector('.app-modal-btn-confirm').onclick = function() {
        close();
        if (onConfirm) onConfirm();
    };

    document.addEventListener('keydown', onKeydown);
    document.body.appendChild(overlay);
}

function showAppAlert(message, onClose) {
    showAppConfirm(message, onClose, { showCancel: false });
}

// ── Developer panel ──────────────────────────────────────────────────
// Wraps the existing /alerts/test, /alerts/test_clear, /alerts/force_check,
// and /alerts/run_now routes so testing doesn't require typing URLs by hand.
function toggleDevPanel() {
    var panel = document.getElementById('dev-panel');
    if (!panel) return;
    panel.style.display = (panel.style.display === 'block') ? 'none' : 'block';
}

function devStatus(msg) {
    var el = document.getElementById('dev-panel-status');
    if (el) el.textContent = msg;
}

function devGetSeries() {
    var input = document.getElementById('dev-series-input');
    return input ? input.value.trim() : '';
}

function devInjectTest() {
    var series = devGetSeries();
    if (!series) { devStatus('Enter a series name first.'); return; }
    var count = document.getElementById('dev-count-input').value || 1;
    fetch('/alerts/test/' + encodeURIComponent(series) + '?count=' + encodeURIComponent(count))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            devStatus(data.message || 'Done.');
            loadAlertsStatus();
        })
        .catch(function() { devStatus('Request failed.'); });
}

function devClearTest() {
    var series = devGetSeries();
    if (!series) { devStatus('Enter a series name first.'); return; }
    fetch('/alerts/test_clear/' + encodeURIComponent(series))
        .then(function(r) { return r.json(); })
        .then(function() {
            devStatus('Cleared test notifications for "' + series + '".');
            loadAlertsStatus();
        })
        .catch(function() { devStatus('Request failed.'); });
}

function devForceCheck() {
    var series = devGetSeries();
    if (!series) { devStatus('Enter a series name first.'); return; }
    devStatus('Checking\u2026');

    // force_check runs outside the normal staggered cycle, so it wouldn't
    // otherwise trigger the favorites-header spinner. Show it manually for
    // the duration of this real check, then re-sync with the server's
    // actual cycle state afterward in case a real cycle is also running.
    var spinner = document.getElementById('favorites-header-spinner');
    if (spinner) {
        spinner.style.display = 'inline-block';
        spinner.title = 'Test check in progress\u2026';
    }

    fetch('/alerts/force_check/' + encodeURIComponent(series))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            devStatus(data.message || (data.success ? 'Done.' : 'Failed.'));
            loadAlertsStatus();
        })
        .catch(function() { devStatus('Request failed.'); })
        .finally(function() { refreshCycleStatus(); });
}

function devClearAllTest() {
    devStatus('Clearing\u2026');
    fetch('/alerts/test_clear_all')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            devStatus(data.message || 'Cleared all test notifications.');
            loadAlertsStatus();
        })
        .catch(function() { devStatus('Request failed.'); });
}

function devRunCycleNow() {
    devStatus('Starting cycle\u2026');
    fetch('/alerts/run_now', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            devStatus(data.message || (data.success ? ('Cycle started \u2014 ' + data.total + ' series queued.') : 'Failed.'));
            loadAlertsStatus();
            refreshCycleStatus();
        })
        .catch(function() { devStatus('Request failed.'); });
}

// ── Dev panel: fake search results ──────────────────────────────────
// Reuses the real appendBooks()/initLoadMore() flow. Load More afterward
// fetches real page-2 "new releases" results (empty query), so it's a
// genuine end-to-end test of the button, loading dots, and pagination.
function devInjectFakeResults() {
    var fakeBooks = [
        { title: 'The Wonderful Wizard of Oz - L. Frank Baum', link: '#', cover: '/static/images/default_cover.jpg', language: 'English', post_date: '01 Jan 2024', format: 'MP3', bitrate: '64kbps', file_size: '210.45 MBs' },
        { title: 'A Really Long Audiobook Title That Wraps Across Multiple Lines To Check The Layout - Some Author Name', link: '#', cover: '/static/images/default_cover.jpg', language: 'German', post_date: '15 Mar 2023', format: 'M4B', bitrate: '128kbps', file_size: '1.2 GBs' },
        { title: 'Dracula - Bram Stoker', link: '#', cover: '/static/images/default_cover.jpg', language: 'English', post_date: '22 Oct 2022', format: 'MP3', bitrate: '32kbps', file_size: '98.10 MBs' },
        { title: "Alice's Adventures in Wonderland - Lewis Carroll", link: '#', cover: '/static/images/default_cover.jpg', language: 'French', post_date: '03 Jul 2021', format: 'FLAC', bitrate: '320kbps', file_size: '540.00 MBs' },
        { title: 'Frankenstein - Mary Shelley', link: '#', cover: '/static/images/default_cover.jpg', language: 'English', post_date: '11 Nov 2020', format: 'MP3', bitrate: '64kbps', file_size: '180.75 MBs' }
    ];

    clearResults();
    appendBooks(fakeBooks);
    showClearBtn(true);
    showFilterBar(true);
    hideFavoritesPanel();
    initLoadMore('', 2);
    try { initializeFilters(); } catch (e) {}

    devStatus('Injected ' + fakeBooks.length + ' fake result(s). Load More will fetch real page-2 results.');
}

// ── Dev panel: error states ──────────────────────────────────────────
function devShowRateLimitError() {
    clearResults();
    showError('AudioBookBay has rate limited this IP. Try again later or route traffic through a VPN.');
    devStatus('Showing rate-limit error.');
}

// ── Dev panel: Download modal previews ───────────────────────────────
function devShowDownloadExists() {
    openDownloadModal('#', 'Series Title 5 - Fake Author');
    setTimeout(function() {
        var warning = document.getElementById('modal-exists-warning');
        var pathEl  = document.getElementById('modal-exists-path');
        if (warning && pathEl) {
            pathEl.textContent = '/data/media/books/audiobooks/Series Title/Series Title 4';
            warning.style.display = 'flex';
        }
    }, 400); // let the modal's real fetches settle first, then force it visible
    devStatus('Opened Download modal with a fake exists warning.');
}

function devShowDownloadKeywordMatch() {
    openDownloadModal('#', 'Series Title 5 - Fake Author');
    setTimeout(function() {
        var banner = document.getElementById('modal-keyword-info');
        var textEl = document.getElementById('modal-keyword-text');
        if (banner && textEl) {
            textEl.textContent = 'Series Title';
            banner.style.display = 'flex';
        }
    }, 400);
    devStatus('Opened Download modal with a fake keyword match banner.');
}

function devShowDownloadResult(success) {
    openDownloadModal('#', 'Series Title 5 - Fake Author');
    setTimeout(function() {
        document.getElementById('download-modal-form').style.display = 'none';
        var result = document.getElementById('download-modal-result');
        if (success) {
            result.innerHTML = '<div class="modal-result modal-result-success">'
                + '<div class="modal-result-icon">\u2713</div>'
                + '<p class="modal-result-title">Added to queue</p>'
                + '<p class="modal-result-sub">Series Title 5 - Fake Author</p>'
                + '</div>';
        } else {
            result.innerHTML = '<div class="modal-result modal-result-error">'
                + '<div class="modal-result-icon">\u2715</div>'
                + '<p class="modal-result-title">Failed to add</p>'
                + '<p class="modal-result-sub">Connection refused (fake test error)</p>'
                + '<button class="modal-btn-cancel" onclick="closeDownloadModal()" style="margin-top:14px;">Close</button>'
                + '</div>';
        }
        result.style.display = 'block';
    }, 400);
    devStatus('Opened Download modal with a fake ' + (success ? 'success' : 'failure') + ' result.');
}

// ── Dev panel: Save Series modal previews ────────────────────────────
function devShowSaveSeriesBanners() {
    openSaveSeriesModal('Series Title 5 - Fake Author', null);
    setTimeout(function() {
        document.getElementById('save-series-already-saved').style.display = 'flex';
        document.getElementById('save-series-disk-path').textContent = '/data/media/books/audiobooks/Series Title';
        document.getElementById('save-series-disk-found').style.display = 'flex';
        document.getElementById('save-series-mapping-text').textContent = 'Series Title 5 \u2192 Series Title';
        document.getElementById('save-series-mapping-info').style.display = 'flex';
    }, 400);
    devStatus('Opened Save Series modal with all three banners shown.');
}

function devShowSaveSeriesResult(success) {
    openSaveSeriesModal('Series Title 5 - Fake Author', null);
    setTimeout(function() {
        document.getElementById('save-series-modal-form').style.display = 'none';
        var result = document.getElementById('save-series-modal-result');
        if (success) {
            result.innerHTML = '<div class="modal-result modal-result-success">'
                + '<div class="modal-result-icon">\u2713</div>'
                + '<p class="modal-result-title">Series saved</p>'
                + '<p class="modal-result-sub">Series Title 5</p>'
                + '</div>';
        } else {
            result.innerHTML = '<div class="modal-result modal-result-error">'
                + '<div class="modal-result-icon">\u2715</div>'
                + '<p class="modal-result-title">Could not save</p>'
                + '<p class="modal-result-sub">Fake test error</p>'
                + '<button class="modal-btn-cancel" onclick="closeSaveSeriesModal()" style="margin-top:14px;">Close</button>'
                + '</div>';
        }
        result.style.display = 'block';
    }, 400);
    devStatus('Opened Save Series modal with a fake ' + (success ? 'success' : 'failure') + ' result.');
}

function populateSelectFilters() {
    const languages = new Set();
    const bitrates  = new Set();
    const formats   = new Set();

    document.querySelectorAll(".result-row").forEach(row => {
        languages.add(row.dataset.language);
        bitrates.add(row.dataset.bitrate);
        formats.add(row.dataset.format);
    });

    function repopulate(selectId, values) {
        const el = document.getElementById(selectId);
        const current = el.value;
        while (el.options.length > 1) el.remove(1);
        values.forEach(val => {
            if (val && val !== 'N/A') {
                const opt = document.createElement("option");
                opt.value = val;
                opt.textContent = val;
                el.appendChild(opt);
            }
        });
        el.value = current;
    }

    repopulate("language-filter", languages);
    repopulate("bitrate-filter",  bitrates);
    repopulate("format-filter",   formats);
}

// ── Apply / clear filters ─────────────────────────────────────────────────
function applyFilters() {
    const language = document.getElementById("language-filter").value;
    const bitrate  = document.getElementById("bitrate-filter").value;
    const format   = document.getElementById("format-filter").value;

    document.querySelectorAll(".result-row").forEach(row => {
        let visible = true;
        if (language && row.dataset.language !== language) visible = false;
        if (bitrate  && row.dataset.bitrate  !== bitrate)  visible = false;
        if (format   && row.dataset.format   !== format)   visible = false;
        row.style.display = visible ? "" : "none";
    });
}

function clearFilters() {
    document.getElementById("language-filter").value = "";
    document.getElementById("bitrate-filter").value  = "";
    document.getElementById("format-filter").value   = "";
    document.querySelectorAll(".result-row").forEach(row => row.style.display = "");
}

var _searchController = null; // AbortController for in-flight search
var SAVED_QUERY_KEY   = 'abb_search_query';
var SAVED_HTML_KEY    = 'abb_search_html';
var SKIP_SERIES_KEY   = 'abb_skip_series';

function startSearch() {
    var query = document.getElementById('search-input').value.trim();
    performSearch(query);
}

function performSearch(query) {
    // Cancel any in-flight search
    if (_searchController) { _searchController.abort(); }
    _searchController = new AbortController();

    // Clear previous results and state
    clearResults();
    sessionStorage.removeItem(SAVED_HTML_KEY);
    sessionStorage.removeItem(SAVED_QUERY_KEY);
    sessionStorage.removeItem('abb_next_page');

    // Update UI to searching state
    document.getElementById('search-input').value = query;
    setSearchingState(true);
    hideError();

    var pageLimit = parseInt(document.getElementById('page-data').dataset.pageLimit || '5');

    fetch('/search_more', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query, start_page: 1 }),
        signal: _searchController.signal
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        _searchController = null;
        setSearchingState(false);

        if (data.error) {
            showError(data.error);
            return;
        }

        if (!data.books || data.books.length === 0) {
            showError(query
                ? 'No results found for "' + query + '".'
                : 'No new releases found.');
            return;
        }

        appendBooks(data.books);
        showClearBtn(true);
        showFilterBar(true);
        hideFavoritesPanel();

        // Save state
        saveSearchState(query);

        // Init load more
        if (data.has_more !== false) {
            initLoadMore(query, 2);
        }

        // Init filters now that rows exist
        try { initializeFilters(); } catch(e) {}
    })
    .catch(function(err) {
        if (err.name === 'AbortError') return; // cancelled — do nothing
        _searchController = null;
        setSearchingState(false);
        showError('Search failed. Check your connection or try again.');
    });
}

function cancelSearch() {
    if (_searchController) {
        _searchController.abort();
        _searchController = null;
    }
    setSearchingState(false);
    // Leave page as-is (results or blank)
}

function setSearchingState(searching) {
    var searchBtn  = document.getElementById('search-btn');
    var cancelBtn  = document.getElementById('cancel-search-btn');
    var clearBtn   = document.getElementById('clear-search-btn');
    var spinner    = document.getElementById('button-spinner');
    var btnText    = searchBtn ? searchBtn.querySelector('.button-text') : null;

    if (searching) {
        if (searchBtn)  searchBtn.disabled = true;
        if (btnText)    btnText.style.display = 'none';
        if (spinner)    spinner.style.display = 'inline-block';
        if (cancelBtn)  cancelBtn.style.display = 'inline-flex';
        if (clearBtn)   clearBtn.style.display = 'none';
    } else {
        if (searchBtn)  searchBtn.disabled = false;
        if (btnText)    btnText.style.display = '';
        if (spinner)    spinner.style.display = 'none';
        if (cancelBtn)  cancelBtn.style.display = 'none';
        // Show clear only if results exist
        var tbody = document.getElementById('results-table-body');
        var hasResults = tbody && tbody.innerHTML.trim().length > 0;
        if (clearBtn) clearBtn.style.display = hasResults ? 'inline-flex' : 'none';
    }
}

function appendBooks(books) {
    var tbody = document.getElementById('results-table-body');
    books.forEach(function(book) {
        var tr = document.createElement('tr');
        tr.className = 'result-row';
        tr.dataset.language = book.language;
        tr.dataset.bitrate  = book.bitrate;
        tr.dataset.format   = book.format;
        tr.dataset.fileSize = book.file_size;
        tr.dataset.postDate = book.post_date;
        tr.dataset.link     = book.link;
        tr.dataset.title    = book.title;

        var alreadySaved = _isAlreadySaved(book.title);
        var coverSrc = book.cover || '/static/images/default_cover.jpg';
        tr.innerHTML =
            '<td><img src="' + escHtml(coverSrc) + '" alt="Cover Art" class="cover" width="100" '
                + 'onerror="this.src=\'/static/images/default_cover.jpg\'"></td>'
            + '<td>'
                + '<p class="book-title">' + escHtml(book.title) + '</p>'
                + '<div class="property-results-container">'
                    + '<span class="book-language">Language: ' + escHtml(book.language) + '</span>'
                    + '<span class="book-bitrate">Bitrate: '   + escHtml(book.bitrate)  + '</span>'
                    + '<span class="book-format">Format: '     + escHtml(book.format)   + '</span>'
                    + '<span class="book-file_size">File Size: '+ escHtml(book.file_size)+ '</span>'
                    + '<span class="book-post_date">Posted: '  + escHtml(book.post_date)+ '</span>'
                + '</div>'
            + '</td>'
            + '<td class="action-cell">'
                + '<button class="btn-download" onclick="handleDownload(this)">Download to Server</button>'
                + '<button class="fav-btn' + (alreadySaved ? ' fav-btn-saved' : '') + '" onclick="handleSaveSeries(this)">' + (alreadySaved ? 'Saved' : 'Save') + '</button>'
                + '<button class="btn-details"  onclick="handleDetails(this)">View on ABB</button>'
            + '</td>';
        tbody.appendChild(tr);
    });
}

// Client-side hint only — a simple substring check against the already-loaded
// favorites cache, not the full server-side fuzzy match. Good enough to show
// "Saved" instead of "Save" on a matching row; the modal still re-checks with
// the authoritative server value regardless of what this says.
function _isAlreadySaved(title) {
    var t = title.toLowerCase();
    return _favsCache.some(function(fav) { return t.indexOf(fav.toLowerCase()) !== -1; });
}

function clearResults() {
    var tbody = document.getElementById('results-table-body');
    if (tbody) tbody.innerHTML = '';
    var lmc = document.getElementById('load-more-container');
    if (lmc) lmc.style.display = 'none';
    var status = document.getElementById('load-more-status');
    if (status) { status.style.display = 'none'; status.textContent = ''; }
}

function clearSearch() {
    // Cancel any in-flight search
    if (_searchController) { _searchController.abort(); _searchController = null; }
    setSearchingState(false);

    // Clear results, state, and sessionStorage
    clearResults();
    sessionStorage.removeItem(SAVED_HTML_KEY);
    sessionStorage.removeItem(SAVED_QUERY_KEY);
    sessionStorage.removeItem('abb_next_page');

    document.getElementById('search-input').value = '';
    showClearBtn(false);
    showFilterBar(false);
    hideError();

    // Open favorites body
    var body  = document.getElementById('favorites-body');
    var arrow = document.getElementById('favorites-header-arrow');
    if (body)  body.style.display = 'block';
    if (arrow) arrow.style.transform = 'rotate(180deg)';
    loadFavorites();
}

function toggleSearchInfo() {
    var pop = document.getElementById('search-info-popover');
    if (!pop) return;
    pop.style.display = (pop.style.display === 'none') ? 'block' : 'none';
}

document.addEventListener('click', function(e) {
    var wrap = document.querySelector('.search-info-wrap');
    if (wrap && !wrap.contains(e.target)) {
        var pop = document.getElementById('search-info-popover');
        if (pop) pop.style.display = 'none';
    }
});

// Hover (matching the notification bell's exact 300ms/200ms open/close
// timing) sits alongside the click-toggle above rather than replacing it —
// there's no fully reliable way to know in advance whether a given device
// will actually hover, so both stay active. On a normal mouse-driven
// desktop this behaves identically to opening on hover; on a touch device,
// hover simply never fires and the click-toggle above just works on its
// own. The only overlap is a hybrid touchscreen laptop, where clicking
// something already open via hover just toggles it closed — harmless.
(function() {
    var btn = document.getElementById('search-info-btn');
    var pop = document.getElementById('search-info-popover');
    if (!btn || !pop) return;

    var hoverTimer = null;
    var leaveTimer = null;

    function openPop()  { clearTimeout(leaveTimer); pop.style.display = 'block'; }
    function scheduleClose() { leaveTimer = setTimeout(function() { pop.style.display = 'none'; }, 200); }

    btn.addEventListener('mouseenter', function() {
        clearTimeout(leaveTimer);
        hoverTimer = setTimeout(openPop, 300);
    });
    btn.addEventListener('mouseleave', function() {
        clearTimeout(hoverTimer);
        scheduleClose();
    });
    pop.addEventListener('mouseenter', function() { clearTimeout(leaveTimer); });
    pop.addEventListener('mouseleave', scheduleClose);
})();

function saveSearchState(query) {
    var tbody = document.getElementById('results-table-body');
    if (tbody && tbody.innerHTML.trim()) {
        sessionStorage.setItem(SAVED_HTML_KEY, tbody.innerHTML);
        sessionStorage.setItem(SAVED_QUERY_KEY, query || '');
    }
}

function restoreSearchState() {
    var savedHTML  = sessionStorage.getItem(SAVED_HTML_KEY);
    var savedQuery = sessionStorage.getItem(SAVED_QUERY_KEY);
    if (savedHTML) {
        document.getElementById('results-table-body').innerHTML = savedHTML;
        document.getElementById('search-input').value = savedQuery || '';
        showClearBtn(true);
        showFilterBar(true);
        try { initializeFilters(); } catch(e) {}

        // Restore load more state
        var savedNext = sessionStorage.getItem('abb_next_page');
        if (savedNext && savedQuery !== null) {
            initLoadMore(savedQuery, parseInt(savedNext));
            var lmc = document.getElementById('load-more-container');
            if (lmc) lmc.style.display = 'block';
        }
    }
}

function showError(msg) {
    var el = document.getElementById('search-error');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
}

function hideError() {
    var el = document.getElementById('search-error');
    if (el) { el.style.display = 'none'; el.textContent = ''; }
}

function hideFavoritesPanel() {
    var body  = document.getElementById('favorites-body');
    var arrow = document.getElementById('favorites-header-arrow');
    if (body)  body.style.display = 'none';
    if (arrow) arrow.style.transform = '';
}

function showClearBtn(show) {
    var btn = document.getElementById('clear-search-btn');
    if (btn) btn.style.display = show ? 'inline-flex' : 'none';
}

function showFilterBar(show) {
    var filterBar = document.getElementById('filter-container');
    if (filterBar) filterBar.style.display = show ? 'flex' : 'none';
}

function initFavoritesVisibility() {
    var tbody = document.getElementById('results-table-body');
    var hasResults = tbody && tbody.innerHTML.trim().length > 0;
    showFilterBar(hasResults);
    var body  = document.getElementById('favorites-body');
    var arrow = document.getElementById('favorites-header-arrow');
    if (!hasResults) {
        // Open by default when no results
        if (body)  body.style.display = 'block';
        if (arrow) arrow.style.transform = 'rotate(180deg)';
        loadFavorites();
    } else {
        // Collapsed when results are showing
        if (body)  body.style.display = 'none';
        if (arrow) arrow.style.transform = '';
    }
}

// ── Alerts state ──────────────────────────────────────────────────────────
var _alertsData      = {};
var _openNotifPanel  = null;
var _panelLeaveTimer = null;

function loadAlertsStatus() {
    fetch('/alerts/status')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _alertsData = data;
            if (_sortCol === 'bell' && _favsCache.length > 0) {
                renderFavorites(_favsCache);
            }
            refreshAlertBells();
        })
        .catch(function(e) { console.warn('[Alerts] Failed to load status:', e); });
}

function refreshAlertBells() {
    document.querySelectorAll('.fav-entry').forEach(function(entry) {
        var series = entry.dataset.series;
        if (!series) return;
        var bell = entry.querySelector('.fav-bell-btn');
        if (!bell) return;
        var data          = _alertsData[series] || {};
        var enabled       = data.enabled || false;
        var notifications = data.notifications || [];
        updateBellState(bell, enabled, notifications, series);

        // Manual check only makes sense for series already being monitored —
        // no separate enable/disable dance, just show/hide with the bell state.
        var refreshBtn = entry.querySelector('.fav-refresh-btn');
        if (refreshBtn) refreshBtn.style.display = enabled ? '' : 'none';
    });
}

function forceCheckSeries(series) {
    // Manual check for a single series, reusing the same /alerts/force_check
    // route the dev panel already uses. Only ever called for series with
    // alerts already enabled, so there's no enable/disable side effect to
    // worry about here — just run the check and let the normal alerts/bell
    // refresh pick up whatever it finds.
    var spinner = document.getElementById('favorites-header-spinner');
    if (spinner) {
        spinner.style.display = 'inline-block';
        spinner.title = 'Checking "' + series + '"\u2026';
    }

    fetch('/alerts/force_check/' + encodeURIComponent(series))
        .then(function(r) { return r.json(); })
        .then(function() { loadAlertsStatus(); })
        .catch(function(e) { console.warn('[Alerts] Force check failed for "' + series + '":', e); })
        .finally(function() { refreshCycleStatus(); });
}

function updateBellState(bell, enabled, notifications, series) {
    var newBell = bell.cloneNode(true);
    bell.parentNode.replaceChild(newBell, bell);
    bell = newBell;

    if (!enabled) {
        bell.className = 'fav-bell-btn bell-dim';
        bell.title = 'Click to enable new volume alerts for this series';
        bell.onclick = function(e) {
            e.stopPropagation();
            closeNotifPanel();
            toggleAlert(series, true);
        };
    } else if (notifications.length > 0) {
        bell.className = 'fav-bell-btn bell-glow';
        bell.title = '';
        // Hover-to-open (below) covers desktop mouse users, but touch
        // devices have no hover at all — without a click handler here,
        // tapping this bell had nowhere to go except bubbling up to the
        // row's own click handler (search), which is the bug this fixes.
        // Click toggles open/closed rather than just opening, mirroring
        // the same hover-alongside-click-toggle pattern already used for
        // the search-info tooltip elsewhere in this file.
        bell.onclick = function(e) {
            e.stopPropagation();
            if (_openNotifPanel) {
                closeNotifPanel();
            } else {
                buildNotifPanel(bell, series, notifications);
            }
        };
        var hoverTimer = null;
        bell.addEventListener('mouseenter', function() {
            clearTimeout(_panelLeaveTimer);
            hoverTimer = setTimeout(function() {
                buildNotifPanel(bell, series, notifications);
            }, 300);
        });
        bell.addEventListener('mouseleave', function() {
            clearTimeout(hoverTimer);
            _panelLeaveTimer = setTimeout(closeNotifPanel, 200);
        });
    } else {
        bell.className = 'fav-bell-btn bell-active';
        bell.title = 'Monitoring for new volumes — click to disable';
        bell.onclick = function(e) {
            e.stopPropagation();
            closeNotifPanel();
            toggleAlert(series, false);
        };
    }
}

function closeNotifPanel() {
    clearTimeout(_panelLeaveTimer);
    if (_openNotifPanel) {
        _openNotifPanel.remove();
        _openNotifPanel = null;
    }
}

function buildNotifPanel(bell, series, notifications) {
    if (_openNotifPanel && _openNotifPanel.dataset.series === series) return;
    closeNotifPanel();

    var panel = document.createElement('div');
    panel.className = 'fav-notif-panel';
    panel.dataset.series = series;

    panel.addEventListener('mouseenter', function() { clearTimeout(_panelLeaveTimer); });
    panel.addEventListener('mouseleave', function() {
        _panelLeaveTimer = setTimeout(closeNotifPanel, 200);
    });

    // Fixed header — stays put regardless of scroll position, since the
    // close button living here needs to stay reachable even when the panel
    // is tall enough that "click off it to close" is no longer a
    // realistic option (see the close button below).
    var header = document.createElement('div');
    header.className = 'fav-notif-header';

    var headerText = document.createElement('span');
    headerText.textContent = '\uD83D\uDD14 New volumes found on ABB';
    header.appendChild(headerText);

    var closeBtn = document.createElement('button');
    closeBtn.className = 'fav-notif-close';
    closeBtn.textContent = '\u2715';
    closeBtn.title = 'Close';
    closeBtn.onclick = function(e) {
        e.stopPropagation();
        closeNotifPanel();
    };
    header.appendChild(closeBtn);
    panel.appendChild(header);

    // scrollWrap is a plain positioning box that itself never scrolls —
    // scrollArea (absolutely filling it) is the only thing that does. This
    // keeps the fade overlay (a sibling of scrollArea, not a child of it)
    // fully outside the scrolling context, so it can't be affected by
    // scroll position at all — a plain absolute-positioned overlay is far
    // more reliably "always exactly there" than a sticky-positioned
    // pseudo-element depending on scroll-container math.
    var scrollWrap = document.createElement('div');
    scrollWrap.className = 'fav-notif-scroll-wrap';

    var scrollArea = document.createElement('div');
    scrollArea.className = 'fav-notif-scroll-area';

    notifications.forEach(function(n) {
        var row = document.createElement('div');
        row.className = 'fav-notif-row';

        // Groups the title with a small verification line underneath —
        // what the app parsed vs. what's already on disk — so a mismatch
        // (wrong series caught in the fuzzy match, or a bad volume parse)
        // is visible at a glance without needing to open the ABB listing.
        var text = document.createElement('div');
        text.className = 'fav-notif-text';

        var link = document.createElement('a');
        link.href = n.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.className = 'fav-notif-link';
        link.textContent = n.title;
        text.appendChild(link);

        var captionParts = [];
        if (n.matched_as) captionParts.push('Detected as ' + n.matched_as);
        // on_disk is only present on notifications found after this field was
        // added — older ones just show the detected volume with no comparison
        // rather than falsely implying "nothing on disk" for lack of data.
        if (n.on_disk !== undefined && n.on_disk !== null) {
            captionParts.push('you have ' + (n.on_disk === -1 ? 'nothing yet' : ('up to Vol. ' + n.on_disk)));
        }
        if (captionParts.length) {
            var caption = document.createElement('div');
            caption.className = 'fav-notif-caption';
            caption.textContent = captionParts.join(', ');
            text.appendChild(caption);
        }
        row.appendChild(text);

        // openDownloadModal only ever needs a link + title — same shape a
        // notification already has — so this skips straight to the same
        // modal a manual search result's Download button opens, no detour
        // through re-searching for something already sitting right here.
        var download = document.createElement('button');
        download.className = 'fav-notif-download';
        download.textContent = '\u2B07';
        download.title = 'Download this';
        download.onclick = function(e) {
            e.stopPropagation();
            closeNotifPanel();
            openDownloadModal(n.url, n.title);
        };
        row.appendChild(download);

        var dismiss = document.createElement('button');
        dismiss.className = 'fav-notif-dismiss';
        dismiss.textContent = '\uD83D\uDEAB';
        dismiss.title = "Add to blocklist";
        dismiss.onclick = function(e) {
            e.stopPropagation();
            showAppConfirm(
                'Add "' + n.title + '" to the blocklist? You won\u2019t be alerted about this listing again.',
                function() { dismissNotification(series, n.url, n.title, n.matched_as, notifications.length); },
                { okLabel: 'Blocklist', danger: true }
            );
        };
        row.appendChild(dismiss);
        scrollArea.appendChild(row);
    });

    scrollWrap.appendChild(scrollArea);

    var fade = document.createElement('div');
    fade.className = 'fav-notif-fade';
    scrollWrap.appendChild(fade);

    panel.appendChild(scrollWrap);

    // Fixed footer — same reasoning as the header/close button: an action
    // you'd want reachable regardless of how far into a long list you've
    // scrolled, not something that scrolls out of reach.
    var clearBtn = document.createElement('button');
    clearBtn.className = 'fav-notif-clear-btn';
    clearBtn.textContent = notifications.length > 1 ? 'Clear All' : 'Clear';
    clearBtn.onclick = function(e) {
        e.stopPropagation();
        closeNotifPanel();
        dismissAllNotifications(series);
    };
    panel.appendChild(clearBtn);

    document.body.appendChild(panel);
    _openNotifPanel = panel;

    var rect = bell.getBoundingClientRect();
    var margin = 12;
    var spaceBelow = window.innerHeight - rect.bottom - margin;
    var spaceAbove = rect.top - margin;
    var cssMaxHeight = window.innerHeight * 0.7;
    var bestSingleSide = Math.max(spaceAbove, spaceBelow);

    // scrollArea is a normal in-flow block at this point (no height set
    // yet), so panel.scrollHeight reflects the true total content height —
    // header + every row + footer — with nothing hidden or clipped yet.
    var naturalHeight = panel.scrollHeight;

    // header/footer's own real rendered size, measured directly rather than
    // relying on flex-grow/grid-fr to work it out — those only distribute
    // "leftover space" correctly when the panel has a definite height, and
    // here the panel only ever has a max-height cap, never a real height.
    var chromeHeight = header.offsetHeight + clearBtn.offsetHeight;

    if (naturalHeight <= Math.min(cssMaxHeight, bestSingleSide)) {
        // Fits comfortably anchored to one edge of the bell — no reason to
        // claim more room than that, keeps it feeling anchored to what was
        // actually clicked rather than taking over the screen unnecessarily.
        // scrollArea is left at its natural height here (no clipping needed).
        var openUpward = spaceBelow < 100 && spaceAbove > spaceBelow;
        var available  = openUpward ? spaceAbove : spaceBelow;
        panel.style.maxHeight = Math.min(cssMaxHeight, available) + 'px';

        var panelHeight = panel.offsetHeight;
        if (openUpward) {
            panel.style.top = (rect.top + window.scrollY - panelHeight - 6) + 'px';
        } else {
            panel.style.top = (rect.bottom + window.scrollY + 6) + 'px';
        }
    } else {
        // Scrolling is unavoidable either way here — pinning to one edge of
        // the bell and only counting that side's space would waste whatever
        // room exists on the other side for no benefit, since the list was
        // never going to fit without scrolling regardless. Claim close to
        // the full viewport height instead, so scrolling covers as little
        // of the list as possible.
        var maxPanelHeight = window.innerHeight - margin * 2;
        panel.style.maxHeight = maxPanelHeight + 'px';
        panel.style.top = (window.scrollY + margin) + 'px';

        // Give scrollArea an explicit pixel height for the room left after
        // header/footer, so it (and only it) scrolls while they stay put.
        // A small floor keeps this sane on very short mobile viewports
        // where chromeHeight could otherwise exceed maxPanelHeight.
        scrollArea.style.height = Math.max(60, maxPanelHeight - chromeHeight) + 'px';
    }
    panel.style.left = Math.max(8, rect.left + window.scrollX - 10) + 'px';

    // Only the internal list (scrollArea), not the whole panel, scrolls.
    if (scrollArea.scrollHeight > scrollArea.clientHeight) {
        fade.classList.add('fav-notif-fade-visible');
        scrollArea.addEventListener('scroll', function() {
            var atBottom = scrollArea.scrollTop + scrollArea.clientHeight >= scrollArea.scrollHeight - 2;
            fade.classList.toggle('fav-notif-fade-visible', !atBottom);
        });

        // overscroll-behavior: contain (see CSS) is the standard fix for
        // scrolling past this list's edge chaining into the page behind
        // it, but it doesn't reliably take effect on every mobile browser
        // (seen on Firefox for Android) — this manual touch handler is a
        // belt-and-suspenders fallback that works everywhere regardless.
        // It only blocks the touch when continuing to scroll would have
        // nowhere left to go (already at the top/bottom edge), so normal
        // scrolling within the list is completely unaffected.
        var touchStartY = 0;
        scrollArea.addEventListener('touchstart', function(e) {
            touchStartY = e.touches[0].clientY;
        }, { passive: true });
        scrollArea.addEventListener('touchmove', function(e) {
            var deltaY = e.touches[0].clientY - touchStartY;
            var atTop = scrollArea.scrollTop <= 0;
            var atBottom = scrollArea.scrollTop + scrollArea.clientHeight >= scrollArea.scrollHeight - 1;
            // Finger moving down (deltaY > 0) drags earlier content into
            // view, i.e. scrolls toward the top — only a problem once
            // already at the top; same logic in reverse for the bottom.
            if ((atTop && deltaY > 0) || (atBottom && deltaY < 0)) {
                e.preventDefault();
            }
        }, { passive: false });
    }
}

function toggleAlert(series, enable) {
    fetch('/alerts/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ series: series, enabled: enable })
    })
    .then(function(r) { return r.json(); })
    .then(function() { loadAlertsStatus(); });
}

function dismissNotification(series, url, title, matched_as, totalCount) {
    fetch('/alerts/dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ series: series, url: url, title: title, matched_as: matched_as })
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        if (totalCount <= 1) {
            closeNotifPanel();
            loadAlertsStatus();
        } else {
            fetch('/alerts/status')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    _alertsData = data;
                    var remaining = (data[series] || {}).notifications || [];
                    if (remaining.length === 0) {
                        closeNotifPanel();
                    } else {
                        var bell = document.querySelector('.fav-entry[data-series="' + CSS.escape(series) + '"] .fav-bell-btn');
                        if (bell) { closeNotifPanel(); buildNotifPanel(bell, series, remaining); }
                    }
                    refreshAlertBells();
                });
        }
    });
}

function dismissAllNotifications(series) {
    closeNotifPanel();
    fetch('/alerts/clear_all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ series: series })
    })
    .then(function(r) { return r.json(); })
    .then(function() { loadAlertsStatus(); });
}

// ── Save Series modal ────────────────────────────────────────────────────
// The series name saved here is purely for ABB searching / alerts.
// It never creates a mapping — folder naming is handled separately by downloads.
var _saveSeriesTotalTitle = '';

function openSaveSeriesModal(title, btn) {
    _saveSeriesTotalTitle = title;
    document.getElementById('save-series-title-display').textContent = title;
    var alertsBox = document.getElementById('save-series-alerts');
    if (alertsBox) { alertsBox.checked = false; alertsBox.disabled = false; }
    var confirmBtn = document.getElementById('save-series-confirm-btn');
    if (confirmBtn) confirmBtn.disabled = false;
    document.getElementById('save-series-hint').textContent = '';
    document.getElementById('save-series-already-saved').style.display = 'none';
    document.getElementById('save-series-disk-found').style.display = 'none';
    document.getElementById('save-series-mapping-info').style.display = 'none';
    document.getElementById('save-series-modal-form').style.display = 'block';
    document.getElementById('save-series-modal-result').style.display = 'none';
    document.getElementById('save-series-modal').style.display = 'flex';

    fetch('/favorites/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.success) return;

        // Pre-fill with extracted series name (not mapped — user sets favorites name independently)
        document.getElementById('save-series-input').value = data.extracted;

        // Show disk path if found
        if (data.disk_path) {
            document.getElementById('save-series-disk-path').textContent = data.disk_path;
            document.getElementById('save-series-disk-found').style.display = 'flex';
        }

        // Show mapping info as read-only note (folder mapping is separate from favorites)
        if (data.is_mapped && data.mapped_to) {
            var mapEl = document.getElementById('save-series-mapping-info');
            var mapText = document.getElementById('save-series-mapping-text');
            if (mapEl && mapText) {
                mapText.textContent = data.extracted + ' → ' + data.mapped_to;
                mapEl.style.display = 'flex';
            }
        }

        // Use the fresh, authoritative already_saved value from THIS live
        // fetch — more reliable than re-deriving it from the client-side
        // favorites cache, which could theoretically still be mid-refresh
        // at this exact moment (e.g. right after a bfcache restore).
        _setSaveSeriesAlreadySavedState(!!data.already_saved);
    });
}

// Applies the visual "already in favorites" state — banner, disabled Save
// button, disabled/unchecked alerts checkbox — given a plain true/false.
// Shared by the live-typing check below (which derives the boolean from
// the cached favorites list) and the modal's initial open (which uses the
// fresh, authoritative value from that same fetch instead of re-deriving
// it from the cache, since the cache could theoretically still be
// mid-refresh at that exact moment, e.g. right after a bfcache restore).
function _setSaveSeriesAlreadySavedState(showMatch) {
    var banner     = document.getElementById('save-series-already-saved');
    var alertsBox  = document.getElementById('save-series-alerts');
    var confirmBtn = document.getElementById('save-series-confirm-btn');
    if (!banner) return;

    banner.style.display = showMatch ? 'flex' : 'none';
    if (alertsBox) {
        alertsBox.disabled = showMatch;
        if (showMatch) alertsBox.checked = false;
    }
    if (confirmBtn) {
        confirmBtn.disabled = showMatch;
        confirmBtn.textContent = showMatch ? 'Saved' : 'Save';
    }
}

// Re-checks the "already in favorites" state against the cached favorites
// list every time the user edits the series name field, so editing down
// to something that matches an existing favorite (even if the modal's
// initial extraction didn't) updates it immediately instead of only
// reflecting whatever was true when the modal first opened. Re-enables
// the moment the name is edited away from a match.
function _updateSaveSeriesAlreadySaved() {
    var input = document.getElementById('save-series-input');
    if (!input) return;
    var current = input.value.trim().toLowerCase();
    var match = _favsCache.some(function(f) { return f.toLowerCase() === current; });
    _setSaveSeriesAlreadySavedState(!!(current && match));
}

function closeSaveSeriesModal() {
    document.getElementById('save-series-modal').style.display = 'none';
    document.getElementById('save-series-modal-form').style.display = 'block';
    var result = document.getElementById('save-series-modal-result');
    result.style.display = 'none';
    result.innerHTML = '';
    document.getElementById('save-series-input').value = '';
    document.getElementById('save-series-hint').textContent = '';
    var alertsBox = document.getElementById('save-series-alerts');
    if (alertsBox) { alertsBox.checked = false; alertsBox.disabled = false; }
    var confirmBtn = document.getElementById('save-series-confirm-btn');
    var cancelBtn  = document.querySelector('#save-series-modal-form .modal-btn-cancel');
    if (confirmBtn) { confirmBtn.textContent = 'Save'; confirmBtn.disabled = false; }
    if (cancelBtn)  { cancelBtn.disabled = false; }
    _saveSeriesTotalTitle = '';
}

function confirmSaveSeries() {
    var seriesName   = document.getElementById('save-series-input').value.trim();
    var enableAlerts = document.getElementById('save-series-alerts').checked;
    var btn          = document.getElementById('save-series-confirm-btn');
    var cancelBtn    = document.querySelector('#save-series-modal-form .modal-btn-cancel');

    if (!seriesName) return;

    btn.textContent    = 'Saving…';
    btn.disabled       = true;
    cancelBtn.disabled = true;

    fetch('/favorites/add_with_options', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            series_name:   seriesName,
            enable_alerts: enableAlerts
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('save-series-modal-form').style.display = 'none';
        var result = document.getElementById('save-series-modal-result');
        if (data.success) {
            var alreadyExisted = !!data.already_existed;
            result.innerHTML = '<div class="modal-result modal-result-success">'
                + '<div class="modal-result-icon">✓</div>'
                + '<p class="modal-result-title">' + (alreadyExisted ? 'Already saved' : 'Saved') + '</p>'
                + '<p class="modal-result-sub">' + escHtml(data.series || seriesName) + '</p>'
                + '</div>';
            result.style.display = 'block';
            var body = document.getElementById('favorites-body');
            if (body && body.style.display !== 'none') loadFavorites();
            setTimeout(function() { closeSaveSeriesModal(); }, 1800);
        } else {
            result.innerHTML = '<div class="modal-result modal-result-error">'
                + '<div class="modal-result-icon">✕</div>'
                + '<p class="modal-result-title">Could not save</p>'
                + '<p class="modal-result-sub">' + escHtml(data.message || 'Unknown error') + '</p>'
                + '<button class="modal-btn-cancel" onclick="closeSaveSeriesModal()" style="margin-top:14px;">Close</button>'
                + '</div>';
            result.style.display = 'block';
        }
    })
    .catch(function(err) {
        document.getElementById('save-series-modal-form').style.display = 'none';
        var result = document.getElementById('save-series-modal-result');
        result.innerHTML = '<div class="modal-result modal-result-error">'
            + '<div class="modal-result-icon">✕</div>'
            + '<p class="modal-result-title">Failed to save</p>'
            + '<p class="modal-result-sub">' + err + '</p>'
            + '<button class="modal-btn-cancel" onclick="closeSaveSeriesModal()" style="margin-top:14px;">Close</button>'
            + '</div>';
        result.style.display = 'block';
    });
}

document.addEventListener('DOMContentLoaded', function() {
    var input = document.getElementById('save-series-input');
    if (input) {
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') confirmSaveSeries();
            if (e.key === 'Escape') closeSaveSeriesModal();
        });
        input.addEventListener('input', _updateSaveSeriesAlreadySaved);
    }
});

// ── Manual alert check / cycle status ────────────────
var _cycleStatusInterval = null;
var _cyclePollStartedAt  = null;
var POLL_BUFFER_MS       = 10 * 60 * 1000; // extra buffer on top of expected cycle duration

function runAlertsNow() {
    var btn = document.getElementById('fav-check-now-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Starting\u2026'; }

    fetch('/alerts/run_now', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (btn) { btn.disabled = false; btn.textContent = 'Check Now'; }
            // The first series is processed synchronously inside run_now, so its
            // result is already on disk by the time this response arrives — reload
            // bell states immediately rather than waiting on cycle_status polling.
            loadAlertsStatus();
            refreshCycleStatus();
        })
        .catch(function() {
            if (btn) { btn.disabled = false; btn.textContent = 'Check Now'; }
        });
}

function refreshCycleStatus() {
    fetch('/alerts/cycle_status')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            updateCycleStatusUI(data);
        })
        .catch(function() {});
}

function updateCycleStatusUI(data) {
    var spinner = document.getElementById('favorites-header-spinner');
    var checkBtn = document.getElementById('fav-check-now-btn');
    var nextText = document.getElementById('fav-next-check-text');

    if (spinner) {
        spinner.style.display = data.running ? 'inline-block' : 'none';
        if (data.running) {
            spinner.title = 'Checking saved series for new volumes (' + data.checked + ' of ' + data.total + ')';
        }
    }

    if (checkBtn) {
        checkBtn.disabled = data.running;
        checkBtn.textContent = data.running
            ? 'Checking\u2026 (' + data.checked + '/' + data.total + ')'
            : 'Check Now';
    }

    if (nextText) {
        nextText.textContent = data.next_run_at ? ('Next check: ' + data.next_run_at) : '';
    }

    // Poll frequently while running, stop polling once idle
    if (data.running && !_cycleStatusInterval) {
        _cyclePollStartedAt = Date.now();
        _cycleStatusInterval = setInterval(refreshCycleStatus, 5000);
    } else if (data.running && _cycleStatusInterval) {
        // Safety cap scales with queue size — assume ~5 min per series (server's
        // ALERT_CHECK_INTERVAL) plus a buffer, so legitimate long cycles aren't cut off
        var intervalMin = data.check_interval_min || 5;
        var expectedMs = (data.total || 1) * intervalMin * 60 * 1000 + POLL_BUFFER_MS;
        if (_cyclePollStartedAt && (Date.now() - _cyclePollStartedAt) > expectedMs) {
            clearInterval(_cycleStatusInterval);
            _cycleStatusInterval = null;
            _cyclePollStartedAt = null;
            console.warn('[Alerts] Stopped polling — exceeded expected cycle duration.');
        }
    } else if (!data.running && _cycleStatusInterval) {
        clearInterval(_cycleStatusInterval);
        _cycleStatusInterval = null;
        _cyclePollStartedAt = null;
        // Reload favorites once a cycle finishes so new notifications show up
        loadAlertsStatus();
    }
}

// ── Favorites panel ───────────────────────────────────────────────────────
function toggleFavorites() {
    var body  = document.getElementById('favorites-body');
    var arrow = document.getElementById('favorites-header-arrow');
    var isOpen = body && body.style.display !== 'none';
    if (isOpen) {
        if (body)  body.style.display = 'none';
        if (arrow) arrow.style.transform = '';
    } else {
        if (body)  body.style.display = 'block';
        if (arrow) arrow.style.transform = 'rotate(180deg)';
        loadFavorites();
    }
}

// ── Sort state (persisted to localStorage) ────────────────────────────────
var SORT_KEY   = 'abb_fav_sort';
var _sortCol   = 'title';
var _sortDir   = 'asc';
var _favsCache = [];

(function() {
    try {
        var saved = JSON.parse(localStorage.getItem(SORT_KEY) || '{}');
        if (saved.col) _sortCol = saved.col;
        if (saved.dir) _sortDir = saved.dir;
    } catch(e) {}
})();

function saveSortPref() {
    try { localStorage.setItem(SORT_KEY, JSON.stringify({ col: _sortCol, dir: _sortDir })); } catch(e) {}
}

function loadFavorites() {
    fetch('/favorites')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _favsCache = data.favorites || [];
            renderFavorites(_favsCache);
            loadAlertsStatus();
        });
}

function applySortedFavs(favs) {
    if (_sortCol === 'title') {
        return favs.slice().sort(function(a, b) {
            return _sortDir === 'asc' ? a.localeCompare(b) : b.localeCompare(a);
        });
    } else {
        return favs.slice().sort(function(a, b) {
            var aEnabled = (_alertsData[a] || {}).enabled ? 1 : 0;
            var bEnabled = (_alertsData[b] || {}).enabled ? 1 : 0;
            if (_sortDir === 'asc') {
                if (bEnabled !== aEnabled) return bEnabled - aEnabled;
            } else {
                if (bEnabled !== aEnabled) return aEnabled - bEnabled;
            }
            return a.localeCompare(b);
        });
    }
}

function cycleSortCol(col) {
    if (_sortCol === col) {
        _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        _sortCol = col;
        _sortDir = 'asc';
    }
    saveSortPref();
    renderFavorites(_favsCache);
    refreshAlertBells();
    updateSortButtons();
}

function updateSortButtons() {
    var bellBtn  = document.getElementById('fav-sort-bell');
    var titleBtn = document.getElementById('fav-sort-title');
    if (!bellBtn || !titleBtn) return;
    var bArrow = _sortCol === 'bell'  ? (_sortDir === 'asc' ? ' \u2191' : ' \u2193') : ' \u2195';
    var tArrow = _sortCol === 'title' ? (_sortDir === 'asc' ? ' \u2191' : ' \u2193') : ' \u2195';
    bellBtn.textContent  = '\uD83D\uDD14' + bArrow;
    titleBtn.textContent = 'Title' + tArrow;
    bellBtn.classList.toggle('fav-sort-active',  _sortCol === 'bell');
    titleBtn.classList.toggle('fav-sort-active', _sortCol === 'title');
}

function renderFavorites(favs) {
    var list  = document.getElementById('favorites-list');
    var empty = document.getElementById('favorites-empty');
    if (!list) return;
    list.querySelectorAll('.fav-entry').forEach(function(el) { el.remove(); });
    list.querySelectorAll('.fav-footer-line').forEach(function(el) { el.remove(); });

    // Sort row — injected into list, below header, above entries
    list.querySelectorAll('.fav-sort-row').forEach(function(el) { el.remove(); });
    var sortRow = document.createElement('div');
    sortRow.className = 'fav-sort-row';

    var bellSortBtn = document.createElement('button');
    bellSortBtn.id = 'fav-sort-bell';
    bellSortBtn.className = 'fav-sort-btn';
    bellSortBtn.title = 'Sort by alert state';
    bellSortBtn.onclick = function(e) { e.stopPropagation(); cycleSortCol('bell'); };
    sortRow.appendChild(bellSortBtn);

    var titleSortBtn = document.createElement('button');
    titleSortBtn.id = 'fav-sort-title';
    titleSortBtn.className = 'fav-sort-btn';
    titleSortBtn.title = 'Sort by title';
    titleSortBtn.onclick = function(e) { e.stopPropagation(); cycleSortCol('title'); };
    sortRow.appendChild(titleSortBtn);

    list.appendChild(sortRow);

    var sorted = applySortedFavs(favs);

    if (sorted.length === 0) {
        empty.style.display = 'block';
    } else {
        empty.style.display = 'none';
        sorted.forEach(function(name) {
            var entry = document.createElement('div');
            entry.className = 'fav-entry';
            entry.dataset.series = name;
            entry.onclick = function() { searchFavorite(name); };

            var bell = document.createElement('button');
            bell.className = 'fav-bell-btn bell-dim';
            bell.textContent = '\uD83D\uDD14';
            bell.title = 'Click to enable new volume alerts for this series';
            entry.appendChild(bell);

            var link = document.createElement('button');
            link.className = 'fav-search-btn';
            link.textContent = name;
            entry.appendChild(link);

            // Manual per-series check. Only ever shown for series that already
            // have alerts enabled (see refreshAlertBells) — hidden by default
            // here since alert state hasn't loaded yet at initial render.
            var refreshBtn = document.createElement('button');
            refreshBtn.className = 'fav-refresh-btn';
            refreshBtn.textContent = '\u21BB';
            refreshBtn.title = 'Check this series now';
            refreshBtn.style.display = 'none';
            refreshBtn.onclick = function(e) { e.stopPropagation(); forceCheckSeries(name); };

            var editBtn = document.createElement('button');
            editBtn.className = 'fav-menu-item';
            editBtn.textContent = 'Edit';
            editBtn.onclick = function(e) { e.stopPropagation(); closeAllFavMenus(); startEdit(entry, name); };

            var delBtn = document.createElement('button');
            delBtn.className = 'fav-menu-item fav-menu-item-danger';
            delBtn.textContent = 'Remove';
            delBtn.onclick = function(e) { e.stopPropagation(); closeAllFavMenus(); confirmDelete(name); };

            var kebabMenu = document.createElement('div');
            kebabMenu.className = 'fav-kebab-menu';
            kebabMenu.style.display = 'none';
            kebabMenu.appendChild(editBtn);
            kebabMenu.appendChild(delBtn);

            var kebabBtn = document.createElement('button');
            kebabBtn.className = 'fav-kebab-btn';
            kebabBtn.textContent = '\u22EE';
            kebabBtn.title = 'More options';
            kebabBtn.onclick = function(e) { e.stopPropagation(); toggleFavMenu(kebabMenu); };

            var kebabWrap = document.createElement('div');
            kebabWrap.className = 'fav-kebab-wrap';
            kebabWrap.appendChild(kebabBtn);
            kebabWrap.appendChild(kebabMenu);

            var actions = document.createElement('div');
            actions.className = 'fav-entry-actions';
            actions.appendChild(refreshBtn);
            actions.appendChild(kebabWrap);
            entry.appendChild(actions);

            list.appendChild(entry);
        });
    }
    renderAddRow(list);
    updateSortButtons();
}

function renderAddRow(list) {
    var row = document.createElement('div');
    row.className = 'fav-add-row';

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'fav-manual-input';
    input.placeholder = 'Add a series manually\u2026';
    input.style.display = 'none';

    var addBtn = document.createElement('button');
    addBtn.className = 'fav-add-btn';
    addBtn.textContent = '+';
    addBtn.title = 'Add a series manually';
    addBtn.onclick = function() {
        if (input.style.display === 'none') {
            input.style.display = 'block';
            input.focus();
            addBtn.textContent = '\u2713';
        } else {
            var name = input.value.trim();
            if (name) saveManualFavorite(name);
        }
    };

    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter')  { var n = input.value.trim(); if (n) saveManualFavorite(n); }
        if (e.key === 'Escape') { input.style.display = 'none'; addBtn.textContent = '+'; }
    });

    var footerLine = document.createElement('div');
    footerLine.className = 'fav-footer-line';

    row.appendChild(input);
    row.appendChild(addBtn);
    footerLine.appendChild(row);

    var checkRow = document.createElement('div');
    checkRow.className = 'fav-check-row';
    checkRow.id = 'fav-check-row';

    var nextCheckText = document.createElement('span');
    nextCheckText.className = 'fav-next-check';
    nextCheckText.id = 'fav-next-check-text';
    nextCheckText.textContent = '';

    var checkBtn = document.createElement('button');
    checkBtn.className = 'fav-menu-item';
    checkBtn.id = 'fav-check-now-btn';
    checkBtn.textContent = 'Check Now';
    checkBtn.title = 'Immediately check all enabled series for new volumes on ABB';
    checkBtn.onclick = function() { closeAllFavMenus(); runAlertsNow(); };

    var discordTestBtn = document.createElement('button');
    discordTestBtn.className = 'fav-menu-item';
    discordTestBtn.id = 'fav-discord-test-btn';
    discordTestBtn.textContent = 'Test Discord Webhook';
    discordTestBtn.title = 'Send a sample notification to confirm your Discord webhook is working';
    discordTestBtn.style.display = 'none';   // shown only if a webhook is actually configured
    discordTestBtn.onclick = function() { sendDiscordTest(discordTestBtn); };

    var cogMenu = document.createElement('div');
    cogMenu.className = 'fav-cog-menu';
    cogMenu.id = 'fav-cog-menu';
    cogMenu.style.display = 'none';
    cogMenu.appendChild(checkBtn);
    cogMenu.appendChild(discordTestBtn);

    var cogBtn = document.createElement('button');
    cogBtn.className = 'fav-cog-btn';
    cogBtn.id = 'fav-cog-btn';
    cogBtn.textContent = '\u2699';
    cogBtn.title = 'Alert options';
    cogBtn.onclick = function(e) { e.stopPropagation(); toggleFavMenu(cogMenu); };

    var cogWrap = document.createElement('div');
    cogWrap.className = 'fav-cog-wrap';
    cogWrap.appendChild(cogBtn);
    cogWrap.appendChild(cogMenu);

    checkRow.appendChild(nextCheckText);
    checkRow.appendChild(cogWrap);
    footerLine.appendChild(checkRow);

    list.appendChild(footerLine);

    fetch('/alerts/discord_status')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.configured) discordTestBtn.style.display = '';
        })
        .catch(function() {});

    refreshCycleStatus();
}

// #favorites-panel normally clips overflow (for its rounded corners), which
// would cut off a per-row kebab menu opening near the bottom edge — same
// reason the cog menu has to open upward. Rather than leaving overflow
// clipped all the time, it's only relaxed while a menu is actually open.
function closeAllFavMenus() {
    var cogMenu = document.getElementById('fav-cog-menu');
    if (cogMenu) cogMenu.style.display = 'none';
    document.querySelectorAll('.fav-kebab-menu').forEach(function(m) { m.style.display = 'none'; });

    var panel = document.getElementById('favorites-panel');
    if (panel) panel.classList.remove('fav-menu-open');
}

function toggleFavMenu(menuEl) {
    if (!menuEl) return;
    var willOpen = menuEl.style.display === 'none';
    closeAllFavMenus();
    if (willOpen) {
        menuEl.style.display = 'block';
        var panel = document.getElementById('favorites-panel');
        if (panel) panel.classList.add('fav-menu-open');
    }
}

document.addEventListener('click', function(e) {
    if (e.target.closest('.fav-cog-wrap') || e.target.closest('.fav-kebab-wrap')) return;
    closeAllFavMenus();
});

function sendDiscordTest(btn) {
    var original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Sending\u2026';

    fetch('/alerts/discord_test', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            btn.textContent = data.success ? 'Sent!' : 'Failed';
        })
        .catch(function() {
            btn.textContent = 'Failed';
        })
        .finally(function() {
            setTimeout(function() {
                btn.textContent = original;
                btn.disabled = false;
                closeAllFavMenus();
            }, 1500);
        });
}

function saveManualFavorite(name) {
    fetch('/favorites/add_manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        loadFavorites();
        if (data.already_existed) {
            showAppAlert('"' + data.series + '" is already saved.');
        }
    });
}

function startEdit(entry, oldName) {
    entry.innerHTML = '';
    // Row is temporarily an edit form now, not a searchable series row —
    // loadFavorites() rebuilds the row (and this handler) fresh once
    // editing ends, whether via save or cancel.
    entry.onclick = null;

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'fav-manual-input';
    input.value = oldName;

    var saveBtn = document.createElement('button');
    saveBtn.className = 'fav-edit-btn';
    saveBtn.textContent = '\u2713';
    saveBtn.onclick = function() { commitEdit(oldName, input.value.trim()); };

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'fav-delete-btn';
    cancelBtn.textContent = '\u2715';
    cancelBtn.onclick = function() { loadFavorites(); };

    entry.appendChild(input);
    entry.appendChild(saveBtn);
    entry.appendChild(cancelBtn);
    input.focus();

    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter')  commitEdit(oldName, input.value.trim());
        if (e.key === 'Escape') loadFavorites();
    });
}

function commitEdit(oldName, newName) {
    if (!newName || newName === oldName) { loadFavorites(); return; }
    fetch('/favorites/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_name: oldName, new_name: newName })
    }).then(function(r) { return r.json(); }).then(function() { loadFavorites(); });
}

function confirmDelete(name) {
    showAppConfirm('Remove "' + name + '" from your saved series?', function() {
        fetch('/favorites/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        }).then(function(r) { return r.json(); }).then(function() { loadFavorites(); });
    }, { okLabel: 'Remove', danger: true });
}

function searchFavorite(name) {
    // Collapse favorites body (panel stays visible) and run AJAX search
    var body  = document.getElementById('favorites-body');
    var arrow = document.getElementById('favorites-header-arrow');
    if (body)  body.style.display = 'none';
    if (arrow) arrow.style.transform = '';
    performSearch(name);
}


// ── Download modal ────────────────────────────────────────────────────────
var _modalLink = '';
var _modalTitle = '';
var _originalSeries = '';

function openDownloadModal(link, title) {
    _modalLink  = link;
    _modalTitle = title;

    var skipSeries = localStorage.getItem(SKIP_SERIES_KEY) === 'true';
    document.getElementById('modal-skip-series').checked = skipSeries;
    document.getElementById('modal-keyword-info').style.display = 'none';

    fetch('/mappings/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        _originalSeries = data.series_name || title;
        document.getElementById('modal-title-display').textContent = title;
        document.getElementById('modal-series-input').value = _originalSeries;
        document.getElementById('modal-series-input').disabled = skipSeries;

        if (data.matched_keyword) {
            document.getElementById('modal-keyword-text').textContent = data.matched_keyword;
            document.getElementById('modal-keyword-info').style.display = 'flex';
        }

        updateModalPath();
        checkVolumeExists(title, _originalSeries);
        document.getElementById('download-modal').style.display = 'flex';
    });
}

function checkVolumeExists(title, series) {
    fetch('/check_exists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title, series: series })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var warning = document.getElementById('modal-exists-warning');
        var pathEl  = document.getElementById('modal-exists-path');
        if (data.exists && data.match) {
            pathEl.textContent = data.match;
            warning.style.display = 'flex';
        } else {
            warning.style.display = 'none';
        }
    })
    .catch(function() { document.getElementById('modal-exists-warning').style.display = 'none'; });
}

function onSkipSeriesChange() {
    var skip = document.getElementById('modal-skip-series').checked;
    localStorage.setItem(SKIP_SERIES_KEY, skip ? 'true' : 'false');
    document.getElementById('modal-series-input').disabled = skip;
    updateModalPath();
}

function updateModalPath() {
    var skip       = document.getElementById('modal-skip-series').checked;
    var series     = document.getElementById('modal-series-input').value.trim();
    var basePath   = document.getElementById('page-data').dataset.savePath || '/audiobooks';
    var safeTitle  = _modalTitle.replace(/[<>:"/\\|?*]/g, '').trim();
    var safeSeries = series.replace(/[<>:"/\\|?*]/g, '').trim();
    var hint       = document.getElementById('modal-series-hint');
    var preview    = document.getElementById('modal-path-preview');

    if (!skip && series !== _originalSeries) {
        hint.textContent = '(custom)';
        hint.style.color = 'var(--accent)';
    } else {
        hint.textContent = '';
    }

    preview.textContent = skip
        ? basePath + '/' + safeTitle
        : basePath + '/' + (safeSeries || '\u2026') + '/' + safeTitle;
}

document.addEventListener('DOMContentLoaded', function() {
    var input = document.getElementById('modal-series-input');
    if (input) {
        input.addEventListener('input', updateModalPath);
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') confirmDownload();
            if (e.key === 'Escape') closeDownloadModal();
        });
    }
});

function closeDownloadModal() {
    document.getElementById('download-modal').style.display = 'none';
    document.getElementById('download-modal-form').style.display = 'block';
    var result = document.getElementById('download-modal-result');
    result.style.display = 'none';
    result.innerHTML = '';
    document.getElementById('modal-title-display').textContent = '';
    document.getElementById('modal-series-input').value = '';
    document.getElementById('modal-series-input').disabled = false;
    document.getElementById('modal-series-hint').textContent = '';
    document.getElementById('modal-path-preview').textContent = '';
    document.getElementById('modal-exists-warning').style.display = 'none';
    document.getElementById('modal-keyword-info').style.display = 'none';
    var confirmBtn = document.querySelector('.modal-btn-confirm');
    var cancelBtn  = document.querySelector('.modal-btn-cancel');
    if (confirmBtn) { confirmBtn.textContent = 'Download to Server'; confirmBtn.disabled = false; }
    if (cancelBtn)  { cancelBtn.disabled = false; }
    _modalLink = '';
    _modalTitle = '';
    _originalSeries = '';
}

function confirmDownload() {
    var skip        = document.getElementById('modal-skip-series').checked;
    var seriesInput = skip ? '' : document.getElementById('modal-series-input').value.trim();
    var btn         = document.querySelector('.modal-btn-confirm');
    var cancelBtn   = document.querySelector('.modal-btn-cancel');
    btn.textContent    = 'Sending\u2026';
    btn.disabled       = true;
    cancelBtn.disabled = true;

    // Editing the series field here only affects this one download — it
    // no longer creates a lasting mapping. Standing folder-name rules for
    // a whole series/franchise are now set up deliberately on the Series
    // Mappings page as a keyword, not as a side effect of one edit here.
    var saveMapping = Promise.resolve();

    saveMapping.then(function() {
        return fetch('/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                link: _modalLink,
                title: _modalTitle,
                series_override: seriesInput,
                skip_series: skip
            })
        });
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('download-modal-form').style.display = 'none';
        var result = document.getElementById('download-modal-result');
        result.innerHTML = '<div class="modal-result modal-result-success">'
            + '<div class="modal-result-icon">\u2713</div>'
            + '<p class="modal-result-title">Added to queue</p>'
            + '<p class="modal-result-sub">' + (_modalTitle || '') + '</p>'
            + '</div>';
        result.style.display = 'block';
        setTimeout(function() { closeDownloadModal(); }, 2000);
    })
    .catch(function(err) {
        document.getElementById('download-modal-form').style.display = 'none';
        var result = document.getElementById('download-modal-result');
        result.innerHTML = '<div class="modal-result modal-result-error">'
            + '<div class="modal-result-icon">\u2715</div>'
            + '<p class="modal-result-title">Failed to add</p>'
            + '<p class="modal-result-sub">' + err + '</p>'
            + '<button class="modal-btn-cancel" onclick="closeDownloadModal()" style="margin-top:14px;">Close</button>'
            + '</div>';
        result.style.display = 'block';
    });
}

// ── Load More ─────────────────────────────────────────────────────────────
var _currentQuery  = '';
var _nextStartPage = 1;
var _loadingMore   = false;

function initLoadMore(query, nextPage) {
    _currentQuery  = query;
    _nextStartPage = nextPage;
    var container = document.getElementById('load-more-container');
    if (container) container.style.display = 'block';
    var btn = document.getElementById('load-more-btn');
    if (btn) { btn.style.display = 'block'; btn.textContent = 'Load More Results'; btn.disabled = false; }
}

function loadMore() {
    if (_loadingMore) return;
    _loadingMore = true;

    var btn    = document.getElementById('load-more-btn');
    var status = document.getElementById('load-more-status');
    btn.disabled = true;
    status.style.display = 'none';
    startLoadingDots(btn);

    fetch('/search_more', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: _currentQuery, start_page: _nextStartPage })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        _loadingMore = false;
        if (data.error) {
            btn.textContent = 'Load More Results';
            btn.disabled    = false;
            status.textContent   = 'Error: ' + data.error;
            status.style.display = 'block';
            return;
        }
        if (!data.books || data.books.length === 0) {
            btn.style.display    = 'none';
            status.textContent   = 'No more results.';
            status.style.display = 'block';
            return;
        }

        appendBooks(data.books);
        var query = document.getElementById('search-input').value.trim();
        saveSearchState(query);

        _nextStartPage += 1;
        sessionStorage.setItem('abb_next_page', _nextStartPage);
        stopLoadingDots(btn, 'Load More Results');
        btn.disabled = false;

        if (!data.has_more) {
            btn.style.display    = 'none';
            status.textContent   = 'All results loaded.';
            status.style.display = 'block';
        }

        try { initializeFilters(); } catch(e) {}
    })
    .catch(function() {
        _loadingMore = false;
        stopLoadingDots(btn, 'Load More Results');
        btn.disabled = false;
        status.textContent   = 'Failed to load more results.';
        status.style.display = 'block';
    });
}

function escHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Loading dots ──────────────────────────────────────────────────────────
var _dotsInterval = null;

function startLoadingDots(btn) {
    var dots = ['Loading', 'Loading.', 'Loading..', 'Loading...'];
    var i = 0;
    btn.textContent = dots[0];
    _dotsInterval = setInterval(function() {
        i = (i + 1) % dots.length;
        btn.textContent = dots[i];
    }, 300);
}

function stopLoadingDots(btn, label) {
    if (_dotsInterval) { clearInterval(_dotsInterval); _dotsInterval = null; }
    btn.textContent = label;
}
