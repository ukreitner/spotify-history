from typing import List, Dict
from datetime import datetime, timedelta
from math import log
from ..db import get_all_tracks_with_counts, ContentType
from ..spotify_client import enrich_tracks_with_spotify_data


def parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string, handling various formats."""
    dt_str = dt_str.replace("Z", "").replace("+00:00", "")
    if "." in dt_str:
        return datetime.fromisoformat(dt_str)
    return datetime.fromisoformat(dt_str)


def calculate_gem_score(track: Dict, now: datetime) -> float:
    """
    Calculate a "gem score" that balances:
    - How much you played it (love intensity)
    - How long it's been forgotten
    - Bonus for tracks with sustained listening (not one-hit wonders)
    """
    play_count = track["play_count"]
    last_played = parse_datetime(track["last_played"])
    first_played = parse_datetime(track.get("first_played", track["last_played"]))
    
    days_since = (now - last_played).days
    listening_span = max((last_played - first_played).days, 1)
    
    # Love intensity: plays adjusted by listening span
    # A track played 20 times over 6 months > 20 times in 1 day
    intensity = play_count * min(log(listening_span + 1) / 3, 2)
    
    # Forgottenness: exponential decay based on absence
    # More forgotten = higher score, but cap it
    forgotten_factor = min(days_since / 30, 12)  # Cap at 12 months worth
    
    # Combine: intensity * forgottenness, with diminishing returns
    score = intensity * (1 + log(forgotten_factor + 1))
    
    return round(score, 1)


def find_forgotten_gems(
    min_plays: int = 5,
    months_absent: int = 3,
    limit: int = 20,
    content_type: ContentType = "music",
) -> List[Dict]:
    """
    Find tracks that were played frequently but haven't been played recently.
    
    Improved algorithm considers:
    - Total play count
    - Listening span (sustained love vs one-time binge)
    - Time since last play
    - Fetches album art from Spotify
    """
    tracks = get_all_tracks_with_counts(content_type)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=months_absent * 30)
    
    gems = []
    for track in tracks.values():
        if track["play_count"] < min_plays:
            continue
        
        last_played = parse_datetime(track["last_played"])
        if last_played >= cutoff:
            continue
        
        days_since = (now - last_played).days
        score = calculate_gem_score(track, now)
        
        gems.append({
            "track_id": track["track_id"],
            "track": track["track"],
            "artist": track["artist"],
            "play_count": track["play_count"],
            "last_played": track["last_played"],
            "days_since_played": days_since,
            "score": score,
        })
    
    # Sort by score descending
    gems.sort(key=lambda x: x["score"], reverse=True)
    top_gems = gems[:limit]
    
    # Enrich with Spotify data (album art, etc.)
    return enrich_tracks_with_spotify_data(top_gems)
