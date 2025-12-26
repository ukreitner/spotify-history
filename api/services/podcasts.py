from typing import List, Dict
from ..db import get_top_podcasts, get_podcast_episodes, get_all_tracks_with_counts
from datetime import datetime


def get_podcast_stats() -> Dict:
    """Get podcast listening statistics."""
    tracks = get_all_tracks_with_counts("podcast")

    total_episodes = len(tracks)
    total_plays = sum(t["play_count"] for t in tracks.values())

    # Get unique shows
    shows = set()
    for track in tracks.values():
        shows.add(track["artist"])

    return {
        "total_plays": total_plays,
        "unique_shows": len(shows),
        "unique_episodes": total_episodes,
    }


def get_top_shows(limit: int = 20) -> List[Dict]:
    """Get top podcast shows by episode count."""
    shows = get_top_podcasts(limit)
    return [{"show": s["artist"], "episode_count": s["play_count"]} for s in shows]


def get_show_episodes(show: str, limit: int = 50) -> List[Dict]:
    """Get episodes for a specific podcast show."""
    return get_podcast_episodes(show, limit)


def get_recently_played_episodes(limit: int = 20) -> List[Dict]:
    """Get recently played podcast episodes."""
    tracks = get_all_tracks_with_counts("podcast")

    # Sort by last played
    sorted_tracks = sorted(
        tracks.values(),
        key=lambda x: x["last_played"],
        reverse=True
    )

    return [
        {
            "episode": t["track"],
            "show": t["artist"],
            "play_count": t["play_count"],
            "last_played": t["last_played"],
        }
        for t in sorted_tracks[:limit]
    ]


def get_podcast_backlog(min_plays: int = 1, limit: int = 20) -> List[Dict]:
    """
    Find podcast episodes you started but may not have finished.
    (Episodes played only once and not recently)
    """
    tracks = get_all_tracks_with_counts("podcast")
    now = datetime.utcnow()

    backlog = []
    for track in tracks.values():
        if track["play_count"] > min_plays:
            continue

        # Parse last played date
        try:
            last_played_str = track["last_played"].replace("Z", "").replace("+00:00", "")
            if "." in last_played_str:
                last_played = datetime.fromisoformat(last_played_str)
            else:
                last_played = datetime.fromisoformat(last_played_str)

            days_since = (now - last_played).days
            if days_since > 7:  # Not played in last week
                backlog.append({
                    "episode": track["track"],
                    "show": track["artist"],
                    "days_since_played": days_since,
                })
        except (ValueError, TypeError):
            continue

    # Sort by days since played
    backlog.sort(key=lambda x: x["days_since_played"], reverse=True)
    return backlog[:limit]
