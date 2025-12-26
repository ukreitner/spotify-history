from typing import List, Dict, Set
from ..db import get_all_tracks_with_counts, get_top_artists, get_top_genres
from ..spotify_client import (
    search_tracks_by_genre, search_artist, 
    get_artist_top_tracks, enrich_tracks_with_spotify_data
)

# Mood profiles with associated genres
MOOD_PROFILES = {
    "focus": {
        "description": "Calm, instrumental tracks for deep work",
        "genres": ["ambient", "classical", "piano", "soundtrack", "study", "lo-fi", 
                   "instrumental", "new age", "meditation", "chillhop"],
        "anti_genres": ["metal", "punk", "hip hop", "rap", "edm", "party"],
    },
    "workout": {
        "description": "High-energy bangers to power your exercise",
        "genres": ["electronic", "edm", "dance", "pop", "hip hop", "rap", 
                   "rock", "metal", "drum and bass", "house", "techno"],
        "anti_genres": ["ambient", "classical", "folk", "acoustic", "sleep"],
    },
    "chill": {
        "description": "Relaxed vibes for unwinding",
        "genres": ["soul", "jazz", "r&b", "indie", "folk", "acoustic", 
                   "soft rock", "bossa nova", "lounge", "neo soul"],
        "anti_genres": ["metal", "punk", "hardcore", "edm"],
    },
    "party": {
        "description": "Danceable hits to get the party started",
        "genres": ["pop", "dance", "disco", "electronic", "house", "funk",
                   "hip hop", "reggaeton", "latin", "party"],
        "anti_genres": ["ambient", "classical", "folk", "acoustic"],
    },
    "melancholy": {
        "description": "Sad, introspective tracks for rainy days",
        "genres": ["indie", "folk", "singer-songwriter", "acoustic", "sad",
                   "alternative", "slowcore", "dream pop", "emo"],
        "anti_genres": ["happy", "party", "edm", "dance"],
    },
}


def genre_matches_mood(track_genres: Set[str], mood: str) -> int:
    """
    Score how well a track's genres match a mood profile.
    Returns a score from -10 to 10.
    """
    if mood not in MOOD_PROFILES:
        return 0
    
    profile = MOOD_PROFILES[mood]
    mood_genres = set(g.lower() for g in profile["genres"])
    anti_genres = set(g.lower() for g in profile.get("anti_genres", []))
    track_genres_lower = set(g.lower() for g in track_genres)
    
    score = 0
    
    # Positive matches
    for genre in track_genres_lower:
        for mood_genre in mood_genres:
            if mood_genre in genre or genre in mood_genre:
                score += 2
                break
    
    # Negative matches (anti-genres)
    for genre in track_genres_lower:
        for anti in anti_genres:
            if anti in genre or genre in anti:
                score -= 3
                break
    
    return score


def generate_mood_playlist(mood: str, limit: int = 25) -> List[Dict]:
    """
    Generate a mood-based playlist using genre matching from user's listening history.
    
    Strategy:
    1. Get user's tracks with their genres
    2. Score each track based on genre match to mood
    3. Return top scoring tracks
    """
    if mood not in MOOD_PROFILES:
        return []
    
    profile = MOOD_PROFILES[mood]
    mood_genres = set(g.lower() for g in profile["genres"])
    
    # Get user's tracks
    all_tracks = get_all_tracks_with_counts("music")
    
    # Get genre information from the database
    from ..db import query_all_dbs
    
    # Build a map of track_id -> genres
    track_genres: Dict[str, Set[str]] = {}
    for db_result in query_all_dbs("SELECT track_id, genre FROM plays WHERE track_id IS NOT NULL AND genre != ''"):
        tid = db_result.get("track_id")
        genre_str = db_result.get("genre", "")
        if tid and genre_str:
            if tid not in track_genres:
                track_genres[tid] = set()
            for g in genre_str.split(", "):
                if g.strip():
                    track_genres[tid].add(g.strip().lower())
    
    # Score tracks
    scored_tracks = []
    for tid, track_data in all_tracks.items():
        if not tid or track_data["play_count"] < 2:
            continue
        
        genres = track_genres.get(tid, set())
        if not genres:
            continue
        
        score = genre_matches_mood(genres, mood)
        
        if score > 0:  # Only include positive matches
            scored_tracks.append({
                "track_id": tid,
                "track": track_data["track"],
                "artist": track_data["artist"],
                "score": score,
                "play_count": track_data["play_count"],
                "genres": list(genres)[:3],
            })
    
    # Sort by score, then by play count
    scored_tracks.sort(key=lambda x: (x["score"], x["play_count"]), reverse=True)
    top_tracks = scored_tracks[:limit]
    
    # If we don't have enough tracks from history, search Spotify
    if len(top_tracks) < limit:
        needed = limit - len(top_tracks)
        existing_ids = {t["track_id"] for t in top_tracks}
        
        for genre in profile["genres"][:5]:
            if needed <= 0:
                break
            
            search_results = search_tracks_by_genre(genre, limit=20)
            for track in search_results:
                track_id = track.get("id")
                if track_id and track_id not in existing_ids:
                    album = track.get("album", {})
                    images = album.get("images", [])
                    
                    top_tracks.append({
                        "track_id": track_id,
                        "track": track.get("name", "Unknown"),
                        "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                        "score": 1,
                        "play_count": 0,
                        "image_url": images[0]["url"] if images else None,
                        "preview_url": track.get("preview_url"),
                        "spotify_url": track.get("external_urls", {}).get("spotify"),
                        "from_search": True,
                    })
                    existing_ids.add(track_id)
                    needed -= 1
                    
                    if needed <= 0:
                        break
    
    # Enrich with Spotify data for tracks from history
    history_tracks = [t for t in top_tracks if not t.get("from_search")]
    if history_tracks:
        enriched = enrich_tracks_with_spotify_data(history_tracks)
        # Merge enriched data back
        enriched_map = {t["track_id"]: t for t in enriched}
        for i, t in enumerate(top_tracks):
            if t["track_id"] in enriched_map:
                top_tracks[i] = {**t, **enriched_map[t["track_id"]]}
    
    return top_tracks[:limit]


def get_available_moods() -> List[Dict]:
    """Get list of available mood profiles."""
    return [
        {
            "id": mood,
            "name": mood.replace("_", " ").title(),
            "description": profile["description"]
        }
        for mood, profile in MOOD_PROFILES.items()
    ]
