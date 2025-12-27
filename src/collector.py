#!/usr/bin/env python3
import os
import sqlite3
import datetime
import logging
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from requests.exceptions import RequestException

# determine monthly DB path
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def retry_with_backoff(func, *args, max_retries=4, **kwargs):
    """
    Retry a function with exponential backoff.
    Retries up to max_retries times with delays of 2s, 4s, 8s, 16s.
    """
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except (SpotifyException, RequestException, Exception) as e:
            # Check if it's a retryable error
            is_retryable = False
            if isinstance(e, RequestException):
                is_retryable = True
            elif isinstance(e, SpotifyException):
                # Retry on 503, 429, and other server errors
                if hasattr(e, 'http_status'):
                    is_retryable = e.http_status in [429, 500, 502, 503, 504]
                else:
                    is_retryable = True
            elif "503" in str(e) or "429" in str(e) or "connect" in str(e).lower():
                is_retryable = True

            if not is_retryable or attempt == max_retries:
                logger.error("Request failed after %d attempts: %s", attempt + 1, e)
                raise

            delay = 2 ** (attempt + 1)  # 2s, 4s, 8s, 16s
            logger.warning("Request failed (attempt %d/%d), retrying in %ds: %s",
                          attempt + 1, max_retries + 1, delay, e)
            time.sleep(delay)

# Use timezone-aware UTC timestamp
now = datetime.datetime.now(datetime.UTC)
db_name = f"history_{now.strftime('%Y%m')}.db"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, db_name)

logger.info("Storing plays in %s", DB_PATH)


conn = sqlite3.connect(DB_PATH)
logger.info("Connected to database")
conn.execute("""CREATE TABLE IF NOT EXISTS plays (
    played_at TEXT PRIMARY KEY,
    track_id  TEXT,
    track     TEXT,
    artist    TEXT,
    ms_played INTEGER,
    genre     TEXT
)""")

# add genre column to older DBs
cols = [c[1] for c in conn.execute("PRAGMA table_info(plays)").fetchall()]
if "genre" not in cols:
    conn.execute("ALTER TABLE plays ADD COLUMN genre TEXT")
    logger.info("Added missing 'genre' column")

oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-read-recently-played",
    cache_path="/tmp/.spotify-cache",
)
token = retry_with_backoff(oauth.refresh_access_token, os.getenv("SPOTIFY_REFRESH_TOKEN"))
logger.info("Refreshed access token")

sp = spotipy.Spotify(auth=token["access_token"])

items = retry_with_backoff(sp.current_user_recently_played, limit=50)["items"]
logger.info("Fetched %d recent plays", len(items))

# fetch genres for all artists in bulk
artist_ids = {
    a["id"]
    for it in items
    for a in it["track"]["artists"]
    if a.get("id")
}
artist_genres = {}
ids = list(artist_ids)
for i in range(0, len(ids), 50):
    batch = ids[i:i+50]
    for artist in retry_with_backoff(sp.artists, batch)["artists"]:
        artist_genres[artist["id"]] = artist.get("genres", [])

inserted = 0
for item in items:
    genres = set()
    for a in item["track"]["artists"]:
        genres.update(artist_genres.get(a.get("id"), []))
    genre = ", ".join(sorted(genres))
    row = (
        item["played_at"],
        item["track"]["id"],
        item["track"]["name"],
        ", ".join(a["name"] for a in item["track"]["artists"]),
        item.get("ms_played", item["track"].get("duration_ms")),
        genre,
    )
    cur = conn.execute(
        "INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?,?)",
        row,
    )
    inserted += cur.rowcount

conn.commit()
logger.info("Inserted %d new plays", inserted)
