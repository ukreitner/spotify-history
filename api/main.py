from typing import List, Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .services.analyzer import (
    get_overview, get_overview_split, get_top_artists_stats, get_top_genres_stats,
    get_listening_patterns, get_listening_streaks
)
from .db import get_top_tracks
from .spotify_client import enrich_tracks_with_spotify_data
from .services.forgotten_gems import find_forgotten_gems
from .services.discover import discover_new_artists
from .services.mood import generate_mood_playlist, get_available_moods
from .services.podcasts import (
    get_podcast_stats, get_top_shows, get_show_episodes,
    get_recently_played_episodes, get_podcast_backlog
)
from .spotify_client import create_playlist

app = FastAPI(title="Spotify History Recommendations", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ContentTypeParam = Literal["all", "music", "podcast"]


# Stats endpoints
@app.get("/api/stats/overview")
def stats_overview(content_type: ContentTypeParam = "all"):
    """Get listening overview stats."""
    return get_overview(content_type)


@app.get("/api/stats/overview/split")
def stats_overview_split():
    """Get listening stats split by music vs podcasts."""
    return get_overview_split()


@app.get("/api/stats/artists")
def stats_artists(limit: int = 20, content_type: ContentTypeParam = "all"):
    """Get top artists by play count."""
    return get_top_artists_stats(limit, content_type)


@app.get("/api/stats/genres")
def stats_genres(limit: int = 20, content_type: ContentTypeParam = "all"):
    """Get top genres by play count."""
    return get_top_genres_stats(limit, content_type)


@app.get("/api/stats/tracks")
def stats_tracks(limit: int = 20, content_type: ContentTypeParam = "all"):
    """Get top tracks by play count with album art."""
    tracks = get_top_tracks(limit, content_type)
    return enrich_tracks_with_spotify_data(tracks)


@app.get("/api/stats/patterns")
def stats_patterns(content_type: ContentTypeParam = "all"):
    """Get listening patterns by hour, day, and month."""
    return get_listening_patterns(content_type)


@app.get("/api/stats/streaks")
def stats_streaks(content_type: ContentTypeParam = "all"):
    """Get listening streak information."""
    return get_listening_streaks(content_type)


# Podcast-specific endpoints
@app.get("/api/podcasts/stats")
def podcast_stats():
    """Get podcast listening statistics."""
    return get_podcast_stats()


@app.get("/api/podcasts/shows")
def podcast_shows(limit: int = 20):
    """Get top podcast shows by episode count."""
    return get_top_shows(limit)


@app.get("/api/podcasts/episodes/{show}")
def podcast_episodes(show: str, limit: int = 50):
    """Get episodes for a specific podcast show."""
    return get_show_episodes(show, limit)


@app.get("/api/podcasts/recent")
def podcast_recent(limit: int = 20):
    """Get recently played podcast episodes."""
    return get_recently_played_episodes(limit)


@app.get("/api/podcasts/backlog")
def podcast_backlog(limit: int = 20):
    """Find podcast episodes you may have started but not finished."""
    return get_podcast_backlog(limit=limit)


# Recommendation endpoints
@app.get("/api/recommendations/gems")
def recommendations_gems(
    min_plays: int = 5,
    months_absent: int = 3,
    limit: int = 20,
    content_type: ContentTypeParam = "music"
):
    """Find forgotten gems - tracks you loved but haven't played recently."""
    return find_forgotten_gems(min_plays, months_absent, limit, content_type)


@app.get("/api/recommendations/discover")
def recommendations_discover(limit: int = 20):
    """Discover new artists similar to your favorites."""
    return discover_new_artists(limit)


@app.get("/api/recommendations/mood/{mood}")
def recommendations_mood(mood: str, limit: int = 25):
    """Generate a mood-based playlist."""
    valid_moods = ["focus", "workout", "chill", "party", "melancholy"]
    if mood not in valid_moods:
        raise HTTPException(status_code=400, detail=f"Invalid mood. Use: {', '.join(valid_moods)}")
    return generate_mood_playlist(mood, limit)


@app.get("/api/recommendations/moods")
def available_moods():
    """Get available mood profiles."""
    return get_available_moods()


@app.get("/api/recommendations/custom")
def recommendations_custom(
    genres: str = "",
    exclude_genres: str = "",
    min_plays: int = 1,
    max_days: int = 365,
    include_unplayed: bool = False,
    artist_filter: str = "all",
    limit: int = 30,
):
    """Generate a custom playlist with fine-tuned filters."""
    from .services.custom_playlist import generate_custom_playlist
    
    genre_list = [g.strip() for g in genres.split(",") if g.strip()]
    exclude_list = [g.strip() for g in exclude_genres.split(",") if g.strip()]
    
    return generate_custom_playlist(
        genres=genre_list,
        exclude_genres=exclude_list,
        min_plays=min_plays,
        max_days=max_days,
        include_unplayed=include_unplayed,
        artist_filter=artist_filter,
        limit=limit,
    )


# Playlist creation
class CreatePlaylistRequest(BaseModel):
    name: str
    track_ids: List[str]
    description: str = ""


@app.post("/api/playlists/create")
def playlists_create(request: CreatePlaylistRequest):
    """Create a Spotify playlist with the given tracks."""
    playlist = create_playlist(request.name, request.track_ids, request.description)
    return {
        "id": playlist.get("id"),
        "name": playlist.get("name"),
        "url": playlist.get("external_urls", {}).get("spotify"),
    }


@app.get("/api/health")
def health():
    """Health check."""
    return {"status": "ok"}
