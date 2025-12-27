from typing import Optional, List, Dict
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from .config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN


# Token cache with expiration tracking
_token_cache: Dict = {}


def get_spotify_client() -> spotipy.Spotify:
    """Get authenticated Spotify client with automatic token refresh."""
    global _token_cache

    # Check if we have a valid cached token (with 5 min buffer)
    now = time.time()
    if _token_cache and _token_cache.get("expires_at", 0) > now + 300:
        return spotipy.Spotify(auth=_token_cache["access_token"])

    # Refresh the token
    oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="user-read-recently-played playlist-modify-public playlist-modify-private",
        cache_path="/tmp/.spotify-cache",
    )
    token = oauth.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    _token_cache = token
    return spotipy.Spotify(auth=token["access_token"])


def get_tracks_bulk(track_ids: List[str]) -> List[Dict]:
    """Get multiple tracks info with album art (max 50 per call)."""
    sp = get_spotify_client()
    results = []
    # Filter out None/empty track IDs
    valid_ids = [tid for tid in track_ids if tid]
    for i in range(0, len(valid_ids), 50):
        batch = valid_ids[i : i + 50]
        try:
            response = sp.tracks(batch)
            results.extend(response.get("tracks", []))
        except Exception:
            continue
    return [t for t in results if t]  # Filter out None results


def enrich_tracks_with_spotify_data(tracks: List[Dict]) -> List[Dict]:
    """Add Spotify metadata (album art, preview URL) to track list."""
    track_ids = [t.get("track_id") for t in tracks if t.get("track_id")]
    if not track_ids:
        return tracks
    
    spotify_data = get_tracks_bulk(track_ids)
    spotify_map = {t["id"]: t for t in spotify_data if t}
    
    enriched = []
    for track in tracks:
        tid = track.get("track_id")
        sp_track = spotify_map.get(tid, {})
        album = sp_track.get("album", {})
        images = album.get("images", [])
        
        enriched.append({
            **track,
            "image_url": images[0]["url"] if images else None,
            "album": album.get("name"),
            "preview_url": sp_track.get("preview_url"),
            "spotify_url": sp_track.get("external_urls", {}).get("spotify"),
        })
    return enriched


def search_tracks_by_artist(artist_name: str, limit: int = 20) -> List[Dict]:
    """Search for tracks by an artist."""
    sp = get_spotify_client()
    results = sp.search(q=f"artist:{artist_name}", type="track", limit=limit)
    return results.get("tracks", {}).get("items", [])


def search_tracks_by_genre(genre: str, limit: int = 20) -> List[Dict]:
    """Search for tracks by genre."""
    sp = get_spotify_client()
    results = sp.search(q=f"genre:{genre}", type="track", limit=limit)
    return results.get("tracks", {}).get("items", [])


def get_artist_related(artist_id: str) -> List[Dict]:
    """Get artists related to a given artist."""
    sp = get_spotify_client()
    try:
        results = sp.artist_related_artists(artist_id)
        return results.get("artists", [])
    except Exception:
        return []


def get_artist_top_tracks(artist_id: str, market: str = "US") -> List[Dict]:
    """Get top tracks for an artist."""
    sp = get_spotify_client()
    try:
        results = sp.artist_top_tracks(artist_id, country=market)
        return results.get("tracks", [])
    except Exception:
        return []


def get_artist_info(artist_id: str) -> Optional[Dict]:
    """Get artist info from Spotify."""
    sp = get_spotify_client()
    try:
        return sp.artist(artist_id)
    except Exception:
        return None


def get_artists_bulk(artist_ids: List[str]) -> List[Dict]:
    """Get multiple artists info (max 50 per call)."""
    sp = get_spotify_client()
    results = []
    valid_ids = [aid for aid in artist_ids if aid]
    for i in range(0, len(valid_ids), 50):
        batch = valid_ids[i : i + 50]
        try:
            response = sp.artists(batch)
            results.extend(a for a in response.get("artists", []) if a)
        except Exception:
            continue
    return results


def search_artist(name: str) -> Optional[Dict]:
    """Search for an artist by name and return the top result."""
    sp = get_spotify_client()
    try:
        results = sp.search(q=f"artist:{name}", type="artist", limit=1)
        artists = results.get("artists", {}).get("items", [])
        return artists[0] if artists else None
    except Exception:
        return None


def get_audio_features(track_ids: List[str]) -> List[Dict]:
    """
    Get audio features for tracks (energy, valence, tempo, etc.).

    Note: As of late 2024, Spotify restricted audio_features API access.
    This will return empty results if the app doesn't have Extended Quota Mode.
    The playlist builder handles this gracefully with fallback scoring.
    """
    sp = get_spotify_client()
    results = []
    valid_ids = [tid for tid in track_ids if tid]
    for i in range(0, len(valid_ids), 100):
        batch = valid_ids[i : i + 100]
        try:
            features = sp.audio_features(batch)
            if features:
                results.extend(f for f in features if f)
        except Exception:
            # Likely 403 due to Spotify API restrictions
            pass
    return results


def get_recommendations(
    seed_artists: List[str] = None,
    seed_tracks: List[str] = None,
    seed_genres: List[str] = None,
    limit: int = 50,
    **kwargs
) -> List[Dict]:
    """
    Get personalized track recommendations from Spotify.

    Can use up to 5 seeds total (artists + tracks + genres).
    kwargs can include target_* params like target_energy, target_valence, etc.
    """
    sp = get_spotify_client()
    try:
        results = sp.recommendations(
            seed_artists=seed_artists or [],
            seed_tracks=seed_tracks or [],
            seed_genres=seed_genres or [],
            limit=limit,
            **kwargs
        )
        return results.get("tracks", [])
    except Exception:
        return []


def get_artist_albums(artist_id: str, limit: int = 10) -> List[Dict]:
    """Get albums for an artist (not just singles/compilations)."""
    sp = get_spotify_client()
    try:
        results = sp.artist_albums(
            artist_id,
            album_type="album",
            limit=limit
        )
        return results.get("items", [])
    except Exception:
        return []


def get_album_tracks(album_id: str) -> List[Dict]:
    """Get all tracks from an album."""
    sp = get_spotify_client()
    try:
        results = sp.album_tracks(album_id, limit=50)
        tracks = results.get("items", [])
        # Get full track info with popularity
        if tracks:
            track_ids = [t["id"] for t in tracks if t.get("id")]
            full_tracks = get_tracks_bulk(track_ids)
            return full_tracks
        return tracks
    except Exception:
        return []


def search_tracks_advanced(
    query: str,
    limit: int = 50,
    market: str = "US"
) -> List[Dict]:
    """Search tracks with full query flexibility."""
    sp = get_spotify_client()
    try:
        results = sp.search(q=query, type="track", limit=limit, market=market)
        return results.get("tracks", {}).get("items", [])
    except Exception:
        return []


def get_new_releases(limit: int = 50, country: str = "US") -> List[Dict]:
    """Get new album releases."""
    sp = get_spotify_client()
    try:
        results = sp.new_releases(limit=limit, country=country)
        return results.get("albums", {}).get("items", [])
    except Exception:
        return []


def create_playlist(name: str, track_ids: List[str], description: str = "") -> Dict:
    """Create a new playlist with the given tracks."""
    sp = get_spotify_client()
    user = sp.current_user()
    playlist = sp.user_playlist_create(
        user["id"], name, public=True, description=description
    )
    if track_ids:
        track_uris = [f"spotify:track:{tid}" for tid in track_ids if tid]
        for i in range(0, len(track_uris), 100):
            sp.playlist_add_items(playlist["id"], track_uris[i : i + 100])
    return playlist
