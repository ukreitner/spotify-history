from typing import Optional, List, Dict
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from functools import lru_cache
from .config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN


@lru_cache(maxsize=1)
def get_spotify_client() -> spotipy.Spotify:
    """Get authenticated Spotify client (cached)."""
    oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="user-read-recently-played playlist-modify-public playlist-modify-private",
        cache_path="/tmp/.spotify-cache",
    )
    token = oauth.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
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
    """Get audio features for tracks (energy, valence, tempo, etc.)."""
    sp = get_spotify_client()
    results = []
    valid_ids = [tid for tid in track_ids if tid]
    for i in range(0, len(valid_ids), 100):
        batch = valid_ids[i : i + 100]
        try:
            features = sp.audio_features(batch)
            results.extend(f for f in features if f)
        except Exception:
            continue
    return results


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
