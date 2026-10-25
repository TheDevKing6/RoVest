"""
Roblox Hidden Gems
------------------
Finds Roblox games that have real players but few total visits, tracks their player count over
time, and shows them in a clean website with charts.

- A background scan searches Roblox and keeps games with at least MIN_CCU players right now and
  at most MAX_VISITS total visits.
- Every game that qualifies is tracked: each scan and each quick "tracker" refresh adds a point
  to its player-count history, which powers the charts.
- Loading the page never starts a scan. It only shows stored results.

Run locally:   pip install -r requirements.txt
               python roblox_gem_finder.py          then open http://localhost:5000
Keep index.html and queries.py in the same folder as this file.

Hosting it publicly: see DEPLOY.md. Settings can be changed with environment variables
(MIN_CCU, MAX_VISITS, SCAN_MODE, ADMIN_TOKEN, AUTO_SCAN_INTERVAL_MIN, TRACK_INTERVAL_MIN, DATA_DIR, ...).
"""

import hmac
import json
import os
import random
import re
import socket
import sys
import threading
import time
import uuid
from collections import deque
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # so queries.py is found from any folder
try:
    from queries import MODES, build_plan, name_terms   # keyword banks and the search plan (queries.py)
except ImportError:
    raise SystemExit(
        "\n❌ queries.py is missing.\n"
        "   It must sit in the same folder as roblox_gem_finder.py. Upload queries.py to your repo.\n"
        "   If you deploy with a Dockerfile, use the one that says  COPY *.py index.html ./  so it gets copied.\n")

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

# ----------------------------- FILTER CRITERIA -----------------------------
MIN_CCU = int(os.environ.get("MIN_CCU", 350))              # minimum players right now
MAX_VISITS = int(os.environ.get("MAX_VISITS", 500_000))    # maximum total visits

# ------------------------------- SETTINGS ----------------------------------
PORT = int(os.environ.get("PORT", 5000))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
CACHE_FILE = os.path.join(DATA_DIR, "results_cache.json")   # latest list shown on the page
STORE_FILE = os.path.join(DATA_DIR, "history.json")         # player-count history for the charts
CACHE_MAX_AGE_MIN = 30       # on startup, rescan automatically if saved results are older than this

# How long / how much history to keep
HISTORY_KEEP_DAYS = int(os.environ.get("HISTORY_KEEP_DAYS", 14))   # drop games that haven't qualified for this long
HISTORY_MAX_POINTS = int(os.environ.get("HISTORY_MAX_POINTS", 800))  # data points kept per game
HISTORY_MAX_GAMES = int(os.environ.get("HISTORY_MAX_GAMES", 3000))   # games tracked at once

# ----------------------------- HOSTING SETTINGS ----------------------------
# ADMIN_TOKEN:            password for the Rescan / Stop buttons (the "Admin" link in the footer).
#                         With no token set, only a direct localhost visit counts as admin.
# AUTO_SCAN_INTERVAL_MIN: run a full scan every N minutes (0 = only on startup / button).
# TRACK_INTERVAL_MIN:     refresh the player counts of tracked games every N minutes (0 = off).
# ONE_SHOT=1:             run ONE full scan, write a static website into EXPORT_DIR, then exit.
# TRACK_ONLY=1:           refresh tracked games only, write the static website, then exit.
#                         (Both are used by the GitHub Actions workflows.)
# PREV_DIR:               folder holding the previously published site, so history carries over.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
AUTO_SCAN_INTERVAL_MIN = int(os.environ.get("AUTO_SCAN_INTERVAL_MIN", 0))
TRACK_INTERVAL_MIN = int(os.environ.get("TRACK_INTERVAL_MIN", 30))
_truthy = lambda v: str(v).strip().lower() in ("1", "true", "yes")
ONE_SHOT = _truthy(os.environ.get("ONE_SHOT", ""))
TRACK_ONLY = _truthy(os.environ.get("TRACK_ONLY", ""))
EXPORT_DIR = os.environ.get("EXPORT_DIR", "site_out")
PREV_DIR = os.environ.get("PREV_DIR", "")
ONE_SHOT_MAX_MIN = int(os.environ.get("ONE_SHOT_MAX_MIN", 100))  # stop scanning after this long and publish what was found
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass
# Optional proxy. Set SMARTPROXY_URL in your host's environment variables, for example:
#   http://USERNAME:PASSWORD@HOST:PORT
# Never write the real URL into this file or commit it to GitHub.
# (On GitHub Actions, save it as a repository secret named SMARTPROXY_URL, see DEPLOY.md.)
PROXY_URL = os.environ.get("SMARTPROXY_URL", "").strip().strip("\"'").strip()   # tolerate quotes pasted around it
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
# Proxy plans are usually billed per GB. A scan stops early once this many MB have been
# downloaded through the proxy (0 = no limit). Each scan prints how much it used.
MAX_PROXY_MB = int(os.environ.get("MAX_PROXY_MB", 0))

# Speed settings. They are slower by default without a proxy, because Roblox limits requests per IP.
# If Roblox answers "rate limited", the script also slows itself down automatically and speeds
# back up when things are calm.
SEARCH_WORKERS = int(os.environ.get("SEARCH_WORKERS", 4 if PROXY_URL else 3))      # queries in parallel
REQUEST_GAP = float(os.environ.get("REQUEST_GAP", 0.25 if PROXY_URL else 0.5))     # starting seconds between requests to each Roblox service
# Search results already include a live player count, so games well below the
# minimum are skipped without fetching their stats (this is the big speed-up).
# Set to 0 to fetch stats for every game found.
PREFILTER_CCU = MIN_CCU // 2

# How many searches one scan runs (the keyword banks live in queries.py):
#   "quick"    500     hand-picked trend keywords only (a few minutes)
#   "standard" 3,000   + a window of the generated pool
#   "deep"     10,000  + a bigger window                      (about an hour)
#   "max"      ~64,000 everything, including a 3-letter crawl (many hours, lots of data)
# The generated pool has ~46,000 searches. quick/standard/deep run a WINDOW of it and move on
# each scan, so repeated scans walk through all of it instead of repeating themselves.
SCAN_MODE = os.environ.get("SCAN_MODE", "deep")
# While scanning, words that appear in 2+ live games' names are searched too (snowballing).
SNOWBALL_MAX = 3000

HEADERS = {"User-Agent": "Mozilla/5.0 (RobloxGemFinder)"}

# ------------------------------ SHARED STATE -------------------------------
state_lock = threading.Lock()
state = {
    "status": "idle",        # idle | scanning | done | error
    "stage": "",             # human-readable current step
    "progress": 0,           # 0-100
    "results": [],           # games shown on the page (already enriched for display)
    "analyzed": 0,           # games checked so far in the current/last scan
    "searched": 0,           # raw search hits (0 after a full scan means Roblox blocked us)
    "last_updated": None,    # unix time of the last data refresh
    "last_scan": None,       # unix time of the last full scan
    "error": None,
    "queries_done": 0,
    "queries_total": 0,
    "eta_seconds": None,
    "mode": None,            # scan size currently running (quick/standard/deep/max)
}
cancel_event = threading.Event()


def set_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def snapshot():
    with state_lock:
        return dict(state)


# ------------------------------- NETWORKING --------------------------------
_hosts = {}               # roblox host -> {"gap", "next", "ok", "hits"}: each service gets its own pace
_hosts_lock = threading.Lock()
_usage = {"requests": 0, "bytes": 0, "limited": 0}
_usage_lock = threading.Lock()


def safe_err(e):
    """Error text with the proxy address and login removed, safe to print or log."""
    text = str(e)
    if PROXY_URL:
        text = text.replace(PROXY_URL, "[proxy]")
        m = re.match(r"^[a-z0-9+.\-]+://([^:@/]+):([^@]+)@", PROXY_URL, re.I)
        if m:
            for part in (m.group(2), m.group(1)):
                if len(part) >= 4:
                    text = text.replace(part, "***")
    return text


def usage_report():
    with _usage_lock:
        n, mb = _usage["requests"], _usage["bytes"] / 1e6
    return n, mb


def rate_limited_count():
    with _usage_lock:
        return _usage["limited"]


def _check_budget():
    """Stop the scan if a proxy data limit (MAX_PROXY_MB) has been reached."""
    if PROXIES and MAX_PROXY_MB and not cancel_event.is_set():
        _, mb = usage_report()
        if mb >= MAX_PROXY_MB:
            print(f"⛔ Reached the proxy data limit ({MAX_PROXY_MB} MB). Stopping the scan early.")
            cancel_event.set()


def _host_state(host):
    st = _hosts.get(host)
    if st is None:
        st = _hosts[host] = {"gap": REQUEST_GAP, "next": 0.0, "ok": 0, "hits": []}
    return st


def throttle(host, extra_pause=0.0):
    """Rate limiter shared by all threads. Each Roblox service (search, games, thumbnails) is
    paced separately, because they have different limits."""
    with _hosts_lock:
        st = _host_state(host)
        now = time.time()
        if extra_pause:
            st["next"] = max(st["next"], now + extra_pause)
        wait = st["next"] - now
        st["next"] = max(st["next"], now) + st["gap"]
    if wait > 0:
        time.sleep(wait)


def get_json(url, params=None, retries=5):
    """GET with per-service rate limiting, Retry-After handling, adaptive slow-down, a cool-down
    when a service keeps refusing, and retry/backoff. Uses the proxy from SMARTPROXY_URL if set."""
    host = urlparse(url).netloc
    for attempt in range(retries):
        throttle(host)
        try:
            r = requests.get(url, params=params, headers=HEADERS, proxies=PROXIES, timeout=20)
            with _usage_lock:
                _usage["requests"] += 1
                _usage["bytes"] += int(r.headers.get("Content-Length") or len(r.content))
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", 0))
                except ValueError:
                    wait = 0
                wait = max(wait, 2 * (attempt + 1))
                with _hosts_lock:
                    st = _host_state(host)
                    st["gap"] = min(3.0, st["gap"] * 1.5)      # slow down this service
                    st["ok"] = 0
                    now = time.time()
                    st["hits"] = [t for t in st["hits"] if now - t < 30] + [now]
                    if len(st["hits"]) >= 8:                  # refused again and again: cool down properly
                        wait = max(wait, 45)
                        st["hits"] = []
                    gap_now = st["gap"]
                with _usage_lock:
                    _usage["limited"] += 1
                print(f"    ⏳ {host} rate limited, pausing {wait:.0f}s (requests to it now spaced {gap_now:.2f}s apart)")
                throttle(host, extra_pause=wait)
                continue
            r.raise_for_status()
            data = r.json()
            with _hosts_lock:
                st = _host_state(host)
                st["ok"] += 1
                if st["ok"] >= 60 and st["gap"] > REQUEST_GAP:   # calm for a while: speed back up
                    st["gap"] = max(REQUEST_GAP, st["gap"] / 1.25)
                    st["ok"] = 0
            _check_budget()
            return data
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            # 4xx (except 429) won't fix itself on retry, so fail immediately
            if 400 <= code < 500 or attempt == retries - 1:
                raise
            time.sleep(1 + attempt)
        except requests.exceptions.ProxyError as e:
            if "407" in str(e):   # wrong proxy username/password: retrying can't help
                raise RuntimeError("The proxy rejected the login (407). Check SMARTPROXY_URL.") from None
            if attempt == retries - 1:
                raise
            time.sleep(1 + attempt)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(1 + attempt)
    return None


def _exit_addresses(n=4):
    """Which address does Roblox see? Several calls show whether the proxy rotates."""
    ips = []
    for _ in range(n):
        try:
            ips.append(requests.get("https://api.ipify.org", proxies=PROXIES, timeout=15).text.strip())
        except Exception:
            ips.append(None)
    return ips


def check_proxy():
    """True if no proxy is configured or the proxy can reach Roblox. Prints what it found."""
    if not PROXIES:
        return True
    try:
        r = requests.get("https://apis.roblox.com/search-api/omni-search",
                         params={"searchQuery": "obby", "sessionId": str(uuid.uuid4()), "pageType": "all"},
                         headers=HEADERS, proxies=PROXIES, timeout=25)
    except Exception as e:
        print(f"❌ Proxy check failed: {safe_err(e)}\n"
              "   Check SMARTPROXY_URL (format http://USER:PASSWORD@HOST:PORT) and that your plan has data left.\n"
              "   If the password has symbols like @ : / # they must be URL-encoded (@ becomes %40).")
        return False
    ips = [i for i in _exit_addresses(3) if i]
    shown = ", ".join(ips) if ips else "unknown address"
    if len(set(ips)) == 1 and len(ips) > 1:
        print(f"⚠️  The proxy gave the SAME address every time ({ips[0]}). Roblox sees one visitor, so its "
              "limits still apply. Use your provider's rotating endpoint/port, not a sticky or static one.")
    elif ips:
        print(f"🔄 The proxy rotates ({len(set(ips))} different addresses in {len(ips)} tries: {shown}).")
    if r.status_code == 429:
        print("⚠️  Proxy works but Roblox is already rate limiting it. The script will slow down.")
    elif r.status_code >= 400:
        print(f"⚠️  Proxy works but Roblox answered HTTP {r.status_code}.")
    else:
        print("🔌 Proxy OK. Roblox answered normally through it.")
    return True


_diag = {"running": False, "lines": []}
_diag_lock = threading.Lock()


def _probe(name, url, make_params, gaps=(1.0, 0.4, 0.15), per_gap=8):
    """Send a few requests at slower-to-faster speeds and report where Roblox starts refusing."""
    out, safe = [], None
    for gap in gaps:
        codes, lat, retry = [], [], None
        for _ in range(per_gap):
            t0 = time.time()
            try:
                r = requests.get(url, params=make_params(), headers=HEADERS, proxies=PROXIES, timeout=20)
                codes.append(r.status_code)
                if r.status_code == 429 and retry is None:
                    retry = r.headers.get("Retry-After")
            except Exception as e:
                codes.append(0)
            lat.append(time.time() - t0)
            time.sleep(max(0, gap - (time.time() - t0)))
        n429 = codes.count(429)
        out.append(f"   {name}: one request every {gap}s -> {per_gap - n429}/{per_gap} fine"
                   + (f", {n429} rate limited" + (f" (Roblox said wait {retry}s)" if retry else "") if n429 else "")
                   + (f", {codes.count(0)} failed" if codes.count(0) else "")
                   + f", avg {sum(lat) / len(lat):.1f}s each")
        if n429 == 0 and codes.count(0) < per_gap:
            safe = gap
        else:
            time.sleep(5)       # let the limit reset before the next test
            if n429:
                break
    return out, safe


def run_diagnose():
    """Connection test for the owner: is the proxy used, does it rotate, how fast can we go?"""
    lines = []
    def say(t):
        lines.append(t)
        with _diag_lock:
            _diag["lines"] = list(lines)
    try:
        say(f"Proxy: {'ON' if PROXIES else 'OFF (direct connection from this server)'}")
        say(f"Current settings: {SEARCH_WORKERS} parallel searches, starting spacing {REQUEST_GAP}s")
        ips = _exit_addresses(4)
        good = [i for i in ips if i]
        say("Address Roblox sees: " + (", ".join(good) if good else "could not check"))
        if PROXIES and len(good) > 1:
            say("-> " + ("the proxy ROTATES (good)." if len(set(good)) > 1 else
                         "SAME address every time. This is a static/sticky proxy, so Roblox's per-address limits still apply. "
                         "Switch to your provider's rotating endpoint/port."))
        say("Testing how fast Roblox answers (about 50 small requests)...")
        results = {}
        for name, url, mk in (
            ("search", "https://apis.roblox.com/search-api/omni-search",
             lambda: {"searchQuery": random.choice(["obby", "tycoon", "pet", "egg", "cat", "tower"]),
                      "sessionId": str(uuid.uuid4()), "pageType": "all"}),
            ("games", "https://games.roblox.com/v1/games", lambda: {"universeIds": "383310974"}),
        ):
            res, safe = _probe(name, url, mk)
            for l in res:
                say(l)
            results[name] = safe
        ok = {n: g for n, g in results.items() if g}
        bad = [n for n, g in results.items() if not g]
        if ok:
            say("-> Fastest pace with no refusals: " + ", ".join(f"{n} every {g}s" for n, g in ok.items()) + ".")
        if bad:
            say(f"-> {', '.join(bad)} refused even the slowest test. Set REQUEST_GAP=1.5 and SEARCH_WORKERS=2, "
                "and make sure the proxy really rotates.")
        else:
            say(f"-> Suggested setting: REQUEST_GAP={max(max(ok.values()), 0.15)} (the script still slows itself down if Roblox pushes back).")
    except Exception as e:
        say(f"Test failed: {safe_err(e)}")
    finally:
        with _diag_lock:
            _diag["running"] = False


def start_diagnose():
    with _diag_lock:
        if _diag["running"]:
            return False
        _diag.update(running=True, lines=["Starting..."])
    threading.Thread(target=run_diagnose, daemon=True).start()
    return True


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def extract_games(data):
    """Pull universeId, name and live player count out of an omni-search response."""
    games = []
    for group in (data or {}).get("searchResults", []):
        for item in group.get("contents", []):
            if item.get("contentType") == "Game" and item.get("universeId"):
                games.append({"universeId": item["universeId"],
                              "name": item.get("name", ""),
                              "playerCount": item.get("playerCount")})  # None = unknown
    return games


def search_keyword(keyword, pages=2):
    """Return games found for a query (first page, plus the second if pages > 1)."""
    url = "https://apis.roblox.com/search-api/omni-search"
    params = {"searchQuery": keyword, "sessionId": str(uuid.uuid4()), "pageType": "all"}
    found = []
    try:
        data = get_json(url, params)
        found += extract_games(data)
        token = (data or {}).get("nextPageToken")
        if token and pages > 1:
            try:
                params["pageToken"] = token
                params["nextPageToken"] = token
                found += extract_games(get_json(url, params))
            except Exception:
                pass  # page 2 is a bonus
    except Exception as e:
        print(f"    ⚠️  Search failed for '{keyword}': {safe_err(e)}")
    return found


def fetch_batch(batch):
    """Fetch one batch. If Roblox answers 400 (usually one invalid/removed
    universe ID poisons the whole request), split the batch in half and retry
    so only the bad ID is lost instead of all 100 games."""
    try:
        data = get_json("https://games.roblox.com/v1/games",
                        {"universeIds": ",".join(map(str, batch))})
        return (data or {}).get("data", [])
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 400:
            if len(batch) == 1:
                print(f"    ⚠️  Skipping invalid universe ID {batch[0]}")
                return []
            mid = len(batch) // 2
            time.sleep(0.2)
            return fetch_batch(batch[:mid]) + fetch_batch(batch[mid:])
        print(f"    ⚠️  Details batch failed: {safe_err(e)}")
        return []
    except Exception as e:
        print(f"    ⚠️  Details batch failed: {safe_err(e)}")
        return []


def fetch_details(universe_ids):
    """Fetch visits / playing / creator for universe IDs in batches."""
    # Keep only clean positive integer IDs
    clean = []
    for u in universe_ids:
        try:
            u = int(u)
            if u > 0:
                clean.append(u)
        except (TypeError, ValueError):
            pass

    out = []
    for i in range(0, len(clean), 50):
        out.extend(fetch_batch(clean[i:i + 50]))
    return out


# ------------------------------- GAME DATA ---------------------------------
def game_url(place_id):
    return f"https://www.roblox.com/games/{place_id}"


def to_gem(d):
    """Turn one row of Roblox's games API into our simple game record (or None)."""
    try:
        uid, place = int(d.get("id") or 0), int(d.get("rootPlaceId") or 0)
        if not uid or not place:
            return None
        return {
            "id": uid,
            "placeId": place,
            "name": str(d.get("name") or "Unknown")[:120],
            "creator": str((d.get("creator") or {}).get("name") or "Unknown")[:60],
            "ccu": int(d.get("playing") or 0),
            "visits": int(d.get("visits") or 0),
            "favorites": int(d.get("favoritedCount") or 0),
        }
    except (TypeError, ValueError):
        return None


def matches(g):
    return g["ccu"] >= MIN_CCU and g["visits"] <= MAX_VISITS


def filter_games(details):
    gems = [g for g in map(to_gem, details) if g and matches(g)]
    gems.sort(key=lambda x: x["ccu"], reverse=True)
    return gems


# ------------------------------ HISTORY STORE ------------------------------
# store["games"][str(universeId)] = {
#   "n": name, "p": placeId, "c": creator, "f": favorites, "t": icon url or missing,
#   "first": unix time first seen, "seen": unix time it last qualified,
#   "pts": [[unix time, players, visits], ...]   (oldest first)
# }
store_lock = threading.Lock()
store = {"games": {}}
THUMB_RE = re.compile(r"^https://[a-z0-9.\-]+\.rbxcdn\.com/[\w\-./?=&%]+$", re.I)


def _prune_locked(now):
    games = store["games"]
    cutoff = now - HISTORY_KEEP_DAYS * 86400
    for k in [k for k, e in games.items() if e.get("seen", 0) < cutoff]:
        del games[k]
    if len(games) > HISTORY_MAX_GAMES:
        oldest = sorted(games, key=lambda k: games[k].get("seen", 0))
        for k in oldest[:len(games) - HISTORY_MAX_GAMES]:
            del games[k]


def hist_record(gems, matched=True, ts=None):
    """Add one data point per game. matched=False records games that no longer qualify
    (their line continues, but they don't count as 'seen' so they age out)."""
    if not gems:
        return
    ts = ts or int(time.time())
    with store_lock:
        games = store["games"]
        for g in gems:
            key = str(g["id"])
            e = games.get(key)
            if e is None:
                e = games[key] = {"first": ts, "seen": ts, "pts": []}
            e["n"], e["p"], e["c"], e["f"] = g["name"], g["placeId"], g["creator"], g["favorites"]
            if matched:
                e["seen"] = ts
            pts = e["pts"]
            point = [ts, g["ccu"], g["visits"]]
            if pts and ts - pts[-1][0] < 60:
                pts[-1] = point                      # same minute: replace instead of stacking
            else:
                pts.append(point)
            if len(pts) > HISTORY_MAX_POINTS:
                del pts[:len(pts) - HISTORY_MAX_POINTS]
        _prune_locked(ts)


def save_store(path=None):
    path = path or STORE_FILE
    tmp = path + ".tmp"
    try:
        with store_lock:
            blob = json.dumps(store, separators=(",", ":"))
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(blob)
        os.replace(tmp, path)
    except OSError as e:
        print(f"⚠️  Could not save history: {e}")


def load_store(path=None):
    path = path or STORE_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        games = {}
        for k, e in (data.get("games") or {}).items():
            if str(k).isdigit() and isinstance(e.get("pts"), list):
                games[str(k)] = e
        with store_lock:
            store["games"] = games
            store["cursor"] = int(data.get("cursor") or 0)   # where the next scan continues in the keyword pool
        return len(games)
    except (OSError, ValueError, AttributeError):
        return 0


def ensure_thumbs(ids):
    """Fetch game icons (Roblox CDN links) for games that don't have one yet."""
    with store_lock:
        need = [i for i in ids if not store["games"].get(str(i), {}).get("t")]
    for k in range(0, len(need), 100):
        batch = need[k:k + 100]
        try:
            data = get_json("https://thumbnails.roblox.com/v1/games/icons",
                            {"universeIds": ",".join(map(str, batch)), "returnPolicy": "PlaceHolder",
                             "size": "150x150", "format": "Png", "isCircular": "false"})
        except Exception as e:
            print(f"    ⚠️  Icon fetch failed: {safe_err(e)}")
            continue
        with store_lock:
            for it in (data or {}).get("data", []):
                url = it.get("imageUrl")
                e = store["games"].get(str(it.get("targetId")))
                if e is not None and it.get("state") == "Completed" and isinstance(url, str) and THUMB_RE.match(url):
                    e["t"] = url


def refresh_tracked():
    """Re-fetch current stats for every tracked game and add a history point for each.
    Returns (games that still qualify, number of games Roblox returned)."""
    with store_lock:
        ids = [int(k) for k in store["games"]]
    if not ids:
        return [], 0
    details = fetch_details(ids)
    ts = int(time.time())
    gems = [g for g in map(to_gem, details) if g]
    hist_record([g for g in gems if matches(g)], matched=True, ts=ts)
    hist_record([g for g in gems if not matches(g)], matched=False, ts=ts)
    return [g for g in gems if matches(g)], len(details)


def _spark(pts, n=40):
    vals = [p[1] for p in pts]
    if len(vals) <= n:
        return vals
    step = (len(vals) - 1) / (n - 1)
    return [vals[round(i * step)] for i in range(n)]


def _change_24h(pts):
    """Percent change in players vs ~24h ago (None until there are 3+ hours of history)."""
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 3 * 3600:
        return None
    target = pts[-1][0] - 86400
    ref = min(pts, key=lambda p: abs(p[0] - target))
    if ref[1] <= 0:
        return None
    return round((pts[-1][1] - ref[1]) / ref[1] * 100, 1)


def publish_results(gems):
    """Build the list the website shows: current stats plus icon, sparkline, peak, 24h change."""
    out = []
    with store_lock:
        for g in gems:
            e = store["games"].get(str(g["id"]), {})
            pts = e.get("pts", [])
            out.append({
                "id": g["id"], "name": g["name"], "creator": g["creator"],
                "ccu": g["ccu"], "visits": g["visits"], "favorites": g["favorites"],
                "url": game_url(g["placeId"]), "thumb": e.get("t"),
                "spark": _spark(pts), "peak": max([p[1] for p in pts] + [g["ccu"]]),
                "d24": _change_24h(pts), "first": e.get("first"),
            })
    out.sort(key=lambda x: x["ccu"], reverse=True)
    return out


# --------------------------------- SCAN ------------------------------------
def run_scan(mode=None):
    mode = mode if mode in MODES else SCAN_MODE
    with store_lock:
        cursor = store.get("cursor", 0)
    plan, info = build_plan(mode, cursor)
    queue = deque((q, pages, True) for q, pages in plan)    # (query, pages, part of the plan?)
    queued = {q.lower() for q, _ in plan}
    lock = threading.Lock()
    seen = set()          # every unique universe ID found or already tracked
    live = []             # games that qualify (tracked + newly found)
    live_ids = set()
    term_hits = {}        # word -> how many live games had it in their name
    c = {"done": 0, "total": len(plan), "skipped": 0, "snow": 0, "active": 0, "found": 0, "plan_done": 0}
    t0 = time.time()

    def add_query(t):
        """Queue a learned query at the front of the line (it came from real hits)."""
        key = t.lower()
        if len(key) >= 3 and key not in queued and c["snow"] < SNOWBALL_MAX:
            queued.add(key)
            queue.appendleft((t, 1, False))
            c["snow"] += 1
            c["total"] += 1

    def add_snowball(name):
        """Learn new queries from live games: a word in 2+ live names is worth searching."""
        for t in name_terms(name):
            term_hits[t] = term_hits.get(t, 0) + 1
            if term_hits[t] == 2:
                add_query(t)

    def process(query, pages):
        found = search_keyword(query, pages)
        with lock:
            c["found"] += len(found)
            new = [g for g in found if g["universeId"] not in seen]
            seen.update(g["universeId"] for g in new)
            for g in new:
                pc = g["playerCount"]
                if pc is not None and pc >= MIN_CCU and g.get("name"):
                    add_snowball(g["name"])

        # Skip games whose live player count is clearly too low (unknown count = check it)
        wanted = [g["universeId"] for g in new
                  if g["playerCount"] is None or g["playerCount"] >= PREFILTER_CCU]
        gems = filter_games(fetch_details(wanted)) if wanted else []

        with lock:
            fresh = [g for g in gems if g["id"] not in live_ids]
            live_ids.update(g["id"] for g in fresh)
            for g in fresh:   # a studio with one hit often has more: search its name too
                if g["creator"].lower() not in ("roblox", "unknown"):
                    add_query(g["creator"])
        if fresh:
            hist_record(fresh, matched=True)
            ensure_thumbs([g["id"] for g in fresh])

        with lock:
            c["skipped"] += len(new) - len(wanted)
            if fresh:
                live.extend(fresh)
                for g in fresh:  # print each match the moment it is found
                    print(f"✨ {g['ccu']:>6,} CCU {g['visits']:>8,} visits  {g['name'][:48]}  "
                          f"{game_url(g['placeId'])}")
                set_state(results=publish_results(live))
            set_state(analyzed=len(seen), searched=c["found"])

    def worker():
        while not cancel_event.is_set():
            with lock:
                if queue:
                    query, pages, in_plan = queue.popleft()
                    c["active"] += 1
                elif c["active"] == 0:
                    return          # nothing queued and nobody can add more
                else:
                    query = None
            if query is None:
                time.sleep(0.2)
                continue
            try:
                process(query, pages)
            except Exception as e:
                print(f"    ⚠️  Query '{query}' failed: {safe_err(e)}")
            finally:
                with lock:
                    c["active"] -= 1
                    c["done"] += 1
                    if in_plan:
                        c["plan_done"] += 1
                    done, total = c["done"], c["total"]
                    elapsed = max(time.time() - t0, 1)
                    eta = int((total - done) / (done / elapsed)) if done else None
                    set_state(progress=min(99, int(done / total * 100)),
                              stage=f"Query {done:,} of {total:,}  ·  {len(seen):,} games found",
                              queries_done=done, queries_total=total, eta_seconds=eta)
                    if done % 50 == 0:
                        print(f"[{done:,}/{total:,}] {len(live)} matches · {len(seen):,} games seen · "
                              f"{c['snow']} learned queries · ETA {(eta or 0) // 60} min")
                    if done % 500 == 0:
                        save_store()

    try:
        est_lo = len(plan) * REQUEST_GAP / 60
        print("\n" + "=" * 70)
        print(f"🔎 SCAN STARTED  mode={mode}  {len(plan):,} queries planned "
              f"(+ up to {SNOWBALL_MAX:,} learned from live results)")
        if info["rotates"]:
            print(f"   Keyword pool: {info['pool']:,} searches. This scan covers #{info['start']:,} to "
                  f"#{info['start'] + info['window']:,}; the next scan continues from there.")
        print(f"   Rough time: {est_lo:.0f}-{est_lo * 3:.0f}+ minutes. Matches appear in the web UI as found.")
        print(f"   Proxy: {'ON' if PROXIES else 'off'}   starting spacing: {REQUEST_GAP}s   workers: {SEARCH_WORKERS}"
              + (f"   data limit: {MAX_PROXY_MB} MB" if PROXIES and MAX_PROXY_MB else ""))
        print("=" * 70)
        set_state(results=[], analyzed=0, searched=0, queries_done=0,
                  queries_total=len(plan), eta_seconds=None, mode=mode)

        # Start from the games we already track (fresh stats), so the list is never empty
        seeded, _ = refresh_tracked()
        ensure_thumbs([g["id"] for g in seeded])
        for g in seeded:
            live.append(g)
            live_ids.add(g["id"])
            seen.add(g["id"])
        if seeded:
            print(f"Refreshed {len(seeded)} tracked games that still qualify.")
            set_state(results=publish_results(live))

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(SEARCH_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stopped = cancel_event.is_set()
        if info["rotates"]:   # move the cursor past the pool searches that actually ran
            advance = max(0, c["plan_done"] - info["base"])
            with store_lock:
                store["cursor"] = (cursor + advance) % info["pool"]
        now = int(time.time())
        set_state(status="done", stage="Stopped early" if stopped else "Scan complete",
                  progress=100, results=publish_results(live), analyzed=len(seen),
                  searched=c["found"], last_updated=now, last_scan=now, error=None, eta_seconds=None, mode=None)
        save_cache()
        save_store()

        print("\n" + "=" * 70)
        print(f"📊 Queries run: {c['done']:,}   Games found: {len(seen):,}   "
              f"⏭️  Skipped (low player count): {c['skipped']:,}")
        print(f"✨ Hidden gems found: {len(live)}" + ("   (stopped early)" if stopped else ""))
        n_req, mb = usage_report()
        print(f"📶 {n_req:,} requests to Roblox ({rate_limited_count():,} were rate limited), about {mb:.0f} MB downloaded"
              + ("  (counts against your proxy plan)" if PROXIES else ""))
        print("=" * 70)
        for i, g in enumerate(sorted(live, key=lambda x: x["ccu"], reverse=True), 1):
            print(f"{i:>3}. {g['ccu']:>7,} CCU  {g['visits']:>9,} visits  {g['name'][:45]}")
            print(f"     {game_url(g['placeId'])}")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"❌ Scan failed: {safe_err(e)}")
        set_state(status="error", stage="Scan failed", error=safe_err(e))


last_scan_attempt = 0.0


def start_scan(mode=None):
    """Start a scan in the background unless one is already running."""
    global last_scan_attempt
    with state_lock:
        if state["status"] == "scanning":
            return False
        state.update(status="scanning", stage="Starting…", progress=0, error=None)
        cancel_event.clear()
        last_scan_attempt = time.time()
    threading.Thread(target=run_scan, args=(mode,), daemon=True).start()
    return True


def track_once():
    """Quick refresh: new stats and a new chart point for every tracked game (no searching)."""
    if snapshot()["status"] == "scanning":
        return
    gems, n = refresh_tracked()
    if not n:
        return
    ensure_thumbs([g["id"] for g in gems])
    results = publish_results(gems)
    with state_lock:
        if state["status"] != "scanning":
            state.update(results=results, last_updated=int(time.time()),
                         status="done", stage="Updated")
    save_cache()
    save_store()
    print(f"⏱️  Tracker refreshed {n} games ({len(gems)} still qualify).")


# --------------------------------- CACHE -----------------------------------
CACHE_VERSION = [MIN_CCU, MAX_VISITS, "v4"]


def save_cache():
    s = snapshot()
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"results": s["results"], "analyzed": s["analyzed"],
                       "last_updated": s["last_updated"], "last_scan": s["last_scan"],
                       "criteria": CACHE_VERSION}, f)
    except OSError as e:
        print(f"⚠️  Could not write cache: {e}")


def load_cache():
    """Load saved results. Returns their age in minutes, or None if there is nothing usable."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("criteria") != CACHE_VERSION or not data.get("last_updated"):
            return None  # saved with different filters or an older format
        set_state(status="done", stage="Loaded saved results", progress=100,
                  results=data["results"], analyzed=data.get("analyzed", 0),
                  last_updated=data["last_updated"], last_scan=data.get("last_scan"))
        return (time.time() - data["last_updated"]) / 60
    except Exception:
        return None


# --------------------------------- ROUTES ----------------------------------
def read_index():
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


INDEX_HTML = read_index()


_fails = {}   # ip -> times of recent wrong owner passwords


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote_addr or "?")


def _locked_out(ip):
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    _fails[ip] = recent
    return len(recent) >= 10


def is_admin():
    """True if the request carries the owner password (or is a direct localhost visit with no
    ADMIN_TOKEN set). Ten wrong passwords from one address lock it out for 10 minutes."""
    if ADMIN_TOKEN:
        sent = request.headers.get("X-Admin-Token", "")
        if not sent:
            return False
        ip = _client_ip()
        if _locked_out(ip):
            return False
        ok = hmac.compare_digest(sent.encode(), ADMIN_TOKEN.encode())
        if not ok:
            _fails.setdefault(ip, []).append(time.time())
            if len(_fails) > 2000:
                _fails.clear()
        return ok
    # No token configured: only a direct localhost visit counts (a proxy or tunnel adds
    # X-Forwarded-For, so a public deployment without a token is automatically read-only).
    return request.remote_addr in ("127.0.0.1", "::1") and not request.headers.get("X-Forwarded-For")


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src https://*.rbxcdn.com data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")
    return resp


@app.route("/")
def home():
    # Only serves the page. Never starts a scan.
    html = INDEX_HTML or read_index()
    if html is None:
        return Response("index.html is missing. Put it in the same folder as roblox_gem_finder.py.",
                        status=500, mimetype="text/plain")
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/api/state")
def api_state():
    s = snapshot()
    s["error"] = "failed" if s["error"] else None   # never expose internal error text publicly
    s["config"] = {"min_ccu": MIN_CCU, "max_visits": MAX_VISITS, "static": False,
                   "default_mode": SCAN_MODE if SCAN_MODE in MODES else "deep"}
    resp = jsonify(s)
    resp.headers["Cache-Control"] = "public, max-age=2"
    return resp


@app.route("/api/history/<int:game_id>")
def api_history(game_id):
    with store_lock:
        e = store["games"].get(str(game_id))
        data = None if e is None else {"id": game_id, "name": e.get("n", ""), "pts": list(e["pts"])}
    if data is None:
        return jsonify({"error": "not tracked"}), 404
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


@app.route("/api/me")
def api_me():
    ok = is_admin()
    resp = jsonify({"admin": ok, "locked": (not ok) and bool(ADMIN_TOKEN) and _locked_out(_client_ip()),
                    "owner_enabled": bool(ADMIN_TOKEN) or request.remote_addr in ("127.0.0.1", "::1")})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    if not is_admin():
        return jsonify({"error": "forbidden"}), 403
    cancel_event.set()
    return jsonify({"cancelling": True})


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    if not is_admin():
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    return jsonify({"started": start_scan(mode if mode in MODES else None)})


@app.route("/api/diagnose", methods=["GET", "POST"])
def api_diagnose():
    """Owner: run a connection test (proxy rotation, safe request speed) and read its result."""
    if not is_admin():
        return jsonify({"error": "forbidden"}), 403
    started = False
    if request.method == "POST":
        started = start_diagnose() if snapshot()["status"] != "scanning" else False
    with _diag_lock:
        resp = jsonify({"started": started, "running": _diag["running"], "lines": list(_diag["lines"]),
                        "scanning": snapshot()["status"] == "scanning"})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/track", methods=["POST"])
def api_track():
    """Owner: refresh the player counts (and chart points) of tracked games right now."""
    if not is_admin():
        return jsonify({"error": "forbidden"}), 403
    if snapshot()["status"] == "scanning":
        return jsonify({"started": False})
    threading.Thread(target=track_once, daemon=True).start()
    return jsonify({"started": True})


def scheduler_loop():
    """Background timer: periodic full scans and periodic quick tracker refreshes."""
    last_track = time.time()
    while True:
        time.sleep(30)
        try:
            status = snapshot()["status"]
            if (AUTO_SCAN_INTERVAL_MIN > 0 and status != "scanning"
                    and time.time() - last_scan_attempt >= AUTO_SCAN_INTERVAL_MIN * 60):
                print("⏰ Scheduled rescan starting...")
                start_scan()
            elif (TRACK_INTERVAL_MIN > 0 and status != "scanning"
                    and time.time() - last_track >= TRACK_INTERVAL_MIN * 60):
                last_track = time.time()
                track_once()
        except Exception as e:
            print(f"⚠️  Scheduler error: {e}")


# ------------------------- STATIC SITE EXPORT (free hosting) ----------------
def export_static():
    """Write a complete static website (page + data files) that GitHub Pages can serve."""
    html = INDEX_HTML or read_index()
    if html is None:
        raise SystemExit("index.html is missing next to roblox_gem_finder.py")
    flag = "const STATIC = false;"
    if flag not in html:
        raise SystemExit("index.html no longer contains the STATIC flag, cannot export")

    out = EXPORT_DIR
    os.makedirs(os.path.join(out, "h"), exist_ok=True)
    s = snapshot()
    payload = {k: s[k] for k in ("results", "analyzed", "last_updated", "last_scan")}
    payload.update(status="done", error=None,
                   config={"min_ccu": MIN_CCU, "max_visits": MAX_VISITS, "static": True})
    with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    with store_lock:
        games = dict(store["games"])
        cursor = store.get("cursor", 0)
    with open(os.path.join(out, "store.json"), "w", encoding="utf-8") as f:
        json.dump({"games": games, "cursor": cursor}, f, separators=(",", ":"))
    for gid, e in games.items():                       # one small file per game for the charts
        with open(os.path.join(out, "h", f"{gid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": int(gid), "name": e.get("n", ""), "pts": e["pts"]}, f, separators=(",", ":"))

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html.replace(flag, "const STATIC = true;", 1))
    open(os.path.join(out, ".nojekyll"), "w").close()   # tell GitHub Pages not to post-process
    print(f"✅ Static site written to '{out}/' ({len(payload['results'])} games, {len(games)} charts)")


def load_prev():
    """Restore history (and the last-scan time) from the previously published site."""
    if not PREV_DIR:
        return
    n = load_store(os.path.join(PREV_DIR, "store.json"))
    try:
        with open(os.path.join(PREV_DIR, "state.json"), "r", encoding="utf-8") as f:
            prev = json.load(f)
        set_state(last_scan=prev.get("last_scan"), analyzed=prev.get("analyzed", 0))
    except (OSError, ValueError):
        pass
    print(f"Restored history for {n} games from '{PREV_DIR}'." if n else "No previous history found, starting fresh.")


def run_once():
    """One full scan, then publish a static site and exit (used by GitHub Actions)."""
    if not check_proxy():
        sys.exit(1)
    load_prev()
    print(f"ONE_SHOT scan: mode={SCAN_MODE}, stops after {ONE_SHOT_MAX_MIN} min, exports to '{EXPORT_DIR}/'")
    set_state(status="scanning", stage="Starting…", progress=0, error=None)
    cancel_event.clear()
    timer = threading.Timer(ONE_SHOT_MAX_MIN * 60, cancel_event.set)
    timer.daemon = True
    timer.start()
    run_scan()
    timer.cancel()
    s = snapshot()
    if s["status"] == "error" or s["searched"] == 0:
        print("❌ The scan found no games at all. Roblox may be blocking this server's IP address.\n"
              "   Leaving the published site unchanged.")
        sys.exit(1)
    export_static()


def run_track():
    """Refresh tracked games only, publish a static site and exit (used by GitHub Actions)."""
    if not check_proxy():
        sys.exit(1)
    load_prev()
    with store_lock:
        tracked = len(store["games"])
    if not tracked:
        print("Nothing is tracked yet. Run the full scan first. Nothing published.")
        return
    gems, n = refresh_tracked()
    if not n:
        print("❌ Roblox returned no data for the tracked games (blocked?). Leaving the site unchanged.")
        sys.exit(1)
    ensure_thumbs([g["id"] for g in gems])
    set_state(status="done", stage="Updated", results=publish_results(gems), last_updated=int(time.time()))
    export_static()


# --------------------------------- STARTUP ---------------------------------
_booted = False


def bootstrap():
    """Runs once on startup, both for `python roblox_gem_finder.py` and under gunicorn."""
    global _booted, last_scan_attempt
    if _booted:
        return
    _booted = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if INDEX_HTML is None:
        print("⚠️  index.html not found next to this script. The website will not load.")
    if not ADMIN_TOKEN:
        print("ℹ️  No ADMIN_TOKEN set: the owner buttons work only when opened directly on localhost.\n"
              "   Set ADMIN_TOKEN to a long random password to use them on your public site.\n"
              "   Make one with:  python -c \"import secrets; print(secrets.token_urlsafe(24))\"")
    check_proxy()

    tracked = load_store()
    age = load_cache()
    if tracked:
        print(f"Loaded history for {tracked} tracked games.")
    if age is not None:
        last_scan_attempt = time.time() - age * 60

    max_age = AUTO_SCAN_INTERVAL_MIN or CACHE_MAX_AGE_MIN
    if age is None:
        print("No saved results found. Starting first scan...")
        start_scan()
    elif age > max_age:
        print(f"Saved results are {age:.0f} min old. Rescanning in the background...")
        start_scan()
    else:
        print(f"Loaded saved results ({age:.0f} min old, {len(state['results'])} games).")

    if AUTO_SCAN_INTERVAL_MIN > 0 or TRACK_INTERVAL_MIN > 0:
        threading.Thread(target=scheduler_loop, daemon=True).start()


# Set RGF_NO_BOOT=1 to import this file (e.g. for tests) without starting a scan.
if not os.environ.get("RGF_NO_BOOT") and not (ONE_SHOT or TRACK_ONLY):
    bootstrap()


if __name__ == "__main__":
    if ONE_SHOT:
        run_once()
        sys.exit(0)
    if TRACK_ONLY:
        run_track()
        sys.exit(0)

    local_ip = get_local_ip()
    print("\n" + "=" * 70)
    print("💎 Roblox Hidden Gems")
    print("=" * 70)
    print(f"   Localhost: http://localhost:{PORT}")
    print(f"   Network:   http://{local_ip}:{PORT}")
    print("=" * 70 + "\n")

    # debug=False / use_reloader=False is important: Flask's debug reloader
    # launches the script twice, which also made the scan run twice.
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
