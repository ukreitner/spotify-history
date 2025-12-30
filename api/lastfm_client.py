"""
Last.fm API client for similar artists discovery.

Used as fallback since Spotify's Related Artists API is restricted.
"""

import requests
from typing import List, Dict, Optional, Tuple
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from .config import LASTFM_API_KEY

LASTFM_API_BASE = "http://ws.audioscrobbler.com/2.0/"


@lru_cache(maxsize=2000)
def get_similar_tracks(artist: str, track: str, limit: int = 100) -> List[Dict]:
    """
    Get tracks similar to the given track using Last.fm API.

    Returns list of dicts with: artist, name, match (0-1 similarity score)
    Results are cached to avoid repeated API calls.
    """
    if not artist or not track or not LASTFM_API_KEY:
        return []

    try:
        response = requests.get(
            LASTFM_API_BASE,
            params={
                "method": "track.getsimilar",
                "artist": artist,
                "track": track,
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": limit,
            },
            timeout=10,
            headers={"User-Agent": "SpotifyHistoryPlaylistMaker/1.0"},
        )
        response.raise_for_status()
        data = response.json()

        similar = data.get("similartracks", {}).get("track", [])

        results = []
        for t in similar:
            artist_info = t.get("artist", {})
            results.append({
                "artist": artist_info.get("name", "") if isinstance(artist_info, dict) else str(artist_info),
                "name": t.get("name", ""),
                "match": float(t.get("match", 0)),
                "url": t.get("url", ""),
            })

        return results

    except Exception as e:
        print(f"Last.fm track.getSimilar error for '{artist}' - '{track}': {e}")
        return []


@lru_cache(maxsize=500)
def get_similar_artists(artist_name: str, limit: int = 20) -> List[Dict]:
    """
    Get artists similar to the given artist using Last.fm API.

    Returns list of dicts with: name, match (0-1 similarity score), url
    Results are cached to avoid repeated API calls.
    """
    if not artist_name or not LASTFM_API_KEY:
        return []

    try:
        response = requests.get(
            LASTFM_API_BASE,
            params={
                "method": "artist.getsimilar",
                "artist": artist_name,
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": limit,
            },
            timeout=10,
            headers={"User-Agent": "SpotifyHistoryPlaylistMaker/1.0"},
        )
        response.raise_for_status()
        data = response.json()

        similar = data.get("similarartists", {}).get("artist", [])

        # Normalize the response
        results = []
        for artist in similar:
            results.append({
                "name": artist.get("name", ""),
                "match": float(artist.get("match", 0)),
                "url": artist.get("url", ""),
                "mbid": artist.get("mbid", ""),  # MusicBrainz ID if available
            })

        return results

    except Exception as e:
        print(f"Last.fm API error for '{artist_name}': {e}")
        return []


def get_similar_tracks_batch(
    tracks: List[Tuple[str, str]],
    limit: int = 20,
    max_workers: int = 10,
) -> Dict[Tuple[str, str], List[Dict]]:
    """
    Get similar tracks for multiple tracks in parallel.

    Args:
        tracks: List of (artist, track_name) tuples
        limit: Max similar tracks per query
        max_workers: Number of parallel threads

    Returns:
        Dict mapping (artist, track) to list of similar tracks
    """
    results = {}

    def fetch_one(track_tuple):
        artist, track = track_tuple
        return track_tuple, get_similar_tracks(artist, track, limit)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_one, t) for t in tracks]
        for future in as_completed(futures):
            try:
                key, similar = future.result()
                results[key] = similar
            except Exception:
                pass

    return results


def get_artist_info(artist_name: str) -> Optional[Dict]:
    """
    Get detailed info about an artist from Last.fm.

    Returns dict with: name, bio, tags, similar artists, etc.
    """
    if not artist_name or not LASTFM_API_KEY:
        return None

    try:
        response = requests.get(
            LASTFM_API_BASE,
            params={
                "method": "artist.getinfo",
                "artist": artist_name,
                "api_key": LASTFM_API_KEY,
                "format": "json",
            },
            timeout=10,
            headers={"User-Agent": "SpotifyHistoryPlaylistMaker/1.0"},
        )
        response.raise_for_status()
        data = response.json()

        artist = data.get("artist", {})
        tags = artist.get("tags", {}).get("tag", [])

        return {
            "name": artist.get("name", ""),
            "mbid": artist.get("mbid", ""),
            "url": artist.get("url", ""),
            "tags": [t.get("name", "") for t in tags] if isinstance(tags, list) else [],
            "bio": artist.get("bio", {}).get("summary", ""),
        }

    except Exception:
        return None
