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
import logging.handlers

_LOG_DIR  = "/config"
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")
os.makedirs(_LOG_DIR, exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

# Rotating file handler — caps at 5MB per file, keeps 3 backups (app.log, app.log.1, app.log.2)
# so it persists across restarts via /config but never grows unbounded.
_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
log = logging.getLogger(__name__)

# APScheduler logs every single job execution at INFO level (including no-op ticks),
# which floods the log with routine heartbeat noise. Quiet it to WARNING so only
# real problems (missed jobs, errors) show up — our own [Alerts] log lines are unaffected.
logging.getLogger("apscheduler").setLevel(logging.WARNING)

app = Flask(__name__)

# Load environment variables
# Load from /config/.env first (user-editable), then fall back to container env vars
_config_env = os.path.join("/config", ".env")
if os.path.exists(_config_env):
    load_dotenv(_config_env, override=True)
else:
    load_dotenv()

ABB_HOSTNAME = os.getenv("ABB_HOSTNAME", "audiobookbay.lu").strip().strip("'\"")
# Every URL built later already prepends "https://" itself, so a value that
# already includes a scheme (someone pasting a full URL instead of just the
# domain, e.g. "https://audiobookbay.lu") would otherwise double up into a
# broken "https://https://..." URL. Strip it if present.
ABB_HOSTNAME = re.sub(r"^https?://", "", ABB_HOSTNAME, flags=re.IGNORECASE).strip().strip("'\"").rstrip("/")


def _get_int_env(name, default):
    """Read an integer env var, falling back to `default` (with a warning,
    not a crash) if it's missing, empty, or not actually a valid number.
    A typo in an optional setting should degrade gracefully, not take the
    whole app down at startup — same principle already applied below to
    ALERT_CHECK_TIME, just generalized to the numeric settings too."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        log.warning(f"Invalid {name} value '{raw}' — expected a whole number. Falling back to: {default}")
        return default


def _get_float_env(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        log.warning(f"Invalid {name} value '{raw}' — expected a number. Falling back to: {default}")
        return default


PAGE_LIMIT   = _get_int_env("PAGE_LIMIT", 2)

DOWNLOAD_CLIENT = os.getenv("DOWNLOAD_CLIENT")
if DOWNLOAD_CLIENT:
    # Case shouldn't matter here, but exact-match comparisons against this
    # value assume lowercase throughout the rest of the file. Worth doing
    # since even upstream's own Unraid template default capitalizes it
    # ("Deluge|qBittorrent|Transmission"), which previously broke downloads
    # entirely with an "Unsupported download client" error for anyone who
    # left that capitalization as-is.
    DOWNLOAD_CLIENT = DOWNLOAD_CLIENT.strip().lower()
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
REQUEST_DELAY   = _get_float_env("REQUEST_DELAY", 0.5)

# qBittorrent-only per-torrent seeding limits, applied at add-time (mirrors
# how Sonarr/Radarr work around qBittorrent having no per-category ratio
# setting the way Deluge does). Left unset by default so qBittorrent's own
# global seeding limit setting applies, exactly as if this app weren't
# specifying anything at all.
QB_RATIO_LIMIT = _get_float_env("QB_RATIO_LIMIT", None)
QB_SEED_TIME_LIMIT_MIN = _get_int_env("QB_SEED_TIME_LIMIT_MIN", None)

NAV_LINK_NAME = os.getenv("NAV_LINK_NAME")
NAV_LINK_URL  = os.getenv("NAV_LINK_URL")
FLASK_PORT    = _get_int_env("PORT", 5078)

# Dev panel — testing tools tucked into Search/Status/Mappings (fake search
# results, fake alerts, fake torrent rows, etc). Off by default since it's
# only useful while actively developing, not for normal use.
DEV_PANEL = os.getenv("DEV_PANEL", "false").strip().lower() == "true"

# Alert scheduler config
ALERT_CHECK_INTERVAL = _get_int_env("ALERT_CHECK_INTERVAL", 5)   # minutes between each series check within a cycle
ALERT_CHECK_TIME     = os.getenv("ALERT_CHECK_TIME", "02:00")       # time of day to run daily cycle (HH:MM, 24hr)
ALERT_CHECK_PAGES    = _get_int_env("ALERT_CHECK_PAGES", 1)         # pages of ABB search results to check per series

# Discord notifications — off entirely unless a webhook URL is set. The
# automated daily cycle always notifies when a URL is present; manual checks
# (Check Now, per-series refresh) only notify if explicitly opted into below,
# so testing/spot-checking a series doesn't spam Discord by default.
DISCORD_WEBHOOK_URL         = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_NOTIFY_MANUAL_CHECKS = os.getenv("DISCORD_NOTIFY_MANUAL_CHECKS", "false").strip().lower() == "true"

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
log.info(f"QB_RATIO_LIMIT: {QB_RATIO_LIMIT if QB_RATIO_LIMIT is not None else 'not set (qBittorrent default)'}")
log.info(f"QB_SEED_TIME_LIMIT_MIN: {QB_SEED_TIME_LIMIT_MIN if QB_SEED_TIME_LIMIT_MIN is not None else 'not set (qBittorrent default)'}")
log.info(f"NAV_LINK_NAME: {NAV_LINK_NAME}")
log.info(f"NAV_LINK_URL: {NAV_LINK_URL}")
log.info(f"PAGE_LIMIT: {PAGE_LIMIT}")
log.info(f"PORT: {FLASK_PORT}")
log.info(f"DEV_PANEL: {DEV_PANEL}")
log.info(f"ALERT_CHECK_INTERVAL: {ALERT_CHECK_INTERVAL}m")
log.info(f"ALERT_CHECK_TIME: {ALERT_CHECK_TIME}")
log.info(f"ALERT_CHECK_PAGES: {ALERT_CHECK_PAGES}")
log.info(f"DISCORD_WEBHOOK_URL: {'set' if DISCORD_WEBHOOK_URL else 'not set (Discord notifications disabled)'}")
log.info(f"DISCORD_NOTIFY_MANUAL_CHECKS: {DISCORD_NOTIFY_MANUAL_CHECKS}")

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

# Pages to load per search and per Load More click    [default: 2]
# PAGE_LIMIT=2

# Delay in seconds between page fetches               [default: 0.5]
# Increase if you get rate limited, decrease if behind a VPN
# REQUEST_DELAY=0.5

# ── Download client ───────────────────────────────────────────────────────
# Uncomment ONE block below depending on your torrent client.  [required]

# ── qBittorrent ──
# DOWNLOAD_CLIENT=qbittorrent
# DL_HOST=qbittorrent       # container name and internal port, or IP and host-mapped port
# DL_PORT=8080
# DL_USERNAME=admin
# DL_PASSWORD=password
# Optional per-torrent seeding limits (qBittorrent has no per-category
# default the way Deluge does). Leave both unset to use qBittorrent's own
# global seeding limit setting instead.                     [optional]
# QB_RATIO_LIMIT=1.5
# QB_SEED_TIME_LIMIT_MIN=4320

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

# Pages of ABB search results to check per series      [default: 1]
# ABB's search results aren't always sorted strictly newest-first, so a series
# with a lot of existing entries could occasionally have a new volume land
# past page 1. Raising this checks further back at the cost of an extra
# request (spaced by REQUEST_DELAY) per additional page, per series checked.
# ALERT_CHECK_PAGES=1

# ── Discord notifications ───────────────────────────────────────────────────
# Send a Discord message when a new volume is found for a favorited series.
# Leave unset to disable — no other Discord setting does anything without this.  [optional]
# DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/xxxx

# Also notify for manually-triggered checks (Check Now, per-series refresh) —
# not just the automated daily cycle.                        [default: false]
# DISCORD_NOTIFY_MANUAL_CHECKS=true

# ── Developer / Testing ───────────────────────────────────────────────────
# Shows a hidden developer panel on Search/Status/Mappings with tools for
# faking search results, alerts, torrent rows, and error states — useful
# while testing UI changes, not needed for normal use.  [default: false]
# DEV_PANEL=true

# ── Logging ───────────────────────────────────────────────────────────────
# In addition to container logs, a rotating log file is written to
# /config/app.log (capped at 5MB, keeps 3 backups) for persistent debugging
# across container restarts. No configuration needed — this is automatic.
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
        "dev_panel":     DEV_PANEL,
        "download_client": DOWNLOAD_CLIENT,
        "qb_ratio_limit": QB_RATIO_LIMIT,
        "qb_seed_time_limit_min": QB_SEED_TIME_LIMIT_MIN,
    }


def _static_versioned(filename):
    """
    Build a static file URL with a cache-busting query param based on the
    file's last-modified time. Ensures browsers fetch fresh CSS/JS after a
    container rebuild instead of serving a stale cached copy.
    """
    static_path = os.path.join(app.static_folder, filename)
    try:
        mtime = int(os.path.getmtime(static_path))
    except OSError:
        mtime = 0
    from flask import url_for
    return f"{url_for('static', filename=filename)}?v={mtime}"


app.jinja_env.globals["static_versioned"] = _static_versioned


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
            # WordPress 301-redirects /page/1/ back to the bare root, since page 1
            # of pagination is canonically the same as the homepage itself. Hitting
            # /page/1/ directly here would return a 301 instead of the actual
            # listing, so page 1 of "new releases" goes straight to the root.
            if page == 1:
                url = f"https://{ABB_HOSTNAME}/"
            else:
                url = f"https://{ABB_HOSTNAME}/page/{page}/"

        if page > start_page:
            time.sleep(REQUEST_DELAY)

        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to fetch page {page}. Reason: {e}")
            # Only treat this as fatal if it's the very first page and nothing
            # has been gathered yet — a hiccup on a later page still returns
            # whatever was already found, same as before. A first-page failure
            # previously fell through silently to an empty result list with no
            # indication anything went wrong; surface it instead.
            if page == start_page and not results:
                raise RuntimeError("connection_failed")
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
    # For anything that will become a filesystem path — folder names,
    # save_path construction, disk lookups. Colons are replaced (not
    # deleted) since they're a meaningful subtitle boundary ("Series V:
    # Subtitle") that volume extraction relies on — deleting them outright
    # would silently break re-parsing a folder name later, once it's the
    # only thing left representing that title on disk.
    # For anything NOT going to touch the filesystem — the Save Series
    # modal's editable name, ABB search queries, alert matching — use
    # clean_display_title() below instead. Running this on those values
    # was replacing every colon with " -", which both looks wrong and
    # actively hurts ABB search relevance, since ABB's search is sensitive
    # to exact punctuation (see the in-app search-tips tooltip).
    title = title.replace(":", " -")
    title = re.sub(r'[<>"/\\|?*]', "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def clean_display_title(title):
    # Companion to sanitize_title() above, for values that stay as text —
    # never become a folder name — so nothing here needs to change to
    # satisfy filesystem rules. Only whitespace left over from extraction
    # gets tidied; punctuation (colons included) stays exactly as ABB
    # itself wrote it.
    return re.sub(r"\s+", " ", title).strip()


# ── Roman numeral support ────────────────────────────────────────────────
# Strict standard-form pattern (no repeated subtractive pairs, no invalid
# combos like IIII or VX). Requires at least one valid roman letter via the
# lookahead so it can never match an empty string.
_ROMAN_RE = r"(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s):
    """Convert a validated Roman numeral string to an int, or None if empty/invalid."""
    if not s:
        return None
    total = 0
    prev = 0
    for ch in reversed(s.upper()):
        val = _ROMAN_VALUES.get(ch)
        if val is None:
            return None
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total if total > 0 else None


def _match_roman_token(text, at_start):
    """
    Match a standalone, ALL-UPPERCASE Roman numeral token at the start or
    end of `text`. Uppercase-only is a deliberate guard: Roman numerals only
    use letters (M/D/C/L/X/V/I) that also spell a handful of real English
    words ("MIX", "MI", etc). Requiring the token to appear fully uppercase
    in the original title — the normal convention for volume markers like
    "Book VII" — makes an accidental collision with an ordinary word rare
    rather than a routine hazard.

    Returns (matched_text, int_value) or None.
    """
    if at_start:
        m = re.match(rf"^({_ROMAN_RE})(?=\s)", text)
    else:
        m = re.search(rf"(?<!\S)({_ROMAN_RE})\s*$", text)
    if not m:
        return None
    token = m.group(1)
    if not token.isupper():
        return None
    val = _roman_to_int(token)
    if val is None:
        return None
    return (m, val)


# ── Spelled-out volume number support ────────────────────────────────────
# Covers "Book Four", "Vol. Twenty-Two", etc. Only fires right after a
# Vol/Book/Part/Year keyword (see _WORD_NUM_RE usage in tier 2) — never as
# a standalone bare-word tier — since ordinary English words far outnumber
# valid volume-number words and would produce constant false matches
# without a keyword anchoring them.
_ONES_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ALL_NUMBER_WORDS = sorted(list(_ONES_WORDS) + list(_TENS_WORDS), key=len, reverse=True)
# Matches "twenty", "twenty-two", "twenty two", "four", etc. Longest words
# first in the alternation so "seventeen" is tried before "seven" can grab
# a partial match out of it.
_WORD_NUM_RE = (
    r"(?:" + "|".join(_ALL_NUMBER_WORDS) + r")"
    r"(?:[\s-]+(?:" + "|".join(_ONES_WORDS) + r"))?"
)


def _word_to_number(text):
    """Convert a spelled-out number ('four', 'twenty-two', 'twenty two') to
    an int, or None if it isn't a valid one/teens/tens(+ones) combination."""
    tokens = re.split(r"[\s-]+", text.strip().lower())
    if len(tokens) == 1:
        return _ONES_WORDS.get(tokens[0]) or _TENS_WORDS.get(tokens[0])
    if len(tokens) == 2 and tokens[0] in _TENS_WORDS and tokens[1] in _ONES_WORDS and _ONES_WORDS[tokens[1]] < 10:
        return _TENS_WORDS[tokens[0]] + _ONES_WORDS[tokens[1]]
    return None


# ── Unified series name / volume number parser ──────────────────────────
# Single source of truth for splitting an ABB (or sanitized on-disk folder)
# title into (series_name, volume_number). Both _extract_series_raw and
# extract_vol_num are built on top of this so they can never disagree about
# where the series name ends and the volume number begins.
#
# Tiers are tried in order, first match wins:
#   1. Bracket number:        "Series [16]"
#   2. Keyword + number:      "Series Vol. 16" / "Series, Book 16"
#   3. Bare digit/Roman at the true end of the title:
#      "Series 16" / "Series XVI"
#   4. Colon fallback (only if nothing above matched anywhere):
#      "Series 16: Subtitle" / "Series: Subtitle"
#   5. Dash fallback (only if nothing above matched anywhere):
#      "Series 16 - Subtitle" / "Series XVI - Subtitle"
#
# Deliberately no "bare digit/Roman at the START of the title" tier. A
# series's own real name commonly starts with a number ("12 Miles Below",
# "1632"), indistinguishable from a genuine leading-volume-number
# convention ("16 Series Title") by string shape alone — and unlike the
# other tiers, this one can misfire even for an UNNUMBERED entry in such a
# series (nothing else to match, so it'd guess the series' own leading
# number is that book's volume). Titles relying on a leading-volume
# convention just return no volume detected, same as any other unmatched
# format.
#
# Parenthetical asides — "(Unabridged)", "(A Progression Fantasy Epic)",
# "(Series, Book 5)" — are stripped before any tier is tried. On ABB these
# are reliably supplementary disambiguation, never the primary series/volume
# declaration, and leaving them in lets a keyword match buried inside one
# (e.g. "book 5" inside a parenthetical) hijack the split before an
# authoritative marker earlier in the title (e.g. a Roman numeral right
# after the real series name) ever gets a chance.
def _parse_series_and_volume(title):
    authorless = title.rsplit(" - ", 1)[0].strip() if " - " in title else title.strip()

    # Strip supplementary noise before any tier is tried: parenthetical
    # asides ("(Unabridged)", "(A Progression Fantasy Epic)") and
    # non-numeric bracket tags ABB uploaders commonly append to flag a
    # re-upload or fix ("[Updated]", "[Retail]", "[FIXED]", "[REPOST]").
    # A purely numeric bracket like "[16]" is deliberately left alone here
    # — that's tier 1's real volume marker, not noise.
    stripped = re.sub(r"\([^)]*\)", " ", authorless)
    stripped = re.sub(r"\[(?![0-9]+(?:[.][0-9]+)?\])[^\]]*\]", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    working = stripped if stripped else authorless

    # ── Tier 1: bracket number ──
    m = re.search(r"\s*\[([0-9]+(?:[.][0-9]+)?)\]\s*", working)
    if m:
        vol = m.group(1)
        series = (working[:m.start()] + working[m.end():]).strip().rstrip("-,:").strip()
        return (series or authorless, vol)

    # ── Tier 2: keyword + number, including "Books N-M" ranges ──
    # Range form checked first: an omnibus like "Books 1-12" uses the
    # HIGHEST number in the range as the volume, since that's what actually
    # determines whether a given release is already covered by what's on
    # disk (owning "Books 1-12" means volume 12 and everything under it is
    # covered). Keyword group accepts an optional trailing "s" throughout
    # (Books/Vols/Volumes/Parts/Years) since ranges are almost always
    # phrased in the plural, and this doesn't change matching for the
    # ordinary singular case either.
    range_matches = list(re.finditer(
        r"[:,]?\s*(?:Vol(?:ume)?s?[.]?|Books?|Parts?|Years?)[ ]+([0-9]+)\s*-\s*([0-9]+)",
        working, re.IGNORECASE
    ))
    if range_matches:
        vol = range_matches[-1].group(2)  # highest number in the range
        series = working[:range_matches[0].start()].strip().rstrip(",").strip()
        return (series or authorless, vol)

    matches = list(re.finditer(
        r"[:,]?\s*(?:Vol(?:ume)?s?[.]?|Books?|Parts?|Years?)[ ]+([0-9]+(?:[.][0-9]+)?)",
        working, re.IGNORECASE
    ))
    if matches:
        vol = matches[-1].group(1)  # last match wins for the number itself
        series = working[:matches[0].start()].strip().rstrip(",").strip()
        return (series or authorless, vol)

    # Same keyword group, but for a spelled-out number ("Book Four",
    # "Vol. Twenty-Two") instead of a digit. Kept as its own pass rather
    # than folded into the digit regex above so the digit form — the far
    # more common case — stays a simple, fast, easy-to-read pattern on its
    # own; this only runs at all when no digit form matched anywhere.
    word_matches = list(re.finditer(
        rf"[:,]?\s*(?:Vol(?:ume)?s?[.]?|Books?|Parts?|Years?)[ ]+({_WORD_NUM_RE})\b",
        working, re.IGNORECASE
    ))
    if word_matches:
        vol = _word_to_number(word_matches[-1].group(1))
        if vol is not None:
            series = working[:word_matches[0].start()].strip().rstrip(",").strip()
            return (series or authorless, str(vol))

    # ── Tier 3: bare digit or Roman numeral at the true end only ──
    # (?<!,) guards against thousands-grouped numbers in a real series name
    # — e.g. "Warhammer 40,000" must never have "000" read as a volume
    # number just because it's the trailing digits after a comma.
    # Start-of-title bare numbers are intentionally NOT checked here — see
    # tier 6 below for why.
    m = re.search(r"(?<![0-9])(?<!,)([0-9]+(?:[.][0-9]+)?)\s*$", working)
    if m:
        series = working[:m.start()].strip()
        if series:  # never let a bare number consume the entire title (e.g. a series literally titled "1632")
            return (series, m.group(1))

    roman = _match_roman_token(working, at_start=False)
    if roman:
        m, val = roman
        series = working[:m.start()].strip()
        if series:  # never let a numeral consume the entire title (e.g. a series literally titled "V")
            return (series, str(val))

    # ── Tier 4: colon fallback ──
    if ":" in working:
        before = working.split(":", 1)[0].strip()
        vol = None
        m = re.search(r"(?<![0-9])(?<!,)([0-9]+(?:[.][0-9]+)?)\s*$", before)
        if m:
            vol = m.group(1)
            before = before[:m.start()].strip()
        else:
            roman = _match_roman_token(before, at_start=False)
            if roman:
                m, val = roman
                candidate = before[:m.start()].strip()
                if candidate:
                    vol = str(val)
                    before = candidate
        if before:
            return (before, vol)

    # ── Tier 5: dash fallback ──
    m = re.search(r"(?<![0-9])(?<!,)([0-9]+(?:[.][0-9]+)?)\s+-\s", working)
    if m:
        series = working[:m.start()].strip()
        if series:
            return (series, m.group(1))

    m = re.search(rf"(?<!\S)({_ROMAN_RE})\s+-\s", working)
    if m and m.group(1).isupper():
        val = _roman_to_int(m.group(1))
        series = working[:m.start()].strip()
        if val and series:
            return (series, str(val))

    # No "bare digit/Roman at the start of the title" tier: a series's own
    # real name commonly starts with a number ("12 Miles Below", "1632"),
    # which is indistinguishable from a genuine leading-volume-number
    # convention ("16 Series Title") by string shape alone. This tier used
    # to exist as an absolute last resort, but even placed last it still
    # misread the series' own leading number as a phantom volume for any
    # UNNUMBERED entry in such a series (nothing else to match, so it fell
    # through to this guess) — e.g. book 1 of "12 Miles Below" registering
    # as volume 12. Titles relying on a genuine leading-volume convention
    # now return no volume at all instead, the same graceful fallback as
    # any other unmatched format — a manual series mapping covers it if
    # actually needed, same as any other extraction gap.
    return (working, None)


def _extract_series_raw(title):
    series, _ = _parse_series_and_volume(title)
    return series


def _match_keyword_mapping(title):
    """
    Check every registered keyword mapping against the raw title. Returns
    (matched_keyword, folder_name) for the longest (most specific) match,
    or (None, None) if nothing matches. Shared by get_series_name() (which
    uses the folder_name directly) and favorites_preview() (which uses
    this purely to show an informational banner, independent of it).
    """
    keyword_map = load_series_map()
    if not keyword_map:
        return (None, None)
    title_lower = title.lower()
    matches = [kw for kw in keyword_map.keys() if kw and kw.lower() in title_lower]
    if not matches:
        return (None, None)
    best = max(matches, key=len)
    return (best, keyword_map[best])


def get_series_name(title):
    """
    Resolve the folder name a download's series-level directory should use.

    Checks every registered keyword mapping against the RAW title first —
    a keyword is a substring the user has told the app to watch for
    anywhere in a title ("Star Wars"), not a specific extracted series
    name to match exactly. This is what lets unrelated-looking titles in
    the same franchise ("Star Wars - Specter of the Past...", "Star Wars -
    Heir to the Empire...") land in the same folder without extraction
    ever needing to figure out they're related on its own, which it
    fundamentally can't for a sprawling franchise with no shared title
    structure.

    Falls through to the normal tiered extraction (_extract_series_raw)
    untouched if no keyword matches at all.
    """
    _, folder_name = _match_keyword_mapping(title)
    if folder_name:
        return folder_name

    series = _extract_series_raw(title)
    if not series:
        series = title

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
    """
    Extract a volume number from a title string. Returns a plain digit
    string or None. Thin wrapper around _parse_series_and_volume so this
    can never disagree with series-name extraction about where the volume
    marker is — both are driven by the exact same tier logic.
    """
    _, vol = _parse_series_and_volume(t)
    return vol


def extract_vol_num_known_series(text, known_series):
    """
    Extract a volume number from `text` when the real series name is
    ALREADY KNOWN (e.g. checking a favorited series against disk or a live
    ABB result) rather than being guessed from scratch. This is used
    instead of extract_vol_num() specifically because knowing the real
    series name in advance resolves an ambiguity the generic parser can't:
    a bare leading number/Roman numeral is indistinguishable from part of
    the series' own name ("12 Miles Below") when nothing else is known —
    but once the known series name is stripped off the front, whatever's
    left over is unambiguously NOT the title, so a leading number there is
    safe to treat as a volume marker.

    This also works retroactively on folder names already on disk whose
    subtitle-boundary punctuation was lost before this app started
    preserving it (e.g. "12 Miles Below II A House Reborn...", saved back
    when colons were deleted rather than replaced) — no renaming needed,
    since we're no longer relying on that punctuation being present at all.

    Falls back to extract_vol_num() if the known series name doesn't
    actually lead `text`, or if nothing follows it.
    """
    known_series = (known_series or "").strip()
    if not known_series or not text.lower().startswith(known_series.lower()):
        return extract_vol_num(text)

    remainder = text[len(known_series):].strip().lstrip(" -:,")
    if not remainder:
        return extract_vol_num(text)

    m = re.match(r"^([0-9]+(?:[.][0-9]+)?)(?:\s|$)", remainder)
    if m:
        return m.group(1)

    roman = _match_roman_token(remainder, at_start=True) or _match_roman_token(remainder, at_start=False)
    if roman:
        _, val = roman
        return str(val)

    return extract_vol_num(text)


def _get_highest_vol_on_disk(series_name):
    """Return the highest volume number found on disk for a series, or -1 if none found."""
    if not SCAN_PATH_BASE:
        return -1

    # If a keyword mapping applies to this favorite name, the series-level
    # folder on disk may actually be named differently (e.g. a favorite of
    # "He Who Fights With Monsters" whose downloads are keyword-mapped
    # into a folder called "HWFWM") — use the mapped folder name to LOCATE
    # the right folder, but keep using the real favorite name below for
    # extracting volume numbers from what's inside it, since each
    # individual book's own folder name still reflects the real title
    # text, not the keyword-mapped parent folder's name.
    _, mapped_folder = _match_keyword_mapping(series_name) if series_name else (None, None)
    folder_search_name = mapped_folder or series_name

    safe_series = sanitize_title(folder_search_name) if folder_search_name else ""
    if safe_series:
        scan_path = _find_series_folder(SCAN_PATH_BASE, safe_series)
    else:
        scan_path = SCAN_PATH_BASE

    if not scan_path or not os.path.isdir(scan_path):
        return -1

    highest    = -1
    has_unnumbered_entry = False
    try:
        for entry in os.scandir(scan_path):
            if not entry.is_dir():
                continue
            vol = extract_vol_num_known_series(entry.name, series_name)
            if vol:
                try:
                    highest = max(highest, int(float(vol)))
                except ValueError:
                    has_unnumbered_entry = True
            else:
                has_unnumbered_entry = True
    except PermissionError:
        pass

    # A series' first entry commonly has no explicit volume marker at all
    # ("Series Name: Subtitle" with nothing else on disk to compare against)
    # — extract_vol_num_known_series correctly returns no volume for it,
    # same as it should. But leaving highest at -1 here is indistinguishable
    # from "nothing on disk for this series," which breaks the >= 0 check in
    # _check_series_for_new_volume: a real, higher-numbered volume 2 posted
    # to ABB would never be flagged, because the comparison never runs.
    # If the folder has content but none of it parsed to a number, treat
    # that as an implicit volume 1 baseline — owning an unnumbered entry
    # means owning at least the first one, never "nothing."
    if highest < 0 and has_unnumbered_entry:
        highest = 1

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


# ── Alert scheduler ────────────────────────────────────────────────────────
_alert_series_queue = []
_alert_cycle_total  = 0   # total series in the current/last cycle, for progress display
_alert_cycle_notify = True  # whether THIS cycle should send Discord pings — set once at
                             # cycle start and read by every later tick, since the tick job
                             # itself runs with no arguments and has no other way to know
                             # whether the cycle it's draining was automated or manual.

def _alert_cycle_start(manual=False):
    """
    Called once per day at ALERT_CHECK_TIME (automated, manual=False), or manually
    via /alerts/run_now (manual=True). Builds the queue of enabled series and
    immediately processes the first one so a manual trigger doesn't sit idle
    waiting for the next interval tick. The rest of the queue is drained by
    _alert_tick on its normal stagger.
    The interval job itself is only registered while a cycle is actually running,
    so the scheduler logs stay quiet between cycles instead of ticking every
    ALERT_CHECK_INTERVAL minutes forever with nothing to do.
    """
    global _alert_series_queue, _alert_cycle_total, _alert_cycle_notify

    alerts  = load_alerts()
    enabled = [s for s, v in alerts.items() if v.get("enabled")]
    if not enabled:
        log.info("[Alerts] Cycle triggered but no series have alerts enabled.")
        return False

    # Automated cycles always notify (if a webhook is configured). Manual
    # "Check Now" runs only notify if the user has explicitly opted into that.
    _alert_cycle_notify = DISCORD_NOTIFY_MANUAL_CHECKS if manual else True

    _alert_series_queue = list(enabled)
    _alert_cycle_total  = len(_alert_series_queue)
    log.info(f"[Alerts] Cycle started ({'manual' if manual else 'automated'}) — {_alert_cycle_total} series queued.")

    # Process the first series right away rather than waiting for the next tick
    first_series = _alert_series_queue.pop(0)
    _check_series_for_new_volume(first_series, alerts, notify=_alert_cycle_notify)

    # If more remain, start the stagger tick job; otherwise the cycle is already done
    if _alert_series_queue:
        _ensure_tick_job_running()
    else:
        log.info("[Alerts] Cycle complete — only one series was queued.")

    return True


def _ensure_tick_job_running():
    """Register the per-series stagger job if it isn't already active."""
    if _scheduler.get_job("alert_series_tick") is None:
        _scheduler.add_job(
            _alert_tick, "interval",
            minutes=ALERT_CHECK_INTERVAL,
            id="alert_series_tick"
        )
        log.info(f"[Alerts] Stagger tick started — checking one series every {ALERT_CHECK_INTERVAL}m.")


def _alert_tick():
    """
    Called every ALERT_CHECK_INTERVAL minutes while a cycle is in progress.
    Processes one series from the queue so ABB is never hit in a burst.
    Removes itself once the queue is fully drained so it doesn't keep
    ticking (and logging) with nothing to do.
    """
    global _alert_series_queue

    if not _alert_series_queue:
        _scheduler.remove_job("alert_series_tick")
        return

    alerts = load_alerts()
    series = _alert_series_queue.pop(0)
    _check_series_for_new_volume(series, alerts, notify=_alert_cycle_notify)

    if not _alert_series_queue:
        log.info("[Alerts] Cycle complete — all series checked.")
        _scheduler.remove_job("alert_series_tick")


def _send_discord_notification(series, title, link, vol_int):
    """
    Best-effort Discord ping for a newly found volume. Uses the app's own
    (trusted, user-set) series name rather than anything re-parsed out of the
    ABB title, since ABB's titles are inconsistently formatted and the
    favorited series name is already known-clean. Never allowed to break the
    actual alert-finding logic — any failure here is just logged and skipped.
    Returns True/False so callers that DO care about success (e.g. the
    "test webhook" button) can report it accurately.
    """
    if not DISCORD_WEBHOOK_URL:
        return False

    payload = {
        "embeds": [
            {
                "title": title,
                "url": link,
                "color": 5793266,  # Discord blurple
                "fields": [
                    {"name": "Series", "value": series, "inline": True},
                    {"name": "Volume", "value": f"Vol. {vol_int}", "inline": True},
                ],
            }
        ]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return True
    except requests.exceptions.RequestException as e:
        log.warning(f"[Alerts] Failed to send Discord notification for '{series}': {e}")
        return False


def _check_series_for_new_volume(series, alerts, notify=True):
    """
    Search ABB (ALERT_CHECK_PAGES pages, default 1) for a series and flag any
    volumes higher than what's on disk. notify controls whether a newly-found
    volume also triggers a Discord webhook call (if one is configured) —
    callers pass False to keep a check silent.
    """
    log.info(f"[Alerts] Checking '{series}' for new volumes...")

    try:
        posts = search_audiobookbay(series, max_pages=ALERT_CHECK_PAGES, start_page=1)
    except RuntimeError as e:
        reason = str(e)
        if reason == "rate_limited":
            log.warning(f"[Alerts] Rate limited while checking '{series}'. Will retry next cycle.")
        else:
            log.error(f"[Alerts] Failed to fetch ABB for '{series}': {reason}")
        return
    except requests.exceptions.RequestException as e:
        log.error(f"[Alerts] Failed to fetch ABB for '{series}': {e}")
        return

    highest_on_disk = _get_highest_vol_on_disk(series)
    blocklist       = load_blocklist()
    blocked_urls    = {e.get("url") for e in blocklist}

    alerts = load_alerts()
    series_data = alerts.get(series, {})
    existing_notifications = {n["url"] for n in series_data.get("notifications", [])}
    new_notifications = list(series_data.get("notifications", []))

    for post in posts:
        try:
            title = post["title"]
            link  = post["link"]

            if link in blocked_urls or link in existing_notifications:
                continue

            # Relevance gate: extract_vol_num_known_series() only uses the
            # series name to pick a PARSING STRATEGY (strip-prefix vs.
            # generic fallback) — it was never actually a check that this
            # search result belongs to the series at all. A result that
            # doesn't even contain the series name could still fall through
            # to the generic parser and produce a number that gets compared
            # against your disk baseline. Require the (normalized) series
            # name to actually appear in the title before any of that
            # parsing is trusted.
            if _normalize_for_fuzzy(series) not in _normalize_for_fuzzy(title):
                continue

            vol = extract_vol_num_known_series(title, series)
            if vol is None:
                continue

            try:
                vol_int = int(float(vol))
            except ValueError:
                continue

            # highest_on_disk == -1 means nothing is owned for this series yet
            # (no folder found, or an empty one) — in that case, ANY volume
            # ABB turns up is new by definition, so there's no floor to
            # compare against. -1 < any real volume number, so this falls
            # out of a plain > comparison without needing a separate branch.
            if vol_int > highest_on_disk:
                log.info(f"[Alerts] New volume found for '{series}': {title} (Vol {vol_int} > disk {highest_on_disk})")
                new_notifications.append({
                    "url":        link,
                    "title":      title,
                    "matched_as": f"Vol. {vol_int}",
                    "found_at":   datetime.utcnow().strftime("%Y-%m-%d"),
                    "on_disk":    highest_on_disk,
                })
                if notify:
                    _send_discord_notification(series, title, link, vol_int)
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

# Daily cycle trigger — fires once per day at the configured time.
# The per-series stagger job (alert_series_tick) is registered dynamically by
# _ensure_tick_job_running() only while a cycle has series left to check, and
# removes itself once drained — see _alert_tick.
_scheduler.add_job(
    _alert_cycle_start, "cron",
    hour=_alert_hour, minute=_alert_minute,
    id="alert_daily_cycle"
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
        elif str(e) == "connection_failed":
            error_msg = (f"Couldn't reach AudioBookBay at '{ABB_HOSTNAME}'. "
                         "Double-check ABB_HOSTNAME in your configuration, or the site may be temporarily down.")
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
        if str(e) == "connection_failed":
            return jsonify({"error": f"Couldn't reach AudioBookBay at '{ABB_HOSTNAME}'. "
                                      "Double-check ABB_HOSTNAME in your configuration, or the site may be temporarily down."}), 502
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
            qb.torrents_add(
                urls=magnet_link, save_path=save_path, category=DL_CATEGORY,
                ratio_limit=QB_RATIO_LIMIT, seeding_time_limit=QB_SEED_TIME_LIMIT_MIN
            )
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
                    "ratio":    round(torrent.ratio, 2),
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
                    "ratio":    round(torrent.ratio, 2),
                }
                for torrent in torrents
            ]
            return render_template("status.html", torrents=torrent_list)
        elif DOWNLOAD_CLIENT == "delugeweb":
            delugeweb = delugewebclient(url=DL_URL, password=DL_PASSWORD)
            delugeweb.login()
            torrents     = delugeweb.get_torrents_status(
                filter_dict={"label": DL_CATEGORY},
                keys=["name", "state", "progress", "total_size", "ratio"],
            )
            torrent_list = [
                {
                    "name":     torrent["name"],
                    "progress": round(torrent["progress"], 2),
                    "state":    torrent["state"],
                    "size":     f"{torrent['total_size'] / (1024 * 1024):.2f} MB",
                    "ratio":    round(torrent["ratio"], 2),
                }
                for k, torrent in torrents.result.items()
            ]
        else:
            return render_template("status.html", torrents=[],
                                    error=f"Unsupported download client: {DOWNLOAD_CLIENT}")
        return render_template("status.html", torrents=torrent_list)
    except Exception as e:
        log.error(f"Failed to fetch torrent status: {e}")
        return render_template("status.html", torrents=[],
                                error=f"Failed to fetch torrent status: {e}")


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


# ── Case-insensitive favorite matching ─────────────────────────────────────
# ABB titles aren't consistently capitalized ("He Who Fights With Monsters"
# vs "He Who Fights with Monsters"), so the same real-world series can
# extract to slightly different casing depending on which listing it came
# from. Every "already saved" / "already exists" check needs to compare
# case-insensitively, or near-duplicate entries silently pile up — each
# with its own separate alert toggle and notification history.
# ── Case-insensitive name matching (shared) ─────────────────────────────────
# ABB titles aren't consistently capitalized ("He Who Fights With Monsters"
# vs "He Who Fights with Monsters"), so the same real-world series can
# extract to slightly different casing depending on which listing it came
# from. Used anywhere a human/extracted series name is compared as a
# dictionary key or list membership — favorites, alerts, and series
# mappings — since exact-match comparison there silently creates
# duplicates or, for mappings, can silently skip an existing mapping.
def _find_case_insensitive(name, collection):
    """Return the existing entry in `collection` that matches `name`
    case-insensitively, or None if there's no match."""
    target = name.lower()
    for entry in collection:
        if entry.lower() == target:
            return entry
    return None


def _migrate_case_duplicate_favorites():
    """
    One-time startup cleanup for the case-sensitivity bug: earlier code
    matched series names case-sensitively everywhere, which allowed two
    distinct problems to accumulate:

      1. Duplicate favorites entries for the same real series under
         different casing (e.g. "He Who Fights with Monsters" and
         "He Who Fights With Monsters" both saved separately).

      2. Orphaned alerts.json entries that don't match any current
         favorite's casing — or don't match any favorite at all. The
         favorites panel only ever renders rows from favorites.json, and
         the bell only ever reads/writes whichever alerts.json key
         exactly matches a favorite's current casing. So an alerts entry
         under a different casing (or with no matching favorite at all)
         becomes fully invisible in the UI, yet the alert scheduler reads
         alerts.json directly and keeps checking every "enabled" key
         regardless of whether it's actually visible or controllable —
         i.e. it silently keeps running with no way to turn it off.

    This reconciles both files down to one canonical entry per real
    series. Any alerts.json key with no matching favorite at all is kept
    (not silently discarded, since it may hold real notification
    history) by restoring it as a visible favorite rather than deleting
    it — logged clearly so it can be manually removed if unwanted.
    """
    favs   = load_favorites()
    alerts = load_alerts()

    # Group existing favorites by lowercase name to find same-series
    # duplicates, and build a lowercase -> canonical-casing map.
    fav_groups = {}
    for f in favs:
        fav_groups.setdefault(f.lower(), []).append(f)

    rename_map = {}       # lowercase key -> canonical display name
    canonical_favs = []
    for variants in fav_groups.values():
        canonical = next((v for v in variants if alerts.get(v, {}).get("enabled")), sorted(variants)[0])
        canonical_favs.append(canonical)
        for v in variants:
            rename_map[v.lower()] = canonical

    # Any alerts.json key with no matching favorite at all (under any
    # casing) is an orphan — most likely from this exact bug. Restore it
    # as a real, visible favorite instead of silently dropping its data.
    restored = []
    for name in alerts.keys():
        if name.lower() not in rename_map:
            rename_map[name.lower()] = name
            canonical_favs.append(name)
            restored.append(name)

    canonical_favs = sorted(set(canonical_favs))

    # Merge alerts.json down to one entry per canonical name.
    merged_alerts = {}
    for name, data in alerts.items():
        canonical = rename_map.get(name.lower(), name)
        if canonical not in merged_alerts:
            merged_alerts[canonical] = data
            continue
        existing = merged_alerts[canonical]
        existing["enabled"] = existing.get("enabled") or data.get("enabled")
        existing_urls = {n.get("url") for n in existing.get("notifications", [])}
        for n in data.get("notifications", []):
            if n.get("url") not in existing_urls:
                existing.setdefault("notifications", []).append(n)
                existing_urls.add(n.get("url"))
        if data.get("last_checked", "") > existing.get("last_checked", ""):
            existing["last_checked"] = data.get("last_checked")

    if canonical_favs == sorted(favs) and merged_alerts == alerts:
        return  # everything already reconciled, nothing to write

    save_favorites(canonical_favs)
    save_alerts(merged_alerts)

    log.info(f"[Migration] Reconciled favorite/alert entries: {canonical_favs}")
    if restored:
        log.warning(
            "[Migration] Restored favorite(s) from alert entries that had no "
            f"matching favorite (remove manually via the UI if unwanted): {restored}"
        )


_migrate_case_duplicate_favorites()


def _warn_case_duplicate_mappings():
    """
    Startup check for series_map.json — logs a warning only, doesn't
    auto-merge. Unlike favorites/alerts, a mapping's value is a folder
    name the user deliberately chose, and two case-variant keys could
    genuinely have different intended values. Silently picking one over
    the other risks quietly changing where future downloads land, so
    this just surfaces the conflict for manual review on the Series
    Mappings page instead.
    """
    mapping = load_series_map()
    groups = {}
    for k in mapping.keys():
        groups.setdefault(k.lower(), []).append(k)

    for variants in groups.values():
        if len(variants) > 1:
            log.warning(
                "[Migration] Series mapping has multiple case-variant entries "
                f"for the same extracted name — review on the Series Mappings "
                f"page and remove the ones you don't want: {variants}"
            )


_warn_case_duplicate_mappings()


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

    raw_extracted = _extract_series_raw(title)
    # This is what's shown/edited in the modal and what actually ends up
    # saved as the series name for ABB search & alert matching — kept as
    # close to ABB's own title text as possible (colon intact), since that
    # text is what gets searched later. A separate, filesystem-safe version
    # is used below only for checking whether a folder already exists on
    # disk, since that's the one place here that's actually a path.
    extracted = clean_display_title(raw_extracted)
    matched_keyword, mapped_to = _match_keyword_mapping(title)
    is_mapped = matched_keyword is not None

    # Check if series folder already exists on disk using extracted name
    disk_path = None
    if SCAN_PATH_BASE:
        search_name = mapped_to if mapped_to else sanitize_title(raw_extracted)
        found = _find_series_folder(SCAN_PATH_BASE, search_name)
        if found:
            disk_path = found

    # Check if already in favorites (check both extracted and mapped name,
    # case-insensitively — see _find_case_insensitive)
    favs = load_favorites()
    already_saved = bool(_find_case_insensitive(extracted, favs)) or (
        bool(mapped_to) and bool(_find_case_insensitive(mapped_to, favs))
    )

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
    series_name   = clean_display_title(data.get("series_name", "").strip())
    enable_alerts = data.get("enable_alerts", False)

    if not series_name:
        return jsonify({"success": False, "message": "No series name provided"}), 400

    # Reuse an existing favorite if one already exists under a different
    # case, rather than creating a duplicate entry (see
    # _find_case_insensitive for why this matters).
    favs     = load_favorites()
    existing = _find_case_insensitive(series_name, favs)
    already_existed = bool(existing)
    if existing:
        series_name = existing
    else:
        favs.append(series_name)
        favs.sort()
        save_favorites(favs)

    # Enable alerts if requested — reuse an existing alerts entry under a
    # different case if one exists, instead of creating a second one.
    if enable_alerts:
        alerts = load_alerts()
        alert_key = _find_case_insensitive(series_name, list(alerts.keys())) or series_name
        if alert_key not in alerts:
            alerts[alert_key] = {}
        alerts[alert_key]["enabled"] = True
        save_alerts(alerts)
        log.info(f"[Favorites] Alerts enabled for '{alert_key}'")

    return jsonify({"success": True, "series": series_name, "already_existed": already_existed})


@app.route("/favorites/add_manual", methods=["POST"])
def add_favorite_manual():
    data = request.json
    name = clean_display_title(data.get("name", "").strip())
    if not name:
        return jsonify({"success": False, "message": "No name provided"}), 400
    favs = load_favorites()
    existing = _find_case_insensitive(name, favs)
    if existing:
        return jsonify({"success": True, "already_existed": True, "series": existing})
    favs.append(name)
    favs.sort()
    save_favorites(favs)
    return jsonify({"success": True, "already_existed": False, "series": name})


@app.route("/favorites/remove", methods=["POST"])
def remove_favorite():
    data = request.json
    name = data.get("name", "").strip()
    favs = load_favorites()
    favs = [f for f in favs if f.lower() != name.lower()]
    save_favorites(favs)
    # Also clean up alerts entry for this series — case-insensitively, so a
    # stray differently-cased alerts key can't survive as an invisible
    # orphan that keeps running in the background forever.
    alerts = load_alerts()
    for key in list(alerts.keys()):
        if key.lower() == name.lower():
            alerts.pop(key, None)
    save_alerts(alerts)
    return jsonify({"success": True})


@app.route("/favorites/rename", methods=["POST"])
def rename_favorite():
    data     = request.json
    old_name = data.get("old_name", "").strip()
    new_name = clean_display_title(data.get("new_name", "").strip())
    if not old_name or not new_name:
        return jsonify({"success": False}), 400

    favs = load_favorites()

    # If the new name collides case-insensitively with a *different*
    # existing favorite, merge into that one instead of creating a
    # second duplicate entry.
    other_favs = [f for f in favs if f != old_name]
    target     = _find_case_insensitive(new_name, other_favs) or new_name

    if old_name in favs:
        favs = [f for f in favs if f != old_name]
        if target not in favs:
            favs.append(target)
        favs.sort()
        save_favorites(favs)

    # Migrate alerts entry to the target name, merging rather than
    # overwriting if an alerts entry already exists there.
    alerts = load_alerts()
    if old_name in alerts:
        old_data = alerts.pop(old_name)
        if target in alerts:
            alerts[target]["enabled"] = alerts[target].get("enabled") or old_data.get("enabled")
            existing_urls = {n.get("url") for n in alerts[target].get("notifications", [])}
            for n in old_data.get("notifications", []):
                if n.get("url") not in existing_urls:
                    alerts[target].setdefault("notifications", []).append(n)
                    existing_urls.add(n.get("url"))
        else:
            alerts[target] = old_data
        save_alerts(alerts)

    return jsonify({"success": True, "series": target})


# ── Series map routes ──────────────────────────────────────────────────────
@app.route("/mappings")
def mappings_page():
    return render_template("mappings.html")


@app.route("/mappings/list")
def list_mappings():
    return jsonify({"mappings": load_series_map()})


@app.route("/mappings/preview", methods=["POST"])
def preview_mapping():
    """Used by the Download modal to pre-fill the series field — returns
    whatever get_series_name() currently resolves to for this title,
    whether that comes from a matching keyword or normal extraction, plus
    which keyword matched (if any) so the modal can show why."""
    data  = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "No title provided"}), 400
    matched_keyword, _ = _match_keyword_mapping(title)
    series_name = sanitize_title(get_series_name(title))
    return jsonify({"success": True, "series_name": series_name, "matched_keyword": matched_keyword})


@app.route("/mappings/add", methods=["POST"])
def add_mapping():
    data     = request.json
    keyword  = data.get("keyword", "").strip()
    folder_name = data.get("folder_name", "").strip()
    if not keyword or not folder_name:
        return jsonify({"success": False, "message": "Both fields required"}), 400
    mapping = load_series_map()
    # If this keyword already exists under different casing, update it
    # rather than creating a second, conflicting entry.
    existing_key = _find_case_insensitive(keyword, mapping.keys())
    if existing_key and existing_key != keyword:
        mapping.pop(existing_key)
    mapping[keyword] = folder_name
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/remove", methods=["POST"])
def remove_mapping():
    data    = request.json
    keyword = data.get("keyword", "").strip()
    mapping = load_series_map()
    existing_key = _find_case_insensitive(keyword, mapping.keys())
    if existing_key:
        mapping.pop(existing_key, None)
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/rename", methods=["POST"])
def rename_mapping():
    data            = request.json
    keyword         = data.get("keyword", "").strip()
    new_keyword     = data.get("new_keyword", "").strip()
    new_folder_name = data.get("new_folder_name", "").strip()
    if not keyword or not new_keyword or not new_folder_name:
        return jsonify({"success": False}), 400
    mapping = load_series_map()

    old_key = _find_case_insensitive(keyword, mapping.keys())
    if old_key:
        mapping.pop(old_key)

    # If the new keyword collides case-insensitively with a different
    # existing keyword, replace that entry rather than ending up with two
    # keys effectively watching for the same text.
    collision_key = _find_case_insensitive(new_keyword, mapping.keys())
    if collision_key:
        mapping.pop(collision_key)

    mapping[new_keyword] = new_folder_name
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


@app.route("/alerts/discord_status")
def alerts_discord_status():
    """Whether a Discord webhook is currently configured (never the URL itself)."""
    return jsonify({"configured": bool(DISCORD_WEBHOOK_URL)})


@app.route("/alerts/discord_test", methods=["POST"])
def alerts_discord_test():
    """
    Send a sample notification through the exact same function real alerts
    use, so a successful test genuinely confirms the real path works —
    not a separate mock that could drift from it.
    """
    if not DISCORD_WEBHOOK_URL:
        return jsonify({"success": False, "message": "No Discord webhook is configured."}), 400

    ok = _send_discord_notification("Test Series", "This is a test notification from AudioBookBay Automated", f"https://{ABB_HOSTNAME}", 1)
    if ok:
        log.info("[Alerts] Discord test notification sent.")
        return jsonify({"success": True, "message": "Test notification sent — check Discord."})

    return jsonify({"success": False, "message": "Failed to reach Discord. Check the webhook URL and container logs."}), 502


@app.route("/alerts/run_now", methods=["POST"])
def alerts_run_now():
    """Manually trigger a check cycle immediately, bypassing the daily schedule."""
    if _alert_series_queue:
        return jsonify({
            "success": False,
            "message": "A check is already running."
        }), 409

    started = _alert_cycle_start(manual=True)
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
    key = _find_case_insensitive(series, list(alerts.keys())) or series
    if key not in alerts:
        alerts[key] = {}
    alerts[key]["enabled"] = enabled
    save_alerts(alerts)
    log.info(f"[Alerts] {'Enabled' if enabled else 'Disabled'} alerts for '{key}'.")
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
    _check_series_for_new_volume(series, load_alerts(), notify=DISCORD_NOTIFY_MANUAL_CHECKS)
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


@app.route("/alerts/test_clear_all")
def alerts_test_clear_all():
    """Clear all test notifications across every series, without touching real ones."""
    alerts  = load_alerts()
    cleared = 0
    for series, data in alerts.items():
        before = len(data.get("notifications", []))
        data["notifications"] = [
            n for n in data.get("notifications", [])
            if "test-notification" not in n.get("url", "")
        ]
        cleared += before - len(data["notifications"])
    save_alerts(alerts)
    log.info(f"[Alerts] Cleared {cleared} test notification(s) across all series.")
    return jsonify({
        "success": True,
        "cleared": cleared,
        "message": f"Cleared {cleared} test notification(s) across all series."
    })


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

    vol_num = extract_vol_num_known_series(title, series) if series else extract_vol_num(title)
    if not vol_num:
        return jsonify({"exists": False})

    try:
        target_int = int(float(vol_num))
    except ValueError:
        return jsonify({"exists": False})

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
            # Extract each folder's own volume number using the same
            # known-series-aware logic used for disk comparison elsewhere,
            # rather than searching for the target digit as raw text
            # inside the folder name — that approach only ever worked by
            # coincidence for a folder whose subtitle happened to contain
            # a literal matching number, and silently missed anything
            # using Roman numerals (e.g. "VI") since there's no digit "6"
            # anywhere in that text to find.
            entry_vol = extract_vol_num_known_series(entry.name, series) if series else extract_vol_num(entry.name)
            if entry_vol is None:
                continue
            try:
                entry_int = int(float(entry_vol))
            except ValueError:
                continue
            if entry_int == target_int:
                return jsonify({"exists": True, "match": entry.path})
    except PermissionError:
        pass

    return jsonify({"exists": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
