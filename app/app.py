import os
import pwd
import grp
import json
import re
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
load_dotenv()

ABB_HOSTNAME = os.getenv("ABB_HOSTNAME", "audiobookbay.lu")

PAGE_LIMIT = int(os.getenv("PAGE_LIMIT", 5))

DOWNLOAD_CLIENT = os.getenv("DOWNLOAD_CLIENT")
DL_URL = os.getenv("DL_URL")
if DL_URL:
    parsed_url = urlparse(DL_URL)
    DL_SCHEME = parsed_url.scheme
    DL_HOST = parsed_url.hostname
    DL_PORT = parsed_url.port
else:
    DL_SCHEME = os.getenv("DL_SCHEME", "http")
    DL_HOST = os.getenv("DL_HOST")
    DL_PORT = os.getenv("DL_PORT")

    # Make a DL_URL for Deluge if one was not specified
    if DL_HOST and DL_PORT:
        DL_URL = f"{DL_SCHEME}://{DL_HOST}:{DL_PORT}"

DL_USERNAME = os.getenv("DL_USERNAME")
DL_PASSWORD = os.getenv("DL_PASSWORD")
DL_CATEGORY = os.getenv("DL_CATEGORY", "Audiobookbay-Audiobooks")
SAVE_PATH_BASE = os.getenv("SAVE_PATH_BASE")

# Custom Nav Link Variables
NAV_LINK_NAME = os.getenv("NAV_LINK_NAME")
NAV_LINK_URL = os.getenv("NAV_LINK_URL")

# Define the port to be used
FLASK_PORT = int(os.getenv("PORT", 5078))

# Print configuration
print(f"ABB_HOSTNAME: {ABB_HOSTNAME}")
print(f"DOWNLOAD_CLIENT: {DOWNLOAD_CLIENT}")
print(f"DL_HOST: {DL_HOST}")
print(f"DL_PORT: {DL_PORT}")
print(f"DL_URL: {DL_URL}")
print(f"DL_USERNAME: {DL_USERNAME}")
print(f"DL_CATEGORY: {DL_CATEGORY}")
print(f"SAVE_PATH_BASE: {SAVE_PATH_BASE}")
print(f"NAV_LINK_NAME: {NAV_LINK_NAME}")
print(f"NAV_LINK_URL: {NAV_LINK_URL}")
print(f"PAGE_LIMIT: {PAGE_LIMIT}")
print(f"PORT: {FLASK_PORT}")

# ── Config / persistent data ───────────────────────────────────────────────
CONFIG_DIR = "/config"
FAVORITES_PATH = os.path.join(CONFIG_DIR, "favorites.json")
SERIES_MAP_PATH = os.path.join(CONFIG_DIR, "series_map.json")

# Auto-create config files on first run
os.makedirs(CONFIG_DIR, exist_ok=True)
if not os.path.exists(FAVORITES_PATH):
    with open(FAVORITES_PATH, "w") as f:
        json.dump([], f)
if not os.path.exists(SERIES_MAP_PATH):
    with open(SERIES_MAP_PATH, "w") as f:
        json.dump({}, f)

# Set nobody:users ownership on config files so they can be manually edited
try:
    nobody = pwd.getpwnam("nobody")
    users_gid = grp.getgrnam("users").gr_gid
    os.chown(FAVORITES_PATH, nobody.pw_uid, users_gid)
    os.chown(SERIES_MAP_PATH, nobody.pw_uid, users_gid)
except Exception as e:
    print(f"[WARN] Could not set config file ownership: {e}")


@app.context_processor
def inject_nav_link():
    return {
        "nav_link_name": os.getenv("NAV_LINK_NAME"),
        "nav_link_url": os.getenv("NAV_LINK_URL"),
    }


# Helper function to search AudiobookBay
def search_audiobookbay(query, max_pages=PAGE_LIMIT):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    results = []

    print(f"Searching for '{query}' on https://{ABB_HOSTNAME}...")

    for page in range(1, max_pages + 1):
        url = f"https://{ABB_HOSTNAME}/page/{page}/?s={requests.utils.quote(query.lower().replace(' ', '+'), safe='+')}"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch page {page}. Reason: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
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
                link = f"https://{ABB_HOSTNAME}{title_element['href']}"

                cover_url = (
                    post.select_one("img")["src"] if post.select_one("img") else None
                )
                cover = cover_url if cover_url else "/static/images/default_cover.jpg"

                post_info = post.select_one(".postInfo")
                post_info_text = (
                    post_info.get_text(separator=" ", strip=True) if post_info else ""
                )

                language_match = re.search(
                    r"Language:\s*(.*?)(?:\s*Keywords:|$)", post_info_text, re.DOTALL
                )
                language = language_match.group(1).strip() if language_match else "N/A"

                details_paragraph = post.select_one(
                    ".postContent p[style*='text-align:center']"
                )

                post_date, book_format, bitrate, file_size = "N/A", "N/A", "N/A", "N/A"

                if details_paragraph:
                    details_html = str(details_paragraph)

                    post_date_match = re.search(r"Posted:\s*([^<]+)", details_html)
                    post_date = (
                        post_date_match.group(1).strip() if post_date_match else "N/A"
                    )

                    format_match = re.search(
                        r"Format:\s*<span[^>]*>([^<]+)</span>", details_html
                    )
                    book_format = (
                        format_match.group(1).strip() if format_match else "N/A"
                    )

                    bitrate_match = re.search(
                        r"Bitrate:\s*<span[^>]*>([^<]+)</span>", details_html
                    )
                    bitrate = bitrate_match.group(1).strip() if bitrate_match else "N/A"

                    file_size_match = re.search(
                        r"File Size:\s*<span[^>]*>([^<]+)</span>\s*([^<]+)",
                        details_html,
                    )
                    if file_size_match:
                        file_size = f"{file_size_match.group(1).strip()} {file_size_match.group(2).strip()}"

                results.append(
                    {
                        "title": title,
                        "link": link,
                        "cover": cover,
                        "language": language,
                        "post_date": post_date,
                        "format": book_format,
                        "bitrate": bitrate,
                        "file_size": file_size,
                    }
                )
            except Exception as e:
                print(f"[ERROR] Could not process a post. Details: {e}")
                continue
    return results


# Helper function to extract magnet link from details page
def extract_magnet_link(details_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(details_url, headers=headers)
        if response.status_code != 200:
            print(
                f"[ERROR] Failed to fetch details page. Status Code: {response.status_code}"
            )
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract Info Hash
        info_hash_row = soup.find("td", string=re.compile(r"Info Hash", re.IGNORECASE))
        if not info_hash_row:
            print("[ERROR] Info Hash not found on the page.")
            return None
        info_hash = info_hash_row.find_next_sibling("td").text.strip()

        # Extract Trackers
        tracker_rows = soup.find_all(
            "td", string=re.compile(r"udp://|http://", re.IGNORECASE)
        )
        trackers = [row.text.strip() for row in tracker_rows]

        if not trackers:
            print("[WARNING] No trackers found on the page. Using default trackers.")
            trackers = [
                "udp://tracker.openbittorrent.com:80",
                "udp://opentor.org:2710",
                "udp://tracker.ccc.de:80",
                "udp://tracker.blackunicorn.xyz:6969",
                "udp://tracker.coppersurfer.tk:6969",
                "udp://tracker.leechers-paradise.org:6969",
            ]

        trackers_query = "&".join(
            f"tr={requests.utils.quote(tracker)}" for tracker in trackers
        )
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}&{trackers_query}"

        print(f"[DEBUG] Generated Magnet Link: {magnet_link}")
        return magnet_link

    except Exception as e:
        print(f"[ERROR] Failed to extract magnet link: {e}")
        return None


# Helper function to sanitize titles
def sanitize_title(title):
    return re.sub(r'[<>:"/\\|?*]', "", title).strip()


# Helper function — raw regex extraction only (no mapping lookup)
def _extract_series_raw(title):
    if " - " in title:
        authorless = title.rsplit(" - ", 1)[0].strip()
    else:
        authorless = title.strip()
    series = re.split(r"[:,]?\s*(?:Vol(?:ume)?\.?|Book|Part|Year)\s+\d+", authorless, flags=re.IGNORECASE)[0]
    series = re.sub(r"\s+\d+$", "", series)
    series = series.strip().rstrip(",").strip()
    return series if series else authorless


# Helper function to extract series name from title (with mapping)
def get_series_name(title):
    if " - " in title:
        authorless = title.rsplit(" - ", 1)[0].strip()
    else:
        authorless = title.strip()
    # Strip keyword-based volume markers (Vol. N, Book N, Year N, etc.)
    series = re.split(r"[:,]?\s*(?:Vol(?:ume)?\.?|Book|Part|Year)\s+\d+", authorless, flags=re.IGNORECASE)[0]
    # Strip bare trailing number
    series = re.sub(r"\s+\d+$", "", series)
    series = series.strip().rstrip(",").strip()
    if not series:
        series = authorless

    # Check custom series map
    mapping_path = SERIES_MAP_PATH
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path) as f:
                content = f.read().strip()
                if content:
                    mapping = json.loads(content)
                    if series in mapping:
                        return mapping[series]
        except json.JSONDecodeError:
            pass

    return series


# Endpoint for search page
@app.route("/", methods=["GET", "POST"])
def search():
    books = []
    query = ""
    try:
        if request.method == "POST":
            query = request.form["query"]
            if query:
                books = search_audiobookbay(query)
        return render_template("search.html", books=books, query=query, save_path_base=SAVE_PATH_BASE or "")
    except Exception as e:
        print(f"[ERROR] Failed to search: {e}")
        return render_template(
            "search.html", books=books, error=f"Failed to search. {str(e)}", query=query, save_path_base=SAVE_PATH_BASE or ""
        )


# Endpoint to send magnet link to torrent client
@app.route("/send", methods=["POST"])
def send():
    data = request.json
    details_url = data.get("link")
    title = data.get("title")
    if not details_url or not title:
        return jsonify({"message": "Invalid request"}), 400

    try:
        magnet_link = extract_magnet_link(details_url)
        if not magnet_link:
            return jsonify({"message": "Failed to extract magnet link"}), 500

        # FORK EDIT: build series/title two-level path
        skip_series   = data.get("skip_series", False)
        series_override = data.get("series_override", "").strip()
        safe_title = sanitize_title(title)
        if skip_series:
            save_path = f"{SAVE_PATH_BASE}/{safe_title}"
        else:
            series = sanitize_title(series_override) if series_override else sanitize_title(get_series_name(title))
            save_path = f"{SAVE_PATH_BASE}/{series}/{safe_title}" if series != safe_title else f"{SAVE_PATH_BASE}/{safe_title}"

        if DOWNLOAD_CLIENT == "qbittorrent":
            qb = Client(
                host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD
            )
            qb.auth_log_in()
            qb.torrents_add(urls=magnet_link, save_path=save_path, category=DL_CATEGORY)
        elif DOWNLOAD_CLIENT == "transmission":
            transmission = transmissionrpc(
                host=DL_HOST,
                port=DL_PORT,
                protocol=DL_SCHEME,
                username=DL_USERNAME,
                password=DL_PASSWORD,
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

        return jsonify(
            {
                "message": "Download added successfully! This may take some time, the download will show in Audiobookshelf when completed."
            }
        )
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route("/status")
def status():
    try:
        if DOWNLOAD_CLIENT == "transmission":
            transmission = transmissionrpc(
                host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD
            )
            torrents = transmission.get_torrents()
            torrent_list = [
                {
                    "name": torrent.name,
                    "progress": round(torrent.progress, 2),
                    "state": torrent.status,
                    "size": f"{torrent.total_size / (1024 * 1024):.2f} MB",
                }
                for torrent in torrents
            ]
            return render_template("status.html", torrents=torrent_list)
        elif DOWNLOAD_CLIENT == "qbittorrent":
            qb = Client(
                host=DL_HOST, port=DL_PORT, username=DL_USERNAME, password=DL_PASSWORD
            )
            qb.auth_log_in()
            torrents = qb.torrents_info(category=DL_CATEGORY)
            torrent_list = [
                {
                    "name": torrent.name,
                    "progress": round(torrent.progress * 100, 2),
                    "state": torrent.state,
                    "size": f"{torrent.total_size / (1024 * 1024):.2f} MB",
                }
                for torrent in torrents
            ]
        elif DOWNLOAD_CLIENT == "delugeweb":
            delugeweb = delugewebclient(url=DL_URL, password=DL_PASSWORD)
            delugeweb.login()
            torrents = delugeweb.get_torrents_status(
                filter_dict={"label": DL_CATEGORY},
                keys=["name", "state", "progress", "total_size"],
            )
            torrent_list = [
                {
                    "name": torrent["name"],
                    "progress": round(torrent["progress"], 2),
                    "state": torrent["state"],
                    "size": f"{torrent['total_size'] / (1024 * 1024):.2f} MB",
                }
                for k, torrent in torrents.result.items()
            ]
        else:
            return jsonify({"message": "Unsupported download client"}), 400
        return render_template("status.html", torrents=torrent_list)
    except Exception as e:
        return jsonify({"message": f"Failed to fetch torrent status: {e}"}), 500


# ── Favorites helpers ─────────────────────────────────────────────────────
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


# ── Favorites routes ──────────────────────────────────────────────────────
@app.route("/favorites")
def get_favorites():
    return jsonify({"favorites": load_favorites()})


@app.route("/favorites/add", methods=["POST"])
def add_favourite():
    data = request.json
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
def add_favourite_manual():
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
def remove_favourite():
    data = request.json
    name = data.get("name", "").strip()
    favs = load_favorites()
    favs = [f for f in favs if f != name]
    save_favorites(favs)
    return jsonify({"success": True})


@app.route("/favorites/rename", methods=["POST"])
def rename_favourite():
    data = request.json
    old_name = data.get("old_name", "").strip()
    new_name = sanitize_title(data.get("new_name", "").strip())
    if not old_name or not new_name:
        return jsonify({"success": False}), 400
    favs = load_favorites()
    if old_name in favs:
        idx = favs.index(old_name)
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
    """Return what the regex/mapping would produce for a given ABB title,
    plus whether an existing mapping is already applied."""
    data = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "No title provided"}), 400
    raw = sanitize_title(_extract_series_raw(title))
    extracted = sanitize_title(get_series_name(title))
    is_mapped = (raw in load_series_map())
    return jsonify({"success": True, "extracted": extracted, "is_mapped": is_mapped})


@app.route("/mappings/add", methods=["POST"])
def add_mapping():
    data = request.json
    extracted = data.get("extracted", "").strip()
    mapped = data.get("mapped", "").strip()
    if not extracted or not mapped:
        return jsonify({"success": False, "message": "Both fields required"}), 400
    mapping = load_series_map()
    mapping[extracted] = mapped
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/remove", methods=["POST"])
def remove_mapping():
    data = request.json
    key = data.get("key", "").strip()
    mapping = load_series_map()
    mapping.pop(key, None)
    save_series_map(mapping)
    return jsonify({"success": True})


@app.route("/mappings/rename", methods=["POST"])
def rename_mapping():
    data = request.json
    key = data.get("key", "").strip()
    new_extracted = data.get("new_extracted", "").strip()
    new_mapped = data.get("new_mapped", "").strip()
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

    if not SAVE_PATH_BASE or not title:
        return jsonify({"exists": False})

    # Extract volume number — keyword match first, bare number as fallback
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

    # Build variants to handle zero-padding (01, 1, 001 all match)
    try:
        num_int = int(float(vol_num))
        variants = list(set([
            str(num_int),
            vol_num,
            str(num_int).zfill(2),
            str(num_int).zfill(3),
        ]))
    except ValueError:
        variants = [vol_num]

    # Determine scan path
    safe_series = sanitize_title(series) if series else ""
    scan_path = os.path.join(SAVE_PATH_BASE, safe_series) if safe_series else SAVE_PATH_BASE

    if not os.path.isdir(scan_path):
        return jsonify({"exists": False})

    # Scan subfolders for any that contain the same volume number
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
