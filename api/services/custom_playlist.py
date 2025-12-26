from typing import List, Dict, Set
from datetime import datetime, timedelta
import random
from ..db import get_all_tracks_with_counts, get_top_artists, get_top_genres, query_all_dbs
from ..spotify_client import enrich_tracks_with_spotify_data, search_tracks_by_genre


def generate_custom_playlist(
    genres: List[str] = None,
    exclude_genres: List[str] = None,
    min_plays: int = 1,
    max_days: int = 365,
    discovery_ratio: int = 30,
    artist_filter: str = "all",
    limit: int = 30,
) -> List[Dict]:
    """
    Generate a custom playlist with fine-tuned filters.
    
    Args:
        genres: List of genres to include (empty = all)
        exclude_genres: List of genres to exclude
        min_plays: Minimum play count for tracks from history
        max_days: Maximum days since last play
        discovery_ratio: Percentage of new music (0-100)
        artist_filter: 'all', 'top', or 'diverse'
        limit: Maximum number of tracks
    """
    genres = genres or []
    exclude_genres = exclude_genres or []
    genres_lower = {g.lower() for g in genres}
    exclude_lower = {g.lower() for g in exclude_genres}
    
    # Calculate how many tracks from history vs discovery
    discovery_count = int(limit * discovery_ratio / 100)
    history_count = limit - discovery_count
    
    result = []
    
    # === PART 1: Get tracks from listening history ===
    if history_count > 0:
        all_tracks = get_all_tracks_with_counts("music")
        
        # Build genre map for tracks
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
        
        # Get top artists if needed
        top_artist_names = set()
        if artist_filter == "top":
            top_artists = get_top_artists(limit=30, content_type="music")
            top_artist_names = {a["artist"].lower() for a in top_artists}
        
        now = datetime.utcnow()
        cutoff_date = now - timedelta(days=max_days)
        
        # Filter and score tracks
        candidates = []
        artist_counts: Dict[str, int] = {}
        
        for tid, track_data in all_tracks.items():
            if not tid:
                continue
            
            # Check play count
            if track_data["play_count"] < min_plays:
                continue
            
            # Check recency
            try:
                last_played_str = track_data["last_played"].replace("Z", "").replace("+00:00", "")
                if "." in last_played_str:
                    last_played = datetime.fromisoformat(last_played_str)
                else:
                    last_played = datetime.fromisoformat(last_played_str)
                
                if last_played < cutoff_date:
                    continue
            except (ValueError, AttributeError):
                continue
            
            # Check genres
            track_genre_set = track_genres.get(tid, set())
            
            # If genres specified, track must have at least one matching genre
            if genres_lower:
                has_match = any(
                    any(g in tg or tg in g for g in genres_lower)
                    for tg in track_genre_set
                )
                if not has_match:
                    continue
            
            # Check excluded genres
            if exclude_lower:
                has_excluded = any(
                    any(g in tg or tg in g for g in exclude_lower)
                    for tg in track_genre_set
                )
                if has_excluded:
                    continue
            
            # Check artist filter
            artist_lower = track_data["artist"].lower()
            if artist_filter == "top" and artist_lower not in top_artist_names:
                continue
            
            # For diverse mode, track artist counts
            if artist_filter == "diverse":
                first_artist = artist_lower.split(",")[0].strip()
                if artist_counts.get(first_artist, 0) >= 2:
                    continue
                artist_counts[first_artist] = artist_counts.get(first_artist, 0) + 1
            
            candidates.append({
                "track_id": tid,
                "track": track_data["track"],
                "artist": track_data["artist"],
                "play_count": track_data["play_count"],
                "last_played": track_data["last_played"],
                "genres": list(track_genre_set)[:3],
                "source": "history",
            })
        
        # Sort by play count and take required number
        candidates.sort(key=lambda x: x["play_count"], reverse=True)
        result.extend(candidates[:history_count])
    
    # === PART 2: Get new tracks from Spotify ===
    if discovery_count > 0:
        existing_ids = {t["track_id"] for t in result}
        
        # Use specified genres, or fall back to user's top genres
        search_genres = list(genres_lower) if genres_lower else []
        if not search_genres:
            top_user_genres = get_top_genres(limit=10, content_type="music")
            search_genres = [g["genre"] for g in top_user_genres]
        
        # Also exclude any artists already in the playlist for diversity
        existing_artists = {t["artist"].lower().split(",")[0].strip() for t in result}
        
        discovered = []
        genres_to_search = search_genres[:8] if search_genres else ["pop", "rock", "indie", "electronic"]
        
        for genre in genres_to_search:
            if len(discovered) >= discovery_count:
                break
            
            # Check if genre should be excluded
            if any(ex in genre.lower() or genre.lower() in ex for ex in exclude_lower):
                continue
            
            search_results = search_tracks_by_genre(genre, limit=30)
            random.shuffle(search_results)  # Add variety
            
            for track in search_results:
                if len(discovered) >= discovery_count:
                    break
                    
                track_id = track.get("id")
                if not track_id or track_id in existing_ids:
                    continue
                
                # Skip artists we already have for diversity
                track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                if any(a.split(",")[0].strip() in existing_artists for a in track_artists):
                    continue
                
                album = track.get("album", {})
                images = album.get("images", [])
                
                discovered.append({
                    "track_id": track_id,
                    "track": track.get("name", "Unknown"),
                    "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                    "play_count": 0,
                    "image_url": images[0]["url"] if images else None,
                    "preview_url": track.get("preview_url"),
                    "spotify_url": track.get("external_urls", {}).get("spotify"),
                    "source": "discovery",
                    "discovered_via": genre,
                })
                existing_ids.add(track_id)
                existing_artists.add(track_artists[0].split(",")[0].strip() if track_artists else "")
        
        result.extend(discovered)
    
    # === PART 3: Enrich history tracks with Spotify data ===
    history_tracks = [t for t in result if t.get("source") == "history"]
    if history_tracks:
        enriched = enrich_tracks_with_spotify_data(history_tracks)
        enriched_map = {t["track_id"]: t for t in enriched}
        for i, t in enumerate(result):
            if t["track_id"] in enriched_map and t.get("source") == "history":
                result[i] = {**t, **enriched_map[t["track_id"]]}
    
    # Shuffle to mix history and discovery
    if discovery_ratio > 0 and discovery_ratio < 100:
        random.shuffle(result)
    
    return result[:limit]
