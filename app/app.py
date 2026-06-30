import os
import pwd
import grp
import json
import re
import time
import logging
import requests
from datetime import datetime
from flask import Flask, request, render_template, jsonify
from bs4 import BeautifulSoup
from qbittorrentapi import Client
from transmission_rpc import Client as transmissionrpc
from deluge_web_client import DelugeWebClient as delugewebclient
from deluge_web_client import TorrentOptions as delugetorrentoptions
from dotenv import load_dotenv
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# Load environment variables
# Load from /config/.env first (user-editable), then fall back to container env vars
_config_env = os.path.join("/config", ".env")
if os.path.exists(_config_env):
    load_dotenv(_config_env, override=True)
else:
    load_dotenv()

ABB_HOSTNAME = os.getenv("ABB_HOSTNAME", "audiobookbay.lu")
PAGE_LIMIT   = int(os.getenv("PAGE_LIMIT", 5))

DOWNLOAD_CLIENT = os.getenv("DOWNLOAD_CLIENT")
DL_URL = os.getenv("DL_URL")
if DL_URL:
    parsed_url = urlparse(DL_URL)
    DL_SCHEME  = parsed_url.scheme
    DL_HOST    = parsed_url.hostname
    DL_PORT    = parsed_url.port
else:
    DL_SCHEME = os.getenv("DL_SCHEME", "http")
    DL_HOST   = os.getenv("DL_HOST")
    DL_PORT   = os.getenv("DL_PORT")
    if DL_HOST and DL_PORT:
        DL_URL = f"{DL_SCHEME}://{DL_HOST}:{DL_PORT}"

DL_USERNAME    = os.getenv("DL_USERNAME")
DL_PASSWORD    = os.getenv("DL_PASSWORD")
DL_CATEGORY    = os.getenv("DL_CATEGORY", "Audiobookbay-Audiobooks")
SAVE_PATH_BASE  = os.getenv("SAVE_PATH_BASE")
SCAN_PATH_BASE  = os.getenv("SCAN_PATH_BASE", SAVE_PATH_BASE)
REQUEST_DELAY   = float(os.getenv("REQUEST_DELAY", "0.75"))

NAV_LINK_NAME = os.getenv("NAV_LINK_NAME")
NAV_LINK_URL  = os.getenv("NAV_LINK_URL")
FLASK_PORT    = int(os.getenv("PORT", 5078))

# Alert scheduler config
ALERT_CHECK_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL", 5))   # minutes between each series check within a cycle
ALERT_CHECK_TIME     = os.getenv("ALERT_CHECK_TIME", "02:00")       # time of day to run daily cycle (HH:MM, 24hr)

log.info(f"ABB_HOSTNAME: {ABB_HOSTNAME}")
log.info(f"DOWNLOAD_CLIENT: {DOWNLOAD_CLIENT}")
log.info(f"DL_HOST: {DL_HOST}")
log.info(f"DL_PORT: {DL_PORT}")
log.info(f"DL_URL: {DL_URL}")
log.info(f"DL_USERNAME: {DL_USERNAME}")
log.info(f"DL_CATEGORY: {DL_CATEGORY}")
log.info(f"SAVE_PATH_BASE: {SAVE_PATH_BASE}")
log.info(f"SCAN_PATH_BASE: {SCAN_PATH_BASE}")
log.info(f"REQUEST_DELAY: {REQUEST_DELAY}")
log.info(f"NAV_LINK_NAME: {NAV_LINK_NAME}")
log.info(f"NAV_LINK_URL: {NAV_LINK_URL}")
log.info(f"PAGE_LIMIT: {PAGE_LIMIT}")
log.info(f"PORT: {FLASK_PORT}")
log.info(f"ALERT_CHECK_INTERVAL: {ALERT_CHECK_INTERVAL}m")
log.info(f"ALERT_CHECK_TIME: {ALERT_CHECK_TIME}")

# ── Startup config validation ──────────────────────────────────────────────
_valid_clients = ("qbittorrent", "transmission", "delugeweb")
if not DOWNLOAD_CLIENT:
    log.warning("DOWNLOAD_CLIENT is not set — downloads will not work. "
                "Set it to one of: qbittorrent, transmission, delugeweb")
elif DOWNLOAD_CLIENT not in _valid_clients:
    log.warning(f"DOWNLOAD_CLIENT '{DOWNLOAD_CLIENT}' is not recognised. "
                f"Valid options are: {', '.join(_valid_clients)}")

if DOWNLOAD_CLIENT in ("qbittorrent", "transmission"):
    if not DL_HOST:
        log.warning("DL_HOST is not set — downloads will not work.")
    if not DL_PORT:
        log.warning("DL_PORT is not set — downloads will not work.")

if DOWNLOAD_CLIENT == "delugeweb" and not DL_URL:
    log.warning("DL_URL is not set — Deluge downloads will not work.")

if not SAVE_PATH_BASE:
    log.warning("SAVE_PATH_BASE is not set — downloads will have no save path.")

# ── Config / persistent data ───────────────────────────────────────────────
CONFIG_DIR        = "/config"
FAVORITES_PATH    = os.path.join(CONFIG_DIR, "favorites.json")
SERIES_MAP_PATH   = os.path.join(CONFIG_DIR, "series_map.json")
ALERTS_PATH       = os.path.join(CONFIG_DIR, "alerts.json")
BLOCKLIST_PATH    = os.path.join(CONFIG_DIR, "alert_blocklist.json")

os.makedirs(CONFIG_DIR, exist_ok=True)

# Create a documented .env template if one doesn't exist yet
_env_template = os.path.join(CONFIG_DIR, ".env")
if not os.path.exists(_env_template):
    with open(_env_template, "w") as f:
        f.write("""# ABB Automated Configuration
# Edit this file and restart the container to apply changes.
# Lines starting with # are comments and are ignored.
# Variables marked [default: x] will use that value if left commented out.
# Variables marked [required] must be set for that feature to work.

# ── AudioBookBay ──────────────────────────────────────────────────────────

# AudioBookBay hostname — update if the domain moves  [default: audiobookbay.lu]
# ABB_HOSTNAME=audiobookbay.lu

# Pages to load per search and per Load More click    [default: 5]
# PAGE_LIMIT=5

# Delay in seconds between page fetches               [default: 0.75]
# Increase if you get rate limited, decrease if behind a VPN
# REQUEST_DELAY=0.75

# ── Download client ───────────────────────────────────────────────────────
# Uncomment ONE block below depending on your torrent client.  [required]

# ── qBittorrent ──
# DOWNLOAD_CLIENT=qbittorrent
# DL_HOST=qbittorrent        # use container name and internal port
# DL_HOST=192.168.1.100      # or use IP address and host-mapped port
# DL_PORT=8080
# DL_USERNAME=admin
# DL_PASSWORD=password

# ── Transmission ──
# DOWNLOAD_CLIENT=transmission
# DL_HOST=transmission       # container name and internal port, or IP and host-mapped port
# DL_PORT=9091
# DL_SCHEME=http
# DL_USERNAME=admin
# DL_PASSWORD=password

# ── Deluge Web ──
# DOWNLOAD_CLIENT=delugeweb
# DL_URL=http://deluge:8112  # container name and internal port, or IP and host-mapped port
# DL_PASSWORD=password

# Category/label assigned to downloads in the torrent client  [default: Audiobookbay-Audiobooks]
# DL_CATEGORY=Audiobookbay-Audiobooks

# ── Paths ─────────────────────────────────────────────────────────────────

# Path where audiobooks are saved — as seen by the torrent client  [required]
# SAVE_PATH_BASE=/data/media/books/audiobooks

# Path used to scan for existing volumes on disk.                  [optional]
# Step 1: In Unraid add a path mapping:
#           Host Path:      /mnt/user/data/media/books/audiobooks
#           Container Path: /audiobooks  (or any name you like)
#           Access:         Read Only
# Step 2: Uncomment and set SCAN_PATH_BASE to match the container path above.
# If not set, falls back to SAVE_PATH_BASE (requires matching path mapping in Unraid).
# SCAN_PATH_BASE=/audiobooks

# ── Web UI ────────────────────────────────────────────────────────────────

# Port the web UI runs on                             [default: 5078]
# PORT=5078

# Optional custom link shown in the navbar            [optional]
# NAV_LINK_NAME=Audiobookshelf
# NAV_LINK_URL=http://192.168.1.100:13378

# ── New volume alerts ─────────────────────────────────────────────────────

# Time of day to run the daily alert check cycle      [default: 02:00]
# Uses 24-hour format. Restarting the container does NOT trigger a check.
# ALERT_CHECK_TIME=02:00

# Minutes between checking each series within a cycle [default: 5]
# With 6 favorites this spreads checks over 30 minutes to avoid rate limits.
# ALERT_CHECK_INTERVAL=5
""")
    log.info(f"Created config template at {_env_template}")

if not os.path.exists(FAVORITES_PATH):
    with open(FAVORITES_PATH, "w") as f:
        json.dump([], f)
if not os.path.exists(SERIES_MAP_PATH):
    with open(SERIES_MAP_PATH, "w") as f:
        json.dump({}, f)
if not os.path.exists(ALERTS_PATH):
    with open(ALERTS_PATH, "w") as f:
        json.dump({}, f)
if not os.path.exists(BLOCKLIST_PATH):
    with open(BLOCKLIST_PATH, "w") as f:
        json.dump([], f)

try:
    nobody    = pwd.getpwnam("nobody")
    users_gid = grp.getgrnam("users").gr_gid
    for _path in [FAVORITES_PATH, SERIES_MAP_PATH, ALERTS_PATH, BLOCKLIST_PATH]:
        os.chown(_path, nobody.pw_uid, users_gid)
        os.chmod(_path, 0o664)
    if os.path.exists(_env_template):
        os.chown(_env_template, nobody.pw_uid, users_gid)
        os.chmod(_env_template, 0o664)
except Exception as e:
    log.warning(f"Could not set config file ownership: {e}")


@app.context_processor
def inject_nav_link():
    return {
        "nav_link_name": os.getenv("NAV_LINK_NAME"),
        "nav_link_url":  os.getenv("NAV_LINK_URL"),
    }


# ── Scraper helpers ────────────────────────────────────────────────────────
_ABB_REQUIRED_MARKERS = [".post", ".postTitle", "#sidebar"]

def _page_looks_valid(soup):
    for marker in _ABB_REQUIRED_MARKERS:
        if soup.select(marker):
            return True
    return False

def _page_is_rate_limited(soup, response):
    if response.status_code in (429, 403):
        return True
    text = response.text.lower()
    return any(phrase in text for phrase in [
        "just a moment", "checking your browser", "captcha",
        "access denied", "banned", "too many requests",
    ])


# ── Search ─────────────────────────────────────────────────────────────────
def search_audiobookbay(query, max_pages=PAGE_LIMIT, start_page=1):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/115.0.0.0 Safari/537.36"
    }
    results = []

    if query:
        log.info(f"Searching for '{query}' on https://{ABB_HOSTNAME}...")
    else:
        log.info(f"Fetching new releases from https://{ABB_HOSTNAME}...")

    for page in range(start_page, start_page + max_pages):
        if query:
            url = (f"https://{ABB_HOSTNAME}/page/{page}/"
                   f"?s={requests.utils.quote(query.lower().replace(' ', '+'), safe='+')}")
        else:
            url = f"https://{ABB_HOSTNAME}/page/{page}/"

        if page > start_page:
            time.sleep(REQUEST_DELAY)

        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to fetch page {page}. Reason: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        if _page_is_rate_limited(soup, response):
            log.warning(f"Rate limited or banned on page {page}. Status: {response.status_code}.")
            raise RuntimeError("rate_limited")

        if response.status_code != 200:
            log.error(f"Page {page} returned HTTP {response.status_code}. Stopping.")
            break

        if not _page_looks_valid(soup):
            log.warning(f"Page {page} doesn't look like a valid ABB page — structure may have changed.")
            break

        posts = soup.select(".post")
        if not posts:
            log.info(f"No more results found on page {page}.")
            break

        log.info(f"Processing {len(posts)} posts on page {page}...")

        for post in posts:
            try:
                title_element = post.select_one(".postTitle > h2 > a")
                if not title_element:
                    continue

                title = title_element.text.strip()
                link  = f"https://{ABB_HOSTNAME}{title_element['href']}"

                cover_url = (
                    post.select_one("img")["src"] if post.select_one("img") else None
                )
                cover = cover_url if cover_url else "/static/images/default_cover.jpg"

                post_info      = post.select_one(".postInfo")
                post_info_text = post_info.get_text(separator=" ", strip=True) if post_info else ""

                language_match = re.search(
                    r"Language:\s*(.*?)(?:\s*Keywords:|$)", post_info_text, re.DOTALL
                )
                language = language_match.group(1).strip() if language_match else "N/A"

                details_paragraph = post.select_one(
                    ".postContent p[style*='text-align:center']"
                )
                post_date = book_format = bitrate = file_size = "N/A"

                if details_paragraph:
                    details_html = str(details_paragraph)

                    m = re.search(r"Posted:\s*([^<]+)", details_html)
                    post_date = m.group(1).strip() if m else "N/A"

                    m = re.search(r"Format:\s*<span[^>]*>([^<]+)</span>", details_html)
                    book_format = m.group(1).strip() if m else "N/A"

                    m = re.search(r"Bitrate:\s*<span[^>]*>([^<]+)</span>", details_html)
                    bitrate = m.group(1).strip() if m else "N/A"

                    m = re.search(
                        r"File Size:\s*<span[^>]*>([^<]+)</span>\s*([^<]+)", details_html
                    )
                    if m:
                        file_size = f"{m.group(1).strip()} {m.group(2).strip()}"

                results.append({
                    "title":     title,
                    "link":      link,
                    "cover":     cover,
                    "language":  language,
                    "post_date": post_date,
                    "format":    book_format,
                    "bitrate":   bitrate,
                    "file_size": file_size,
                })
            except Exception as e:
                log.error(f"Could not process a post. Details: {e}")
                continue

    return results


# ── Magnet link extraction ─────────────────────────────────────────────────
def extract_magnet_link(details_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/115.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(details_url, headers=headers)
        if response.status_code != 200:
            log.error(f"Failed to fetch details page. Status Code: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        info_hash_row = soup.find("td", string=re.compile(r"Info Hash", re.IGNORECASE))
        if not info_hash_row:
            log.error("Info Hash not found on the page.")
            return None
        info_hash = info_hash_row.find_next_sibling("td").text.strip()

        tracker_rows = soup.find_all("td", string=re.compile(r"udp://|http://", re.IGNORECASE))
        trackers = [row.text.strip() for row in tracker_rows]

        if not trackers:
            trackers = [
                "udp://tracker.openbittorrent.com:80",
                "udp://opentor.org:2710",
                "udp://tracker.ccc.de:80",
                "udp://tracker.blackunicorn.xyz:6969",
                "udp://tracker.coppersurfer.tk:6969",
                "udp://tracker.leechers-paradise.org:6969",
            ]

        trackers_query = "&".join(f"tr={requests.utils.quote(t)}" for t in trackers)
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}&{trackers_query}"
        log.debug(f"Generated Magnet Link: {magnet_link}")
        return magnet_link

    except Exception as e:
        log.error(f"Failed to extract magnet link: {e}")
        return None


# ── Title / series helpers ─────────────────────────────────────────────────
def sanitize_title(title):
    return re.sub(r'[<>:"/\\|?*]', "", title).strip()


def _extract_series_raw(title):
    if " - " in title:
        authorless = title.rsplit(" - ", 1)[0].strip()
    else:
        authorless = title.strip()
    series = re.split(
        r"[:,]?\s*(?:Vol(?:ume)?\.?|Book|Part|Year)\s+[0-9]+",
        authorless, flags=re.IGNORECASE
    )[0]
    series = re.sub(r"\s+[0-9]+$", "", series)
    series = series.strip().rstrip(",").strip()
    return series if series else authorless


def get_series_name(title):
    series = _extract_series_raw(title)
    if not series:
        series = title

    if os.path.exists(SERIES_MAP_PATH):
        try:
            with open(SERIES_MAP_PATH) as f:
                content = f.read().strip()
                if content:
                    mapping = json.loads(content)
                    sanitized = sanitize_title(series)
                    if sanitized in mapping:
                        return mapping[sanitized]
        except json.JSONDecodeError:
            pass

    return series


def _normalize_for_fuzzy(name):
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _find_series_folder(scan_base, series_name):
    if not os.path.isdir(scan_base):
        return None

    normalized_target = _normalize_for_fuzzy(series_name)
    best_match = None

    try:
        for entry in os.scandir(scan_base):
            if not entry.is_dir():
                continue
            if entry.name.lower() == series_name.lower():
                return entry.path
            if _normalize_for_fuzzy(entry.name) == normalized_target:
                best_match = entry.path
    except PermissionError:
        pass

    return best_match


# ── Volume number extraction (shared) ─────────────────────────────────────
def extract_vol_num(t):
    """Extract a volume number from a title string. Returns string or None."""
    authorless = t.rsplit(" - ", 1)[0].strip() if " - " in t else t.strip()

    # 1. Bracket format anywhere: [16], - [16]
    m = re.search(r"(?:^|-\s*)\[([0-9]+(?:[.][0-9]+)?)\]", authorless)
    if m:
        return m.group(1)

    # 2. Keyword-based: Vol./Volume/Book/Part/Year N — take last match
    matches = list(re.finditer(
        r"(?:Vol(?:ume)?[.]?|Book|Part|Year)[ ]*([0-9]+(?:[.][0-9]+)?)",
        authorless, re.IGNORECASE
    ))
    if matches:
        return matches[-1].group(1)

    # 3. Bare number at start: "16 Series Title"
    m = re.match(r"^([0-9]+(?:[.][0-9]+)?)\s+\S", authorless)
    if m:
        return m.group(1)

    # 4. Bare number at end: "Series Title 16"
    m = re.search(r"(?<![0-9])([0-9]+(?:[.][0-9]+)?)(?:[ ]*:|[ ]*$)", authorless)
    if m:
        return m.group(1)

    return None


def _get_highest_vol_on_disk(series_name):
    """Return the highest volume number found on disk for a series, or -1 if none found."""
    if not SCAN_PATH_BASE:
        return -1

    safe_series = sanitize_title(series_name) if series_name else ""
    if safe_series:
        scan_path = _find_series_folder(SCAN_PATH_BASE, safe_series)
    else:
        scan_path = SCAN_PATH_BASE

    if not scan_path or not os.path.isdir(scan_path):
        return -1

    highest = -1
    try:
        for entry in os.scandir(scan_path):
            if not entry.is_dir():
                continue
            vol = extract_vol_num(entry.name)
            if vol:
                try:
                    highest = max(highest, int(float(vol)))
                except ValueError:
                    pass
    except PermissionError:
        pass

    return highest


# ── Alert helpers ──────────────────────────────────────────────────────────
def load_alerts():
    if os.path.exists(ALERTS_PATH):
        try:
            with open(ALERTS_PATH) as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            return {}
    return {}


def save_alerts(data):
    with open(ALERTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_blocklist():
    if os.path.exists(BLOCKLIST_PATH):
        try:
            with open(BLOCKLIST_PATH) as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        except json.JSONDecodeError:
            return []
    return []


def save_blocklist(data):
    with open(BLOCKLIST_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _is_blocked(url):
    return any(entry.get("url") == url for entry in load_blocklist())


# ── Alert scheduler ────────────────────────────────────────────────────────
_alert_series_queue = []
_alert_cycle_total  = 0   # total series in the current/last cycle, for progress display

def _alert_cycle_start():
    """
    Called once per day at ALERT_CHECK_TIME (or manually via /alerts/run_now).
    Builds the queue of enabled series and immediately processes the first one
    so a manual trigger doesn't sit idle waiting for the next interval tick.
    The rest of the queue is drained by _alert_tick on its normal stagger.
    """
    global _alert_series_queue, _alert_cycle_total

    alerts  = load_alerts()
    enabled = [s for s, v in alerts.items() if v.get("enabled")]
    if not enabled:
        log.info("[Alerts] Cycle triggered but no series have alerts enabled.")
        return False

    _alert_series_queue = list(enabled)
    _alert_cycle_total  = len(_alert_series_queue)
    log.info(f"[Alerts] Cycle started — {_alert_cycle_total} series queued.")

    # Process the first series right away rather than waiting for the next tick
    first_series = _alert_series_queue.pop(0)
    _check_series_for_new_volume(first_series, alerts)

    return True


def _alert_tick():
    """
    Called every ALERT_CHECK_INTERVAL minutes.
    Processes one series from the queue so ABB is never hit in a burst.
    """
    global _alert_series_queue

    if not _alert_series_queue:
        return

    alerts = load_alerts()
    series = _alert_series_queue.pop(0)
    _check_series_for_new_volume(series, alerts)


def _check_series_for_new_volume(series, alerts):
    """Search ABB page 1 for a series and flag any volumes higher than what's on disk."""
    log.info(f"[Alerts] Checking '{series}' for new volumes...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/115.0.0.0 Safari/537.36"
    }

    url = (f"https://{ABB_HOSTNAME}/page/1/"
           f"?s={requests.utils.quote(series.lower().replace(' ', '+'), safe='+')}")

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.exceptions.RequestException as e:
        log.error(f"[Alerts] Failed to fetch ABB for '{series}': {e}")
        return

    if _page_is_rate_limited(BeautifulSoup(response.text, "html.parser"), response):
        log.warning(f"[Alerts] Rate limited while checking '{series}'. Will retry next cycle.")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    if not _page_looks_valid(soup):
        log.warning(f"[Alerts] ABB page for '{series}' didn't look valid.")
        return

    highest_on_disk = _get_highest_vol_on_disk(series)
    blocklist       = load_blocklist()
    blocked_urls    = {e.get("url") for e in blocklist}

    alerts = load_alerts()
    series_data = alerts.get(series, {})
    existing_notifications = {n["url"] for n in series_data.get("notifications", [])}
    new_notifications = list(series_data.get("notifications", []))

    posts = soup.select(".post")
    for post in posts:
        try:
            title_el = post.select_one(".postTitle > h2 > a")
            if not title_el:
                continue
            title = title_el.text.strip()
            link  = f"https://{ABB_HOSTNAME}{title_el['href']}"

            if link in blocked_urls or link in existing_notifications:
                continue

            vol = extract_vol_num(title)
            if vol is None:
                continue

            try:
                vol_int = int(float(vol))
            except ValueError:
                continue

            if highest_on_disk >= 0 and vol_int > highest_on_disk:
                log.info(f"[Alerts] New volume found for '{series}': {title} (Vol {vol_int} > disk {highest_on_disk})")
                new_notifications.append({
                    "url":        link,
                    "title":      title,
                    "matched_as": f"Vol. {vol_int}",
                    "found_at":   datetime.utcnow().strftime("%Y-%m-%d"),
                })
        except Exception as e:
            log.error(f"[Alerts] Error processing post for '{series}': {e}")
            continue

    series_data["notifications"] = new_notifications
    series_data["last_checked"]  = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    alerts[series] = series_data
    save_alerts(alerts)


# ── Start scheduler ────────────────────────────────────────────────────────
try:
    _alert_hour, _alert_minute = [int(x) for x in ALERT_CHECK_TIME.split(":")]
except ValueError:
    log.warning(f"[Alerts] Invalid ALERT_CHECK_TIME '{ALERT_CHECK_TIME}', defaulting to 02:00.")
    _alert_hour, _alert_minute = 2, 0

_scheduler = BackgroundScheduler(daemon=True)

# Daily cycle trigger — fires once per day at the configured time
_scheduler.add_job(
    _alert_cycle_start, "cron",
    hour=_alert_hour, minute=_alert_minute,
    id="alert_daily_cycle"
)

# Per-series stagger — fires every ALERT_CHECK_INTERVAL minutes to drain the queue
_scheduler.add_job(
    _alert_tick, "interval",
    minutes=ALERT_CHECK_INTERVAL,
    id="alert_series_tick"
)

_scheduler.start()
log.info(f"[Alerts] Scheduler started — daily cycle at {ALERT_CHECK_TIME}, one series checked every {ALERT_CHECK_INTERVAL}m.")


def _get_next_daily_run():
    """Return the next scheduled daily cycle time as an ISO string, or None."""
    job = _scheduler.get_job("alert_daily_cycle")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%I:%M %p").lstrip("0")
    return None


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def search():
    books = []
    query = ""
    try:
        if request.method == "POST":
            query = request.form.get("query", "").strip()
            books = search_audiobookbay(query)
        return render_template(
            "search.html", books=books, query=query,
            save_path_base=SAVE_PATH_BASE or "",
            page_limit=PAGE_LIMIT
        )
    except RuntimeError as e:
        if str(e) == "rate_limited":
            error_msg = ("AudioBookBay has rate limited this IP. "
                         "Try again later or route traffic through a VPN.")
        else:
            error_msg = str(e)
        return render_template(
            "search.html", books=books, error=error_msg, query=query,
            save_path_base=SAVE_PATH_BASE or "", page_limit=PAGE_LIMIT
        )
    except Exception as e:
        log.error(f"Failed to search: {e}")
        return render_template(
            "search.html", books=books, error=f"Failed to search. {str(e)}",
            query=query, save_path_base=SAVE_PATH_BASE or "", page_limit=PAGE_LIMIT
        )


@app.route("/search_more", methods=["POST"])
def search_more():
    data       = request.json
    query      = data.get("query", "").strip()
    start_page = int(data.get("start_page", 1))
    try:
        books = search_audiobookbay(query, max_pages=PAGE_LIMIT, start_page=start_page)
        return jsonify({"books": books, "has_more": len(books) > 0})
    except RuntimeError as e:
        if str(e) == "rate_limited":
            return jsonify({"error": "Rate limited by AudioBookBay. Try again later or use a VPN."}), 429
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/send", methods=["POST"])
def send():
    data        = request.json
    details_url = data.get("link")
    title       = data.get("title")
    if not details_url or not title:
        return jsonify({"message": "Invalid request"}), 400

    try:
        magnet_link = extract_magnet_link(details_url)
        if not magnet_link:
            return jsonify({"message": "Failed to extract magnet link"}), 500

        skip_series     = data.get("skip_series", False)
        series_override = data.get("series_override", "").strip()
        safe_title      = sanitize_title(title)

        if skip_series:
            save_path = f"{SAVE_PATH_BASE}/{safe_title}"
        else:
            series    = sanitize_title(series_override) if series_override else sanitize_title(get_series_name(title))
            save_path = f"{SAVE_PATH_BASE}/{series}/{safe_title}" if series != safe_title else f"{SAVE_PATH_BASE}/{safe_title}"

        if DOWNLOAD_CLIENT == "qbittorrent":
            qb = Client(host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD)
            qb.auth_log_in()
            qb.torrents_add(urls=magnet_link, save_path=save_path, category=DL_CATEGORY)
        elif DOWNLOAD_CLIENT == "transmission":
            transmission = transmissionrpc(
                host=DL_HOST, port=DL_PORT, protocol=DL_SCHEME,
                username=DL_USERNAME, password=DL_PASSWORD,
            )
            transmission.add_torrent(magnet_link, download_dir=save_path)
        elif DOWNLOAD_CLIENT == "delugeweb":
            delugeweb = delugewebclient(url=DL_URL, password=DL_PASSWORD)
            delugeweb.login()
            torrent_options = delugetorrentoptions(
                download_location=save_path, label=DL_CATEGORY
            )
            delugeweb.add_torrent_magnet(magnet_link, torrent_options=torrent_options)
        else:
            return jsonify({"message": "Unsupported download client"}), 400

        return jsonify({"message": "Download added successfully! This may take some time, "
                                   "the download will show in Audiobookshelf when completed."})
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route("/status")
def status():
    try:
        if DOWNLOAD_CLIENT == "transmission":
            transmission = transmissionrpc(
                host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD
            )
            torrents     = transmission.get_torrents()
            torrent_list = [
                {
                    "name":     torrent.name,
                    "progress": round(torrent.progress, 2),
                    "state":    torrent.status,
                    "size":     f"{torrent.total_size / (1024 * 1024):.2f} MB",
                }
                for torrent in torrents
            ]
            return render_template("status.html", torrents=torrent_list)
        elif DOWNLOAD_CLIENT == "qbittorrent":
            qb = Client(host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD)
            qb.auth_log_in()
            torrents     = qb.torrents_info(category=DL_CATEGORY)
            torrent_list = [
                {
                    "name":     torrent.name,
                    "progress": round(torrent.progress * 100, 2),
                    "state":    torrent.state,
                    "size":     f"{torrent.total_size / (1024 * 1024):.2f} MB",
                }
                for torrent in torrents
            ]
            return render_template("status.html", torrents=torrent_list)
        elif DOWNLOAD_CLIENT == "delugeweb":
            delugeweb = delugewebclient(url=DL_URL, password=DL_PASSWORD)
            delugeweb.login()
            torrents     = delugeweb.get_torrents_status(
                filter_dict={"label": DL_CATEGORY},
                keys=["name", "state", "progress", "total_size"],
            )
            torrent_list = [
                {
                    "name":     torrent["name"],
                    "progress": round(torrent["progress"], 2),
                    "state":    torrent["state"],
                    "size":     f"{torrent['total_size'] / (1024 * 1024):.2f} MB",
                }
                for k, torrent in torrents.result.items()
            ]
        else:
            return jsonify({"message": "Unsupported download client"}), 400
        return render_template("status.html", torrents=torrent_list)
    except Exception as e:
        return jsonify({"message": f"Failed to fetch torrent status: {e}"}), 500


# ── Favorites helpers ──────────────────────────────────────────────────────
def load_favorites():
    if os.path.exists(FAVORITES_PATH):
        with open(FAVORITES_PATH) as f:
            return json.load(f)
    return []


def save_favorites(favs):
    with open(FAVORITES_PATH, "w") as f:
        json.dump(favs, f, indent=2)


# ── Series map helpers ─────────────────────────────────────────────────────
def load_series_map():
    if os.path.exists(SERIES_MAP_PATH):
        try:
            with open(SERIES_MAP_PATH) as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            return {}
    return {}


def save_series_map(mapping):
    with open(SERIES_MAP_PATH, "w") as f:
        json.dump(mapping, f, indent=2)


# ── Favorites routes ───────────────────────────────────────────────────────
@app.route("/favorites")
def get_favorites():
    return jsonify({"favorites": load_favorites()})


@app.route("/favorites/preview", methods=["POST"])
def favorites_preview():
    """Return extracted series name, mapping info, and disk scan for Save Series modal."""
    data  = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "No title provided"}), 400

    extracted   = sanitize_title(_extract_series_raw(title))
    series_map  = load_series_map()
    is_mapped   = extracted in series_map
    mapped_to   = series_map.get(extracted)  # what the mapping resolves to, if any

    # Check if series folder already exists on disk using extracted name
    disk_path = None
    if SCAN_PATH_BASE:
        search_name = mapped_to if mapped_to else extracted
        found = _find_series_folder(SCAN_PATH_BASE, search_name)
        if found:
            disk_path = found

    # Check if already in favorites (check both extracted and mapped name)
    favs = load_favorites()
    already_saved = extracted in favs or (mapped_to and mapped_to in favs)

    return jsonify({
        "success":       True,
        "extracted":     extracted,
        "is_mapped":     is_mapped,
        "mapped_to":     mapped_to,
        "disk_path":     disk_path,
        "already_saved": already_saved,
    })


@app.route("/favorites/add_with_options", methods=["POST"])
def add_favorite_with_options():
    """Add to favorites with optional name edit and alert toggle. Never creates mappings."""
    data          = request.json
    series_name   = sanitize_title(data.get("series_name", "").strip())
    enable_alerts = data.get("enable_alerts", False)

    if not series_name:
        return jsonify({"success": False, "message": "No series name provided"}), 400

    # Add to favorites if not already there
    favs = load_favorites()
    if series_name not in favs:
        favs.append(series_name)
        favs.sort()
        save_favorites(favs)

    # Enable alerts if requested
    if enable_alerts:
        alerts = load_alerts()
        if series_name not in alerts:
            alerts[series_name] = {}
        alerts[series_name]["enabled"] = True
        save_alerts(alerts)
        log.info(f"[Favorites] Alerts enabled for '{series_name}'")

    return jsonify({"success": True, "series": series_name})


@app.route("/favorites/add", methods=["POST"])
def add_favorite():
    data  = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "No title provided"}), 400

    series = sanitize_title(get_series_name(title))
    if not series:
        return jsonify({"success": False, "message": "Could not extract series name"}), 400

    favs = load_favorites()
    if series in favs:
        return jsonify({"success": False, "message": "Already saved"})

    favs.append(series)
    favs.sort()
    save_favorites(favs)
    return jsonify({"success": True, "series": series})


@app.route("/favorites/add_manual", methods=["POST"])
def add_favorite_manual():
    data = request.json
    name = sanitize_title(data.get("name", "").strip())
    if not name:
        return jsonify({"success": False, "message": "No name provided"}), 400
    favs = load_favorites()
    if name not in favs:
        favs.append(name)
        favs.sort()
        save_favorites(favs)
    return jsonify({"success": True})


@app.route("/favorites/remove", methods=["POST"])
def remove_favorite():
    data = request.json
    name = data.get("name", "").strip()
    favs = load_favorites()
    favs = [f for f in favs if f != name]
    save_favorites(favs)
    # Also clean up alerts entry for this series
    alerts = load_alerts()
    alerts.pop(name, None)
    save_alerts(alerts)
    return jsonify({"success": True})


@app.route("/favorites/rename", methods=["POST"])
def rename_favorite():
    data     = request.json
    old_name = data.get("old_name", "").strip()
    new_name = sanitize_title(data.get("new_name", "").strip())
    if not old_name or not new_name:
        return jsonify({"success": False}), 400
    favs = load_favorites()
    if old_name in favs:
        idx       = favs.index(old_name)
        favs[idx] = new_name
        favs.sort()
        save_favorites(favs)
    # Migrate alerts entry to new name
    alerts = load_alerts()
    if old_name in alerts:
        alerts[new_name] = alerts.pop(old_name)
        save_alerts(alerts)
    return jsonify({"success": True})


# ── Series map routes ──────────────────────────────────────────────────────
@app.route("/mappings")
def mappings_page():
    return render_template("mappings.html")


@app.route("/mappings/list")
def list_mappings():
    return jsonify({"mappings": load_series_map()})


@app.route("/mappings/preview", methods=["POST"])
def preview_mapping():
    data  = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "No title provided"}), 400
    raw       = sanitize_title(_extract_series_raw(title))
    extracted = sanitize_title(get_series_name(title))
    is_mapped = (raw in load_series_map())
    return jsonify({"success": True, "extracted": extracted, "is_mapped": is_mapped})


@app.route("/mappings/add", methods=["POST"])
def add_mapping():
    data      = request.json
    extracted = data.get("extracted", "").strip()
    mapped    = data.get("mapped", "").strip()
    if not extracted or not mapped:
        return jsonify({"success": False, "message": "Both fields required"}), 400
    mapping             = load_series_map()
    mapping[extracted]  = mapped
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/remove", methods=["POST"])
def remove_mapping():
    data    = request.json
    key     = data.get("key", "").strip()
    mapping = load_series_map()
    mapping.pop(key, None)
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/rename", methods=["POST"])
def rename_mapping():
    data          = request.json
    key           = data.get("key", "").strip()
    new_extracted = data.get("new_extracted", "").strip()
    new_mapped    = data.get("new_mapped", "").strip()
    if not key or not new_extracted or not new_mapped:
        return jsonify({"success": False}), 400
    mapping = load_series_map()
    if key in mapping:
        mapping.pop(key)
    mapping[new_extracted] = new_mapped
    save_series_map(mapping)
    return jsonify({"success": True})


# ── Alert routes ───────────────────────────────────────────────────────────
@app.route("/alerts/status")
def alerts_status():
    """Return alert state for all favorites."""
    alerts = load_alerts()
    favs   = load_favorites()
    result = {}
    for series in favs:
        data = alerts.get(series, {})
        result[series] = {
            "enabled":       data.get("enabled", False),
            "notifications": data.get("notifications", []),
            "last_checked":  data.get("last_checked"),
        }
    return jsonify(result)


@app.route("/alerts/run_now", methods=["POST"])
def alerts_run_now():
    """Manually trigger a check cycle immediately, bypassing the daily schedule."""
    if _alert_series_queue:
        return jsonify({
            "success": False,
            "message": "A check is already running."
        }), 409

    started = _alert_cycle_start()
    if not started:
        return jsonify({
            "success": False,
            "message": "No series have alerts enabled."
        })

    log.info("[Alerts] Manual check triggered by user.")
    return jsonify({"success": True, "total": _alert_cycle_total})


@app.route("/alerts/cycle_status")
def alerts_cycle_status():
    """Return whether a check cycle is currently running and its progress."""
    running   = len(_alert_series_queue) > 0
    remaining = len(_alert_series_queue)
    total     = _alert_cycle_total if running else 0
    checked   = (total - remaining) if running else 0

    return jsonify({
        "running":            running,
        "total":              total,
        "checked":            checked,
        "remaining":          remaining,
        "next_run_at":        _get_next_daily_run(),
        "check_interval_min": ALERT_CHECK_INTERVAL,
    })


@app.route("/alerts/toggle", methods=["POST"])
def alerts_toggle():
    """Enable or disable alerts for a specific series."""
    data    = request.json
    series  = data.get("series", "").strip()
    enabled = data.get("enabled", False)
    if not series:
        return jsonify({"success": False}), 400

    alerts = load_alerts()
    if series not in alerts:
        alerts[series] = {}
    alerts[series]["enabled"] = enabled
    save_alerts(alerts)
    log.info(f"[Alerts] {'Enabled' if enabled else 'Disabled'} alerts for '{series}'.")
    return jsonify({"success": True})


@app.route("/alerts/dismiss", methods=["POST"])
def alerts_dismiss():
    """Dismiss a specific notification URL and add it to the blocklist."""
    data   = request.json
    series = data.get("series", "").strip()
    url    = data.get("url", "").strip()
    title  = data.get("title", "").strip()
    matched_as = data.get("matched_as", "").strip()
    if not series or not url:
        return jsonify({"success": False}), 400

    # Remove from notifications
    alerts = load_alerts()
    if series in alerts:
        alerts[series]["notifications"] = [
            n for n in alerts[series].get("notifications", [])
            if n.get("url") != url
        ]
        save_alerts(alerts)

    # Add to blocklist
    blocklist = load_blocklist()
    if not any(e.get("url") == url for e in blocklist):
        blocklist.append({
            "url":        url,
            "title":      title,
            "matched_as": matched_as,
            "series":     series,
            "blocked_at": datetime.utcnow().strftime("%Y-%m-%d"),
        })
        save_blocklist(blocklist)

    return jsonify({"success": True})


@app.route("/alerts/dismiss_all", methods=["POST"])
def alerts_dismiss_all():
    """Dismiss all notifications for a series and block all their URLs."""
    data   = request.json
    series = data.get("series", "").strip()
    if not series:
        return jsonify({"success": False}), 400

    alerts = load_alerts()
    notifications = alerts.get(series, {}).get("notifications", [])

    blocklist = load_blocklist()
    blocked_urls = {e.get("url") for e in blocklist}
    for n in notifications:
        if n.get("url") not in blocked_urls:
            blocklist.append({
                "url":        n.get("url"),
                "title":      n.get("title"),
                "matched_as": n.get("matched_as"),
                "series":     series,
                "blocked_at": datetime.utcnow().strftime("%Y-%m-%d"),
            })
    save_blocklist(blocklist)

    if series in alerts:
        alerts[series]["notifications"] = []
        save_alerts(alerts)

    return jsonify({"success": True})


@app.route("/alerts/clear_all", methods=["POST"])
def alerts_clear_all():
    """Clear all notifications for a series without adding to blocklist."""
    data   = request.json
    series = data.get("series", "").strip()
    if not series:
        return jsonify({"success": False}), 400

    alerts = load_alerts()
    if series in alerts:
        alerts[series]["notifications"] = []
        save_alerts(alerts)

    log.info(f"[Alerts] Cleared notifications for '{series}' (no blocklist).")
    return jsonify({"success": True})


@app.route("/alerts/force_check/<path:series>")
def alerts_force_check(series):
    """Immediately run a real ABB check for a specific series outside the scheduler."""
    favs = load_favorites()
    if series not in favs:
        return jsonify({"success": False, "message": f"'{series}' is not in your favorites."}), 404

    alerts = load_alerts()
    if series not in alerts:
        alerts[series] = {"enabled": True}
        save_alerts(alerts)

    log.info(f"[Alerts] Force check triggered for '{series}'.")
    _check_series_for_new_volume(series, load_alerts())
    updated = load_alerts().get(series, {})
    notifications = updated.get("notifications", [])
    log.info(f"[Alerts] Force check complete for '{series}' — {len(notifications)} notification(s).")
    return jsonify({
        "success":       True,
        "series":        series,
        "notifications": notifications,
        "message":       f"Check complete. {len(notifications)} new volume(s) found."
    })


@app.route("/alerts/test/<path:series>")
def alerts_test(series):
    """Inject fake notifications for testing the UI.
    Optional query param: ?count=N (default 1, max 10)
    Example: /alerts/test/My Series?count=3
    """
    try:
        count = max(1, min(10, int(request.args.get("count", 1))))
    except (ValueError, TypeError):
        count = 1

    alerts = load_alerts()
    if series not in alerts:
        alerts[series] = {"enabled": True}
    existing     = alerts[series].get("notifications", [])
    existing_urls = {n.get("url") for n in existing}

    added = 0
    for i in range(count):
        vol_num  = 97 + i   # 97, 98, 99 etc. so they look distinct
        fake_url = f"https://{ABB_HOSTNAME}/test-notification-{vol_num}-do-not-download/"
        if fake_url not in existing_urls:
            existing.append({
                "url":        fake_url,
                "title":      f"{series} Vol. {vol_num} — TEST NOTIFICATION",
                "matched_as": f"Vol. {vol_num}",
                "found_at":   datetime.utcnow().strftime("%Y-%m-%d"),
            })
            existing_urls.add(fake_url)
            added += 1

    alerts[series]["notifications"] = existing
    save_alerts(alerts)
    log.info(f"[Alerts] Injected {added} test notification(s) for '{series}'.")
    return jsonify({"success": True, "message": f"Injected {added} test notification(s) for '{series}'"})


@app.route("/alerts/test_clear/<path:series>")
def alerts_test_clear(series):
    """Clear all test notifications for a series without adding to blocklist."""
    alerts = load_alerts()
    if series in alerts:
        alerts[series]["notifications"] = [
            n for n in alerts[series].get("notifications", [])
            if "test-notification" not in n.get("url", "")
        ]
        save_alerts(alerts)
    log.info(f"[Alerts] Cleared test notifications for '{series}'.")
    return jsonify({"success": True})


# ── Blocklist routes ───────────────────────────────────────────────────────
@app.route("/blocklist")
def get_blocklist():
    return jsonify({"blocklist": load_blocklist()})


@app.route("/blocklist/remove", methods=["POST"])
def remove_from_blocklist():
    data = request.json
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False}), 400
    blocklist = [e for e in load_blocklist() if e.get("url") != url]
    save_blocklist(blocklist)
    return jsonify({"success": True})


# ── Volume existence check ─────────────────────────────────────────────────
@app.route("/check_exists", methods=["POST"])
def check_exists():
    data   = request.json
    title  = data.get("title", "").strip()
    series = data.get("series", "").strip()

    if not SCAN_PATH_BASE or not title:
        return jsonify({"exists": False})

    vol_num = extract_vol_num(title)
    if not vol_num:
        return jsonify({"exists": False})

    try:
        num_int  = int(float(vol_num))
        variants = list(set([
            str(num_int),
            vol_num,
            str(num_int).zfill(2),
            str(num_int).zfill(3),
        ]))
    except ValueError:
        variants = [vol_num]

    safe_series = sanitize_title(series) if series else ""
    if safe_series:
        scan_path = _find_series_folder(SCAN_PATH_BASE, safe_series)
        if not scan_path:
            return jsonify({"exists": False})
    else:
        scan_path = SCAN_PATH_BASE
        if not os.path.isdir(scan_path):
            return jsonify({"exists": False})

    try:
        for entry in os.scandir(scan_path):
            if not entry.is_dir():
                continue
            for v in variants:
                pattern = "(?<![0-9])" + re.escape(v) + "(?![0-9])"
                if re.search(pattern, entry.name):
                    return jsonify({"exists": True, "match": entry.path})
    except PermissionError:
        pass

    return jsonify({"exists": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
