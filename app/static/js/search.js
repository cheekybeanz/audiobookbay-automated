document.addEventListener("DOMContentLoaded", function () {
    if (document.querySelectorAll(".result-row").length > 0) {
        initializeFilters();
        document.getElementById("filter-button").addEventListener("click", applyFilters);
        document.getElementById("clear-button").addEventListener("click", clearFilters);
    }
});

let datePicker;
let fileSizeSlider;

function initializeFilters() {
    populateSelectFilters();
    initializeFileSizeSlider();
    initializeDateRangePicker();
}

// ── Helper functions ──────────────────────────────────────────────────────
function parseFileSizeToMB(sizeString) {
    if (!sizeString || sizeString.trim().toLowerCase() === 'n/a') return null;
    const parts = sizeString.trim().split(/\s+/);
    if (parts.length < 2) return null;
    const size = parseFloat(parts[0]);
    const unit = parts[1].toUpperCase();
    if (isNaN(size)) return null;
    if (unit.startsWith("TB")) return size * 1024 * 1024;
    if (unit.startsWith("GB")) return size * 1024;
    return size;
}

function formatFileSize(mb) {
    if (mb === null || isNaN(mb)) return "N/A";
    if (mb >= 1024 * 1024) return (mb / (1024 * 1024)).toFixed(2) + " TB";
    if (mb >= 1024) return (mb / 1024).toFixed(2) + " GB";
    return mb.toFixed(2) + " MB";
}

// ── Filter initialisation ─────────────────────────────────────────────────
function initializeDateRangePicker() {
    const allDates = Array.from(document.querySelectorAll('.result-row'))
        .map(row => {
            const dateStr = row.dataset.postDate;
            if (!dateStr || dateStr === 'N/A') return null;
            const formattedStr = dateStr.replace(/(\d{1,2})\s(\w{3})\s(\d{4})/, '$2 $1, $3');
            const date = new Date(formattedStr);
            return isNaN(date) ? null : date;
        })
        .filter(date => date !== null);

    let options = { mode: "range", dateFormat: "Y-m-d" };

    if (allDates.length > 0) {
        options.minDate = new Date(Math.min.apply(null, allDates));
        options.maxDate = new Date(Math.max.apply(null, allDates));
    }

    if (datePicker) datePicker.destroy();
    datePicker = flatpickr("#date-range-filter", options);
}

function initializeFileSizeSlider() {
    const sliderElement = document.getElementById('file-size-slider');
    const allSizes = Array.from(document.querySelectorAll('.result-row'))
        .map(row => parseFileSizeToMB(row.dataset.fileSize))
        .filter(size => size !== null);

    if (allSizes.length < 2) {
        document.querySelector('.file-size-filter-wrapper').style.display = 'none';
        return;
    }

    const minSize = Math.min(...allSizes);
    const maxSize = Math.max(...allSizes);
    const formatter = {
        to:   value => formatFileSize(value),
        from: value => Number(parseFileSizeToMB(value))
    };

    if (fileSizeSlider) {
        fileSizeSlider.destroy();
        fileSizeSlider = null;
    }

    fileSizeSlider = noUiSlider.create(sliderElement, {
        start: [minSize, maxSize],
        connect: true,
        tooltips: [formatter, formatter],
        range: { min: minSize, max: maxSize }
    });
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
    const language      = document.getElementById("language-filter").value;
    const bitrate       = document.getElementById("bitrate-filter").value;
    const format        = document.getElementById("format-filter").value;
    const selectedDates = datePicker ? datePicker.selectedDates : [];
    const sizeRange     = fileSizeSlider ? fileSizeSlider.get().map(parseFloat) : null;

    document.querySelectorAll(".result-row").forEach(row => {
        let visible = true;
        if (language && row.dataset.language !== language) visible = false;
        if (bitrate  && row.dataset.bitrate  !== bitrate)  visible = false;
        if (format   && row.dataset.format   !== format)   visible = false;
        if (sizeRange) {
            const rowSizeMB = parseFileSizeToMB(row.dataset.fileSize);
            if (rowSizeMB !== null && (rowSizeMB < sizeRange[0] || rowSizeMB > sizeRange[1])) visible = false;
        }
        if (selectedDates.length === 2) {
            const rowDateStr = row.dataset.postDate;
            if (!rowDateStr || rowDateStr === 'N/A') {
                visible = false;
            } else {
                try {
                    const formattedStr = rowDateStr.replace(/(\d{1,2})\s(\w{3})\s(\d{4})/, '$2 $1, $3');
                    const rowDate = new Date(formattedStr);
                    rowDate.setHours(0, 0, 0, 0);
                    if (rowDate < selectedDates[0] || rowDate > selectedDates[1]) visible = false;
                } catch (e) { visible = false; }
            }
        }
        row.style.display = visible ? "" : "none";
    });
}

function clearFilters() {
    document.getElementById("language-filter").value = "";
    document.getElementById("bitrate-filter").value  = "";
    document.getElementById("format-filter").value   = "";
    if (datePicker)     datePicker.clear();
    if (fileSizeSlider) fileSizeSlider.reset();
    document.querySelectorAll(".result-row").forEach(row => row.style.display = "");
}

// ── Loading spinner ───────────────────────────────────────────────────────
function showLoadingSpinner() {
    const buttonSpinner = document.getElementById("button-spinner");
    if (buttonSpinner) buttonSpinner.style.display = "inline-block";
    setTimeout(showScrollingMessages, 5000);
}

// ── Scrolling messages ────────────────────────────────────────────────────
const messages = [
    "Searching... This better be worth it!",
    "Hold on, this takes a while...",
    "Still searching... Maybe grab a snack?",
    "Patience, young grasshopper...",
    "Wow, this is taking a minute!",
    "Don't worry, I got this!",
    "Maybe go for a walk?",
    "Still thinking... Almost there!",
    "Finding the best results for you!",
    "Hang tight! Searching magic happening!",
    "One moment... while I consult the ancients.",
    "Beep boop... processing... please wait...",
    "My hamsters are running on a wheel, almost there!",
    "Just gathering some pixie dust, be right back!",
    "Is it lunchtime yet? Oh, searching... right.",
    "Please remain calm, the search is in progress.",
    "Warning: Search may cause extreme awesomeness.",
    "Calculating the optimal route to your results...",
    "Almost there... just defragmenting my brain.",
    "Searching... because the internet is a big place!",
    "Polishing the search results for your viewing pleasure.",
    "The search is strong with this one.",
    "Please wait while I summon the search demons.",
    "Searching in hyperspace... almost there!",
    "My coffee is kicking in... search commencing!",
    "Just a few more gigabytes to process...",
    "Rome wasn't built in a day.",
    "Don't blame me, the internet is slow today.",
    "Almost there... just need to find the right key...",
];

let messageIndex = 0;
let intervalId   = null;

function showScrollingMessages() {
    const messageScroller  = document.getElementById("message-scroller");
    const scrollingMessage = document.getElementById("scrolling-message");
    if (!scrollingMessage) return;
    const shuffled = messages.sort(() => Math.random() - 0.5);
    messageScroller.style.display = "block";
    scrollingMessage.textContent  = shuffled[messageIndex];
    intervalId = setInterval(() => {
        messageIndex = (messageIndex + 1) % messages.length;
        scrollingMessage.textContent = shuffled[messageIndex];
    }, 5000);
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
    // Clone to wipe all old event listeners cleanly
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

    // Keep panel open while mouse is inside it
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
        dismiss.textContent = '\u229B';
        dismiss.title = "Add to blocklist \u2014 won't show again for this specific upload";
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
        if (totalCount <= 1) closeNotifPanel();
        loadAlertsStatus();
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

// ── Favorites panel ───────────────────────────────────────────────────────
function toggleFavorites() {
    var panel = document.getElementById('favorites-panel');
    var isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) loadFavorites();
}

// ── Sort state (persisted to localStorage) ────────────────────────────────
var SORT_KEY  = 'abb_fav_sort';
var _sortCol  = 'title';
var _sortDir  = 'asc';
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
    list.querySelectorAll('.fav-entry').forEach(function(el) { el.remove(); });
    list.querySelectorAll('.fav-add-row').forEach(function(el) { el.remove(); });
    list.querySelectorAll('.fav-sort-row').forEach(function(el) { el.remove(); });

    // Sort row — bell button aligns above bell icons, title button above series names
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

    row.appendChild(input);
    row.appendChild(addBtn);
    list.appendChild(row);
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
    if (confirm('Remove "' + name + '" from favorites?')) {
        fetch('/favorites/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        }).then(function(r) { return r.json(); }).then(function() { loadFavorites(); });
    }
}

function searchFavorite(name) {
    var input = document.getElementById('search-input');
    if (input) {
        input.value = name;
        showLoadingSpinner();
        input.closest('form').submit();
    }
}

function saveFavorite(title, btn) {
    fetch('/favorites/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            btn.textContent = '\u2713 Saved';
            btn.disabled = true;
            var panel = document.getElementById('favorites-panel');
            if (panel.style.display !== 'none') loadFavorites();
        } else {
            btn.textContent = data.message || 'Already saved';
            setTimeout(function() { btn.textContent = '\u2B50 Save Series'; btn.disabled = false; }, 2000);
        }
    });
}

// ── Session persistence ───────────────────────────────────────────────────
var PAGE_HAS_RESULTS = document.getElementById('page-data').dataset.hasResults === 'true';
var SAVED_QUERY_KEY  = 'abb_search_query';
var SAVED_HTML_KEY   = 'abb_search_html';
var SKIP_SERIES_KEY  = 'abb_skip_series';

function saveSearchState() {
    var tbody = document.getElementById('results-table-body');
    var query = document.getElementById('search-input').value;
    if (tbody && tbody.innerHTML.trim()) {
        sessionStorage.setItem(SAVED_HTML_KEY, tbody.innerHTML);
        sessionStorage.setItem(SAVED_QUERY_KEY, query);
    }
}

function resetLoadMoreState() {
    sessionStorage.removeItem('abb_next_page');
    var container = document.getElementById('load-more-container');
    if (container) container.style.display = 'none';
}

function restoreSearchState() {
    if (PAGE_HAS_RESULTS) {
        saveSearchState();
        resetLoadMoreState();
        showClearBtn(true);
        showFilterBar(true);
        var lmc = document.getElementById('load-more-container');
        if (lmc) lmc.style.display = 'block';
        return;
    }
    var savedHTML  = sessionStorage.getItem(SAVED_HTML_KEY);
    var savedQuery = sessionStorage.getItem(SAVED_QUERY_KEY);
    if (savedHTML) {
        document.getElementById('results-table-body').innerHTML = savedHTML;
        document.getElementById('search-input').value = savedQuery || '';
        showClearBtn(true);
        showFilterBar(true);
        if (typeof initializeFilters === 'function') {
            try { initializeFilters(); } catch(e) {}
            var filterBtn = document.getElementById('filter-button');
            var clearBtn  = document.getElementById('clear-button');
            if (filterBtn) filterBtn.addEventListener('click', applyFilters);
            if (clearBtn)  clearBtn.addEventListener('click', clearFilters);
        }
    }
}

function clearSearch() {
    sessionStorage.removeItem(SAVED_HTML_KEY);
    sessionStorage.removeItem(SAVED_QUERY_KEY);
    sessionStorage.removeItem('abb_next_page');
    document.getElementById('results-table-body').innerHTML = '';
    document.getElementById('search-input').value = '';
    showClearBtn(false);
    showFilterBar(false);
    var panel = document.getElementById('favorites-panel');
    panel.style.display = 'block';
    loadFavorites();
}

function showClearBtn(show) {
    var btn = document.getElementById('clear-search-btn');
    if (btn) btn.style.display = show ? 'inline-flex' : 'none';
}

function showFilterBar(show) {
    var filterBar    = document.getElementById('filter-container');
    var noResultsBar = document.getElementById('no-results-bar');
    if (filterBar)    filterBar.style.display    = show ? 'flex' : 'none';
    if (noResultsBar) noResultsBar.style.display = show ? 'none' : 'block';
}

function initFavoritesVisibility() {
    var tbody = document.getElementById('results-table-body');
    var hasResults = tbody && tbody.innerHTML.trim().length > 0;
    showFilterBar(hasResults);
    if (!hasResults) {
        document.getElementById('favorites-panel').style.display = 'block';
        loadFavorites();
    }
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

    if (skip) {
        preview.textContent = basePath + '/' + safeTitle;
    } else {
        preview.textContent = basePath + '/' + (safeSeries || '\u2026') + '/' + safeTitle;
    }
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
    if (_loadingMore || !_currentQuery) return;
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

        var tbody = document.getElementById('results-table-body');
        data.books.forEach(function(book) {
            var tr = document.createElement('tr');
            tr.className = 'result-row';
            tr.dataset.language = book.language;
            tr.dataset.bitrate  = book.bitrate;
            tr.dataset.format   = book.format;
            tr.dataset.fileSize = book.file_size;
            tr.dataset.postDate = book.post_date;
            tr.dataset.link     = book.link;
            tr.dataset.title    = book.title;

            var defaultCover = '/static/images/default_cover.jpg';
            var coverSrc = book.cover || defaultCover;
            tr.innerHTML =
                '<td><img src="' + escHtml(coverSrc) + '" '
                    + 'alt="Cover Art" class="cover" width="100" '
                    + 'onerror="this.src=&quot;/static/images/default_cover.jpg&quot;"></td>'
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
                    + '<button class="btn-details"  onclick="handleDetails(this)">Details</button>'
                    + '<button class="btn-download" onclick="handleDownload(this)">Download to Server</button>'
                    + '<button class="fav-btn"      onclick="handleFavorite(this)">\u2B50 Save Series</button>'
                + '</td>';
            tbody.appendChild(tr);
        });

        saveSearchState();
        _nextStartPage += 1;
        sessionStorage.setItem('abb_next_page', _nextStartPage);
        stopLoadingDots(btn, 'Load More Results');
        btn.disabled = false;

        if (!data.has_more) {
            btn.style.display    = 'none';
            status.textContent   = 'All results loaded.';
            status.style.display = 'block';
        }
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
    var dots = ['Loading .  ', 'Loading .. ', 'Loading ...'];
    var i = 0;
    btn.textContent = dots[0];
    _dotsInterval = setInterval(function() {
        i = (i + 1) % dots.length;
        btn.textContent = dots[i];
    }, 400);
}

function stopLoadingDots(btn, label) {
    if (_dotsInterval) { clearInterval(_dotsInterval); _dotsInterval = null; }
    btn.textContent = label;
}

// ── Row helpers ───────────────────────────────────────────────────────────
function handleDetails(btn)  { window.open(btn.closest('tr').dataset.link, '_blank'); }
function handleDownload(btn) { var r = btn.closest('tr'); openDownloadModal(r.dataset.link, r.dataset.title); }
function handleFavorite(btn) { saveFavorite(btn.closest('tr').dataset.title, btn); }

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    restoreSearchState();
    initFavoritesVisibility();
    var query = document.getElementById('search-input').value.trim();
    var tbody = document.getElementById('results-table-body');
    var hasResults = tbody && tbody.innerHTML.trim().length > 0;
    if (hasResults && query) {
        var pageLimit = parseInt(document.getElementById('page-data').dataset.pageLimit || '5');
        var savedNext = sessionStorage.getItem('abb_next_page');
        var nextPage  = savedNext ? parseInt(savedNext) : (pageLimit + 1);
        initLoadMore(query, nextPage);
        var lmc = document.getElementById('load-more-container');
        if (lmc) lmc.style.display = 'block';
    }
});
