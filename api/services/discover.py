from typing import List, Dict, Set
from ..db import get_top_artists, get_all_artist_ids, get_top_genres
from ..spotify_client import search_artist, search_tracks_by_genre, get_artist_top_tracks


def discover_new_artists(limit: int = 20) -> List[Dict]:
    """
    Discover new artists using genre-based search.
    
    Strategy:
    1. Get user's top genres
    2. Search for tracks in those genres
    3. Extract artists from those tracks
    4. Filter out artists user already knows
    """
    # Get all artists the user has listened to (for filtering)
    known_artist_names = get_all_artist_ids("music")
    known_lower = {name.lower() for name in known_artist_names}
    
    # Get user's top genres
    top_genres = get_top_genres(limit=20, content_type="music")
    
    # Get user's top artists for context
    top_artists = get_top_artists(limit=10, content_type="music")
    top_artist_names = [a["artist"].split(", ")[0] for a in top_artists]
    
    discovered: Dict[str, Dict] = {}  # artist_id -> artist data
    
    # Search for tracks in each top genre
    for genre_data in top_genres[:10]:
        genre = genre_data["genre"]
        
        # Search for tracks in this genre
        tracks = search_tracks_by_genre(genre, limit=30)
        
        for track in tracks:
            for artist in track.get("artists", []):
                artist_name = artist.get("name", "")
                artist_id = artist.get("id")
                
                if not artist_id or not artist_name:
                    continue
                
                # Skip if user already knows this artist
                if artist_name.lower() in known_lower:
                    continue
                
                # Skip if already discovered
                if artist_id in discovered:
                    discovered[artist_id]["relevance"] += 1
                    continue
                
                # Get album art from the track
                album = track.get("album", {})
                images = album.get("images", [])
                
                discovered[artist_id] = {
                    "artist_id": artist_id,
                    "artist_name": artist_name,
                    "genres": [genre],
                    "image_url": images[0]["url"] if images else None,
                    "relevance": 1,
                    "sample_track": track.get("name"),
                    "sample_track_id": track.get("id"),
                    "preview_url": track.get("preview_url"),
                    "found_via_genre": genre,
                }
    
    # Sort by relevance (found in multiple genre searches)
    sorted_artists = sorted(
        discovered.values(),
        key=lambda x: x["relevance"],
        reverse=True
    )[:limit]
    
    # Try to get better images and more tracks for top results
    for artist in sorted_artists[:10]:
        artist_info = search_artist(artist["artist_name"])
        if artist_info:
            images = artist_info.get("images", [])
            if images:
                artist["image_url"] = images[0]["url"]
            artist["genres"] = artist_info.get("genres", artist.get("genres", []))[:3]
    
    return sorted_artists
