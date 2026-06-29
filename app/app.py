import os
import pwd
import grp
import json
import re
import time
import requests
from flask import Flask, request, render_template, jsonify
from bs4 import BeautifulSoup
from qbittorrentapi import Client
from transmission_rpc import Client as transmissionrpc
from deluge_web_client import DelugeWebClient as delugewebclient
from deluge_web_client import TorrentOptions as delugetorrentoptions
from dotenv import load_dotenv
from urllib.parse import urlparse

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
SCAN_PATH_BASE  = os.getenv("SCAN_PATH_BASE", SAVE_PATH_BASE)  # Falls back to SAVE_PATH_BASE
REQUEST_DELAY   = float(os.getenv("REQUEST_DELAY", "0.75"))

NAV_LINK_NAME = os.getenv("NAV_LINK_NAME")
NAV_LINK_URL  = os.getenv("NAV_LINK_URL")
FLASK_PORT    = int(os.getenv("PORT", 5078))

print(f"ABB_HOSTNAME: {ABB_HOSTNAME}")
print(f"DOWNLOAD_CLIENT: {DOWNLOAD_CLIENT}")
print(f"DL_HOST: {DL_HOST}")
print(f"DL_PORT: {DL_PORT}")
print(f"DL_URL: {DL_URL}")
print(f"DL_USERNAME: {DL_USERNAME}")
print(f"DL_CATEGORY: {DL_CATEGORY}")
print(f"SAVE_PATH_BASE: {SAVE_PATH_BASE}")
print(f"SCAN_PATH_BASE: {SCAN_PATH_BASE}")
print(f"REQUEST_DELAY: {REQUEST_DELAY}")
print(f"NAV_LINK_NAME: {NAV_LINK_NAME}")
print(f"NAV_LINK_URL: {NAV_LINK_URL}")
print(f"PAGE_LIMIT: {PAGE_LIMIT}")
print(f"PORT: {FLASK_PORT}")

# ── Config / persistent data ───────────────────────────────────────────────
CONFIG_DIR      = "/config"
FAVORITES_PATH  = os.path.join(CONFIG_DIR, "favorites.json")
SERIES_MAP_PATH = os.path.join(CONFIG_DIR, "series_map.json")

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
""")
    print(f"[INFO] Created config template at {_env_template}")

if not os.path.exists(FAVORITES_PATH):
    with open(FAVORITES_PATH, "w") as f:
        json.dump([], f)
if not os.path.exists(SERIES_MAP_PATH):
    with open(SERIES_MAP_PATH, "w") as f:
        json.dump({}, f)

try:
    nobody    = pwd.getpwnam("nobody")
    users_gid = grp.getgrnam("users").gr_gid
    for _path in [FAVORITES_PATH, SERIES_MAP_PATH]:
        os.chown(_path, nobody.pw_uid, users_gid)
        os.chmod(_path, 0o664)  # rw-rw-r-- owner and group can read/write
    if os.path.exists(_env_template):
        os.chown(_env_template, nobody.pw_uid, users_gid)
        os.chmod(_env_template, 0o664)
except Exception as e:
    print(f"[WARN] Could not set config file ownership: {e}")


@app.context_processor
def inject_nav_link():
    return {
        "nav_link_name": os.getenv("NAV_LINK_NAME"),
        "nav_link_url":  os.getenv("NAV_LINK_URL"),
    }


# ── Scraper helpers ────────────────────────────────────────────────────────
# Structural markers we expect on a real ABB page
_ABB_REQUIRED_MARKERS = [".post", ".postTitle", "#sidebar"]

def _page_looks_valid(soup):
    """Return True if the page has the structure we expect from ABB."""
    for marker in _ABB_REQUIRED_MARKERS:
        if soup.select(marker):
            return True
    return False

def _page_is_rate_limited(soup, response):
    """Detect common signs of rate limiting or IP bans."""
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
        print(f"Searching for '{query}' on https://{ABB_HOSTNAME}...")
    else:
        print(f"Fetching new releases from https://{ABB_HOSTNAME}...")

    for page in range(start_page, start_page + max_pages):
        if query:
            url = (f"https://{ABB_HOSTNAME}/page/{page}/"
                   f"?s={requests.utils.quote(query.lower().replace(' ', '+'), safe='+')}")
        else:
            url = f"https://{ABB_HOSTNAME}/page/{page}/"

        # Polite delay between pages to avoid rate limiting
        if page > start_page:
            time.sleep(REQUEST_DELAY)

        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch page {page}. Reason: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        # Check for rate limiting / ban first
        if _page_is_rate_limited(soup, response):
            print(f"[WARNING] Rate limited or banned on page {page}. "
                  f"Status: {response.status_code}.")
            raise RuntimeError("rate_limited")

        if response.status_code != 200:
            print(f"[ERROR] Page {page} returned HTTP {response.status_code}. Stopping.")
            break

        # Verify page structure looks like ABB
        if not _page_looks_valid(soup):
            print(f"[WARNING] Page {page} doesn't look like a valid ABB page — "
                  f"structure may have changed.")
            break

        posts = soup.select(".post")
        if not posts:
            print(f"No more results found on page {page}.")
            break

        print(f"Processing {len(posts)} posts on page {page}...")

        for post in posts:
            try:
                title_element = post.select_one(".postTitle > h2 > a")
                if not title_element:
                    continue

                title = title_element.text.strip()
                link  = f"https://{ABB_HOSTNAME}{title_element['href']}"

                # Use cover URL directly — browser handles broken images via onerror
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
                print(f"[ERROR] Could not process a post. Details: {e}")
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
            print(f"[ERROR] Failed to fetch details page. Status Code: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        info_hash_row = soup.find("td", string=re.compile(r"Info Hash", re.IGNORECASE))
        if not info_hash_row:
            print("[ERROR] Info Hash not found on the page.")
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

        trackers_query = "&".join(
            f"tr={requests.utils.quote(t)}" for t in trackers
        )
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}&{trackers_query}"
        print(f"[DEBUG] Generated Magnet Link: {magnet_link}")
        return magnet_link

    except Exception as e:
        print(f"[ERROR] Failed to extract magnet link: {e}")
        return None


# ── Title / series helpers ─────────────────────────────────────────────────
def sanitize_title(title):
    return re.sub(r'[<>:"/\\|?*]', "", title).strip()


def _extract_series_raw(title):
    """Extract series name before any mapping is applied."""
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
    """Extract series name and apply any custom mapping."""
    series = _extract_series_raw(title)
    if not series:
        series = title

    # Check custom series map
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
        print(f"[ERROR] Failed to search: {e}")
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


# ── Volume existence check ─────────────────────────────────────────────────
@app.route("/check_exists", methods=["POST"])
def check_exists():
    """Fuzzy-check if a volume already exists on disk."""
    data   = request.json
    title  = data.get("title", "").strip()
    series = data.get("series", "").strip()

    if not SCAN_PATH_BASE or not title:
        return jsonify({"exists": False})

    def extract_vol_num(t):
        matches = list(re.finditer(
            r"(?:Vol(?:ume)?[.]?|Book|Part|Year)[ ]*([0-9]+(?:[.][0-9]+)?)",
            t, re.IGNORECASE
        ))
        if matches:
            return matches[-1].group(1)
        authorless = t.rsplit(" - ", 1)[0] if " - " in t else t
        m = re.search(r"(?<![0-9])([0-9]+(?:[.][0-9]+)?)(?:[ ]*:|[ ]*$)", authorless.strip())
        return m.group(1) if m else None

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
    scan_path   = os.path.join(SCAN_PATH_BASE, safe_series) if safe_series else SCAN_PATH_BASE

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
