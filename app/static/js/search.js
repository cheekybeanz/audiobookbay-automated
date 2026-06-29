document.addEventListener("DOMContentLoaded", function () {
    // Initialize filtering if results are present on page load (server-side render)
    if (document.querySelectorAll(".result-row").length > 0) {
        initializeFilters();
        document.getElementById("filter-button").addEventListener("click", applyFilters);
        document.getElementById("clear-button").addEventListener("click", clearFilters);
    }
});

let datePicker;
let fileSizeSlider;

// Called from search.html after session-restored or load-more results are injected
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

    // Destroy existing instance before reinitialising (called after load more)
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

    // Destroy existing slider before reinitialising (called after load more)
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

    // Clear and repopulate (safe to call multiple times after load more)
    function repopulate(selectId, values) {
        const el = document.getElementById(selectId);
        const current = el.value;
        // Remove all options except the first "All X" default
        while (el.options.length > 1) el.remove(1);
        values.forEach(val => {
            if (val && val !== 'N/A') {
                const opt = document.createElement("option");
                opt.value = val;
                opt.textContent = val;
                el.appendChild(opt);
            }
        });
        el.value = current; // Restore previous selection if still valid
    }

    repopulate("language-filter", languages);
    repopulate("bitrate-filter",  bitrates);
    repopulate("format-filter",   formats);
}

// ── Apply / clear filters ─────────────────────────────────────────────────
function applyFilters() {
    const language     = document.getElementById("language-filter").value;
    const bitrate      = document.getElementById("bitrate-filter").value;
    const format       = document.getElementById("format-filter").value;
    const selectedDates = datePicker ? datePicker.selectedDates : [];
    const sizeRange    = fileSizeSlider ? fileSizeSlider.get().map(parseFloat) : null;

    document.querySelectorAll(".result-row").forEach(row => {
        let visible = true;

        if (language && row.dataset.language !== language) visible = false;
        if (bitrate  && row.dataset.bitrate  !== bitrate)  visible = false;
        if (format   && row.dataset.format   !== format)   visible = false;

        if (sizeRange) {
            const rowSizeMB = parseFileSizeToMB(row.dataset.fileSize);
            if (rowSizeMB !== null && (rowSizeMB < sizeRange[0] || rowSizeMB > sizeRange[1])) {
                visible = false;
            }
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
                } catch (e) {
                    visible = false;
                }
            }
        }

        row.style.display = visible ? "" : "none";
    });
}

function clearFilters() {
    document.getElementById("language-filter").value = "";
    document.getElementById("bitrate-filter").value  = "";
    document.getElementById("format-filter").value   = "";
    if (datePicker)    datePicker.clear();
    if (fileSizeSlider) fileSizeSlider.reset();
    document.querySelectorAll(".result-row").forEach(row => row.style.display = "");
}

// ── Loading spinner (called from search.html form onsubmit) ───────────────
function showLoadingSpinner() {
    const buttonSpinner = document.getElementById("button-spinner");
    if (buttonSpinner) buttonSpinner.style.display = "inline-block";
    setTimeout(showScrollingMessages, 5000);
}

// ── Scrolling messages during long searches ───────────────────────────────
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
