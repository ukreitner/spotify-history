import sqlite3
from pathlib import Path
from typing import Iterator, List, Dict, Set, Optional, Literal
from contextlib import contextmanager
from .config import DATA_DIR

# Content type enum
ContentType = Literal["all", "music", "podcast"]

# Known podcast artists/shows (case-insensitive matching)
PODCAST_ARTISTS = {
    "dear hank & john",
    "cortex",
    "lateral with tom scott",
    "hello internet",
    "99% invisible",
    "freakonomics radio",
    "the good place: the podcast",
    "wtf with marc maron podcast",
    "a podcast of unnecessary detail",
    "the lonely island and seth meyers podcast",
    "accidental tech podcast",
    "atp",
    "connected",
    "upgrade",
    "reconcilable differences",
    "the talk show",
    "under the radar",
    "analog(ue)",
    "robot or not?",
    "the incomparable",
    "back to work",
}

# Patterns that indicate podcast content
PODCAST_PATTERNS = [
    "podcast",
    "episode",
    ": ep ",
    " ep.",
]


def is_podcast(artist: str, track: str = "") -> bool:
    """Determine if a track is likely a podcast based on artist/track name."""
    artist_lower = artist.lower()
    track_lower = track.lower()

    # Check against known podcast artists
    if artist_lower in PODCAST_ARTISTS:
        return True

    # Check for podcast patterns in artist name
    for pattern in PODCAST_PATTERNS:
        if pattern in artist_lower:
            return True

    # Check track name for numbered episodes (common podcast pattern)
    if track and track[0].isdigit() and ":" in track[:10]:
        return True

    return False


def get_content_filter_sql(content_type: ContentType, artist_col: str = "artist", track_col: str = "track") -> str:
    """Generate SQL WHERE clause fragment for content type filtering."""
    if content_type == "all":
        return "1=1"

    # Build conditions for podcast detection
    podcast_conditions = []
    for artist in PODCAST_ARTISTS:
        podcast_conditions.append(f"LOWER({artist_col}) = '{artist}'")
    for pattern in PODCAST_PATTERNS:
        podcast_conditions.append(f"LOWER({artist_col}) LIKE '%{pattern}%'")

    podcast_sql = " OR ".join(podcast_conditions)

    if content_type == "podcast":
        return f"({podcast_sql})"
    else:  # music
        return f"NOT ({podcast_sql})"


def get_all_db_paths() -> List[Path]:
    """Get all monthly database files sorted by date."""
    return sorted(DATA_DIR.glob("history_*.db"))


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager for read-only database connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def query_all_dbs(sql: str, params: tuple = ()) -> List[Dict]:
    """Run a query across all monthly databases and combine results."""
    results = []
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
                results.extend(dict(row) for row in rows)
            except sqlite3.OperationalError:
                continue
    return results


def get_total_plays(content_type: ContentType = "all") -> int:
    """Get total play count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    total = 0
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM plays WHERE {filter_sql}").fetchone()[0]
                total += count
            except sqlite3.OperationalError:
                continue
    return total


def get_unique_artists(content_type: ContentType = "all") -> int:
    """Get unique artist count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    artists = set()
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(f"SELECT DISTINCT artist FROM plays WHERE {filter_sql}").fetchall()
                artists.update(row[0] for row in rows)
            except sqlite3.OperationalError:
                continue
    return len(artists)


def get_unique_tracks(content_type: ContentType = "all") -> int:
    """Get unique track count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    tracks = set()
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(f"SELECT DISTINCT track_id FROM plays WHERE track_id IS NOT NULL AND {filter_sql}").fetchall()
                tracks.update(row[0] for row in rows if row[0])
            except sqlite3.OperationalError:
                continue
    return len(tracks)


def get_top_artists(limit: int = 20, content_type: ContentType = "all") -> List[Dict]:
    """Get top artists by play count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    artist_counts: Dict[str, int] = {}
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT artist, COUNT(*) as count FROM plays WHERE {filter_sql} GROUP BY artist"
                ).fetchall()
                for row in rows:
                    artist_counts[row["artist"]] = artist_counts.get(row["artist"], 0) + row["count"]
            except sqlite3.OperationalError:
                continue

    sorted_artists = sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"artist": a, "play_count": c} for a, c in sorted_artists[:limit]]


def get_top_genres(limit: int = 20, content_type: ContentType = "all") -> List[Dict]:
    """Get top genres by play count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    genre_counts: Dict[str, int] = {}
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(f"SELECT genre FROM plays WHERE genre != '' AND {filter_sql}").fetchall()
                for row in rows:
                    for genre in row["genre"].split(", "):
                        genre = genre.strip()
                        if genre:
                            genre_counts[genre] = genre_counts.get(genre, 0) + 1
            except sqlite3.OperationalError:
                continue

    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"genre": g, "play_count": c} for g, c in sorted_genres[:limit]]


def get_track_history(track_id: str) -> List[Dict]:
    """Get all plays of a specific track."""
    return query_all_dbs(
        "SELECT * FROM plays WHERE track_id = ? ORDER BY played_at DESC",
        (track_id,)
    )


def get_all_tracks_with_counts(content_type: ContentType = "all") -> Dict[str, Dict]:
    """Get all tracks with their play counts and metadata."""
    filter_sql = get_content_filter_sql(content_type)
    tracks: Dict[str, Dict] = {}
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT track_id, track, artist, COUNT(*) as count, "
                    f"MAX(played_at) as last_played, MIN(played_at) as first_played "
                    f"FROM plays WHERE track_id IS NOT NULL AND {filter_sql} GROUP BY track_id"
                ).fetchall()
                for row in rows:
                    tid = row["track_id"]
                    if tid in tracks:
                        tracks[tid]["play_count"] += row["count"]
                        if row["last_played"] > tracks[tid]["last_played"]:
                            tracks[tid]["last_played"] = row["last_played"]
                        if row["first_played"] < tracks[tid]["first_played"]:
                            tracks[tid]["first_played"] = row["first_played"]
                    else:
                        tracks[tid] = {
                            "track_id": tid,
                            "track": row["track"],
                            "artist": row["artist"],
                            "play_count": row["count"],
                            "last_played": row["last_played"],
                            "first_played": row["first_played"],
                        }
            except sqlite3.OperationalError:
                continue
    return tracks


def get_all_artist_ids(content_type: ContentType = "all") -> Set[str]:
    """Get all unique artist names (for filtering recommendations)."""
    filter_sql = get_content_filter_sql(content_type)
    artist_names: Set[str] = set()
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(f"SELECT DISTINCT artist FROM plays WHERE {filter_sql}").fetchall()
                artist_names.update(row[0] for row in rows)
            except sqlite3.OperationalError:
                continue
    return artist_names


# Podcast-specific queries
def get_top_podcasts(limit: int = 20) -> List[Dict]:
    """Get top podcasts by episode count."""
    return get_top_artists(limit=limit, content_type="podcast")


def get_podcast_episodes(show: str, limit: int = 50) -> List[Dict]:
    """Get episodes for a specific podcast show."""
    episodes = []
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    "SELECT track as episode, COUNT(*) as play_count, MAX(played_at) as last_played "
                    "FROM plays WHERE LOWER(artist) = LOWER(?) GROUP BY track ORDER BY last_played DESC",
                    (show,)
                ).fetchall()
                for row in rows:
                    episodes.append(dict(row))
            except sqlite3.OperationalError:
                continue

    # Dedupe and sort
    seen = set()
    unique = []
    for ep in sorted(episodes, key=lambda x: x["last_played"], reverse=True):
        if ep["episode"] not in seen:
            seen.add(ep["episode"])
            unique.append(ep)
    return unique[:limit]


def get_listening_stats_by_type() -> Dict[str, Dict]:
    """Get listening stats broken down by content type."""
    return {
        "music": {
            "total_plays": get_total_plays("music"),
            "unique_artists": get_unique_artists("music"),
            "unique_tracks": get_unique_tracks("music"),
        },
        "podcast": {
            "total_plays": get_total_plays("podcast"),
            "unique_shows": get_unique_artists("podcast"),
            "unique_episodes": get_unique_tracks("podcast"),
        },
    }


def get_all_plays_with_timestamps(content_type: ContentType = "all") -> List[str]:
    """Get all play timestamps for pattern analysis."""
    filter_sql = get_content_filter_sql(content_type)
    timestamps = []
    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT played_at FROM plays WHERE {filter_sql}"
                ).fetchall()
                timestamps.extend(row[0] for row in rows if row[0])
            except sqlite3.OperationalError:
                continue
    return timestamps


def get_top_tracks(limit: int = 20, content_type: ContentType = "all") -> List[Dict]:
    """Get top tracks by play count across all databases."""
    filter_sql = get_content_filter_sql(content_type)
    track_data: Dict[str, Dict] = {}

    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT track_id, track, artist, COUNT(*) as count "
                    f"FROM plays WHERE track_id IS NOT NULL AND {filter_sql} "
                    f"GROUP BY track_id"
                ).fetchall()
                for row in rows:
                    tid = row["track_id"]
                    if tid in track_data:
                        track_data[tid]["play_count"] += row["count"]
                    else:
                        track_data[tid] = {
                            "track_id": tid,
                            "track": row["track"],
                            "artist": row["artist"],
                            "play_count": row["count"],
                        }
            except sqlite3.OperationalError:
                continue

    sorted_tracks = sorted(track_data.values(), key=lambda x: x["play_count"], reverse=True)
    return sorted_tracks[:limit]


def get_recent_listening(days: int = 30, content_type: ContentType = "music") -> Dict:
    """
    Analyze recent listening patterns.
    Returns artists, tracks, and genres from the last N days with play counts.
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    filter_sql = get_content_filter_sql(content_type)

    artists: Dict[str, int] = {}
    tracks: Dict[str, Dict] = {}
    genres: Dict[str, int] = {}

    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT track_id, track, artist, genre, played_at FROM plays "
                    f"WHERE played_at > ? AND {filter_sql}",
                    (cutoff,)
                ).fetchall()

                for row in rows:
                    # Count artists
                    artist = row["artist"].split(",")[0].strip()
                    artists[artist] = artists.get(artist, 0) + 1

                    # Count tracks
                    tid = row["track_id"]
                    if tid:
                        if tid not in tracks:
                            tracks[tid] = {
                                "track_id": tid,
                                "track": row["track"],
                                "artist": row["artist"],
                                "play_count": 0,
                            }
                        tracks[tid]["play_count"] += 1

                    # Count genres
                    if row["genre"]:
                        for g in row["genre"].split(", "):
                            g = g.strip()
                            if g:
                                genres[g] = genres.get(g, 0) + 1

            except sqlite3.OperationalError:
                continue

    # Sort by play count
    sorted_artists = sorted(artists.items(), key=lambda x: x[1], reverse=True)
    sorted_tracks = sorted(tracks.values(), key=lambda x: x["play_count"], reverse=True)
    sorted_genres = sorted(genres.items(), key=lambda x: x[1], reverse=True)

    return {
        "artists": [{"artist": a, "play_count": c} for a, c in sorted_artists],
        "tracks": sorted_tracks,
        "genres": [{"genre": g, "play_count": c} for g, c in sorted_genres],
        "total_plays": sum(artists.values()),
    }


def search_user_tracks(query: str, limit: int = 20) -> List[Dict]:
    """
    Search user's listening history for tracks matching query.

    Searches track name and artist name.
    Returns tracks sorted by play count.
    """
    query_lower = query.lower()
    results: Dict[str, Dict] = {}

    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    "SELECT track_id, track, artist, COUNT(*) as count "
                    "FROM plays WHERE track_id IS NOT NULL "
                    "AND (LOWER(track) LIKE ? OR LOWER(artist) LIKE ?) "
                    "GROUP BY track_id",
                    (f"%{query_lower}%", f"%{query_lower}%")
                ).fetchall()

                for row in rows:
                    tid = row["track_id"]
                    if tid in results:
                        results[tid]["play_count"] += row["count"]
                    else:
                        results[tid] = {
                            "track_id": tid,
                            "track": row["track"],
                            "artist": row["artist"],
                            "play_count": row["count"],
                        }
            except sqlite3.OperationalError:
                continue

    sorted_results = sorted(results.values(), key=lambda x: x["play_count"], reverse=True)
    return sorted_results[:limit]


def get_recent_tracks(days: int = 7, limit: int = 20, content_type: ContentType = "music") -> List[Dict]:
    """
    Get most recently played tracks.

    Returns tracks from the last N days, sorted by most recent first.
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    filter_sql = get_content_filter_sql(content_type)
    tracks: Dict[str, Dict] = {}

    for db_path in get_all_db_paths():
        with connect(db_path) as conn:
            try:
                rows = conn.execute(
                    f"SELECT track_id, track, artist, played_at "
                    f"FROM plays WHERE track_id IS NOT NULL "
                    f"AND played_at > ? AND {filter_sql} "
                    f"ORDER BY played_at DESC",
                    (cutoff,)
                ).fetchall()

                for row in rows:
                    tid = row["track_id"]
                    if tid not in tracks:
                        tracks[tid] = {
                            "track_id": tid,
                            "track": row["track"],
                            "artist": row["artist"],
                            "last_played": row["played_at"],
                            "play_count": 0,
                        }
                    tracks[tid]["play_count"] += 1
            except sqlite3.OperationalError:
                continue

    sorted_tracks = sorted(tracks.values(), key=lambda x: x["last_played"], reverse=True)
    return sorted_tracks[:limit]
