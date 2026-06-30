document.addEventListener("DOMContentLoaded", function () {
    restoreSearchState();
    initFavoritesVisibility();
    refreshCycleStatus();

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
                + '<button class="fav-btn"      onclick="handleSaveSeries(this)">\u2B50 Save Series</button>'
                + '<button class="btn-details"  onclick="handleDetails(this)">Details</button>'
            + '</td>';
        tbody.appendChild(tr);
    });
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
    });
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
        bell.onclick = null;
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

    var header = document.createElement('div');
    header.className = 'fav-notif-header';
    header.textContent = '\uD83D\uDD14 New volumes found on ABB';
    panel.appendChild(header);

    notifications.forEach(function(n) {
        var row = document.createElement('div');
        row.className = 'fav-notif-row';

        var link = document.createElement('a');
        link.href = n.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.className = 'fav-notif-link';
        link.textContent = n.title;
        row.appendChild(link);

        var dismiss = document.createElement('button');
        dismiss.className = 'fav-notif-dismiss';
        dismiss.textContent = '\uD83D\uDEAB';
        dismiss.title = "Add to blocklist";
        dismiss.onclick = function(e) {
            e.stopPropagation();
            dismissNotification(series, n.url, n.title, n.matched_as, notifications.length);
        };
        row.appendChild(dismiss);
        panel.appendChild(row);
    });

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
    panel.style.top  = (rect.bottom + window.scrollY + 6) + 'px';
    panel.style.left = Math.max(8, rect.left + window.scrollX - 10) + 'px';
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
    document.getElementById('save-series-alerts').checked = false;
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

        // Show already-saved notice
        if (data.already_saved) {
            document.getElementById('save-series-already-saved').style.display = 'flex';
        }

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

        document.getElementById('save-series-input').focus();
        document.getElementById('save-series-input').select();
    });
}

function closeSaveSeriesModal() {
    document.getElementById('save-series-modal').style.display = 'none';
    document.getElementById('save-series-modal-form').style.display = 'block';
    document.getElementById('save-series-modal-result').style.display = 'none';
    document.getElementById('save-series-input').value = '';
    document.getElementById('save-series-hint').textContent = '';
    document.getElementById('save-series-alerts').checked = false;
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
            result.innerHTML = '<div class="modal-result modal-result-success">'
                + '<div class="modal-result-icon">✓</div>'
                + '<p class="modal-result-title">Series saved</p>'
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
            spinner.title = 'Checking favorites for new volumes (' + data.checked + ' of ' + data.total + ')';
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

            var bell = document.createElement('button');
            bell.className = 'fav-bell-btn bell-dim';
            bell.textContent = '\uD83D\uDD14';
            bell.title = 'Click to enable new volume alerts for this series';
            entry.appendChild(bell);

            var link = document.createElement('button');
            link.className = 'fav-search-btn';
            link.textContent = name;
            link.onclick = function(e) { e.stopPropagation(); searchFavorite(name); };
            entry.appendChild(link);

            var editBtn = document.createElement('button');
            editBtn.className = 'fav-edit-btn';
            editBtn.textContent = '\u270F\uFE0F';
            editBtn.title = 'Edit';
            editBtn.onclick = function(e) { e.stopPropagation(); startEdit(entry, name); };
            entry.appendChild(editBtn);

            var del = document.createElement('button');
            del.className = 'fav-delete-btn';
            del.textContent = '\u2715';
            del.title = 'Remove';
            del.onclick = function(e) { e.stopPropagation(); confirmDelete(name); };
            entry.appendChild(del);

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
    checkBtn.className = 'fav-check-now-btn';
    checkBtn.id = 'fav-check-now-btn';
    checkBtn.textContent = 'Check Now';
    checkBtn.title = 'Immediately check all enabled series for new volumes on ABB';
    checkBtn.onclick = function() { runAlertsNow(); };

    checkRow.appendChild(nextCheckText);
    checkRow.appendChild(checkBtn);
    footerLine.appendChild(checkRow);

    list.appendChild(footerLine);

    refreshCycleStatus();
}

function saveManualFavorite(name) {
    fetch('/favorites/add_manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
    }).then(function(r) { return r.json(); }).then(function() { loadFavorites(); });
}

function startEdit(entry, oldName) {
    entry.innerHTML = '';
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
    showAppConfirm('Remove "' + name + '" from favorites?', function() {
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

    fetch('/mappings/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        _originalSeries = data.extracted || title;
        document.getElementById('modal-title-display').textContent = title;
        document.getElementById('modal-series-input').value = _originalSeries;
        document.getElementById('modal-series-input').disabled = skipSeries;
        updateModalPath();
        checkVolumeExists(title, _originalSeries);
        document.getElementById('download-modal').style.display = 'flex';
        if (!skipSeries) document.getElementById('modal-series-input').focus();
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

    var saveMapping = Promise.resolve();
    if (!skip && seriesInput && seriesInput !== _originalSeries) {
        saveMapping = fetch('/mappings/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ extracted: _originalSeries, mapped: seriesInput })
        }).then(function(r) { return r.json(); });
    }

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
