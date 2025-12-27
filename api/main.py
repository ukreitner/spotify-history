from typing import List, Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .services.analyzer import (
    get_overview, get_overview_split, get_top_artists_stats, get_top_genres_stats,
    get_listening_patterns, get_listening_streaks
)
from .db import get_top_tracks, search_user_tracks, get_recent_tracks
from .spotify_client import (
    enrich_tracks_with_spotify_data, create_playlist, search_tracks_advanced
)
from .services.forgotten_gems import find_forgotten_gems
from .services.discover import discover_new_artists
from .services.mood import generate_mood_playlist, get_available_moods
from .services.podcasts import (
    get_podcast_stats, get_top_shows, get_show_episodes,
    get_recently_played_episodes, get_podcast_backlog
)

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
    discovery_ratio: int = 30,
    artist_filter: str = "all",
    limit: int = 30,
    # Audio feature filters (0-100)
    energy_min: Optional[int] = None,
    energy_max: Optional[int] = None,
    valence_min: Optional[int] = None,
    valence_max: Optional[int] = None,
    danceability_min: Optional[int] = None,
    danceability_max: Optional[int] = None,
    tempo_min: Optional[int] = None,  # In BPM
    tempo_max: Optional[int] = None,
    acousticness_min: Optional[int] = None,
    acousticness_max: Optional[int] = None,
    # Other filters
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    exclude_artists: str = "",
):
    """Generate a custom playlist with fine-tuned filters including audio features."""
    from .services.custom_playlist import generate_custom_playlist

    genre_list = [g.strip() for g in genres.split(",") if g.strip()]
    exclude_genre_list = [g.strip() for g in exclude_genres.split(",") if g.strip()]
    exclude_artist_list = [a.strip() for a in exclude_artists.split(",") if a.strip()]

    return generate_custom_playlist(
        genres=genre_list,
        exclude_genres=exclude_genre_list,
        min_plays=min_plays,
        max_days=max_days,
        discovery_ratio=discovery_ratio,
        artist_filter=artist_filter,
        limit=limit,
        energy_min=energy_min,
        energy_max=energy_max,
        valence_min=valence_min,
        valence_max=valence_max,
        danceability_min=danceability_min,
        danceability_max=danceability_max,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        acousticness_min=acousticness_min,
        acousticness_max=acousticness_max,
        year_min=year_min,
        year_max=year_max,
        exclude_artists=exclude_artist_list,
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


# === NEW VIBE-BASED PLAYLIST ENDPOINTS ===

class VibePlaylistRequest(BaseModel):
    anchor_track_ids: List[str]
    track_count: int = 30
    discovery_ratio: int = 50
    flow_mode: str = "smooth"  # smooth, energy_arc, shuffle
    exclude_artists: List[str] = []


@app.post("/api/recommendations/vibe")
def recommendations_vibe(request: VibePlaylistRequest):
    """
    Generate a coherent playlist based on anchor tracks.

    Select 1-5 anchor tracks that define the vibe you want.
    The algorithm finds similar tracks from your history and new discoveries.
    """
    from .services.custom_playlist import generate_vibe_playlist

    if not request.anchor_track_ids:
        raise HTTPException(status_code=400, detail="Need at least 1 anchor track")
    if len(request.anchor_track_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 anchor tracks")
    if request.flow_mode not in ("smooth", "energy_arc", "shuffle"):
        raise HTTPException(status_code=400, detail="Invalid flow mode")

    try:
        return generate_vibe_playlist(
            anchor_track_ids=request.anchor_track_ids,
            track_count=request.track_count,
            discovery_ratio=request.discovery_ratio,
            flow_mode=request.flow_mode,
            exclude_artists=request.exclude_artists,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tracks/search")
def tracks_search(q: str, limit: int = 20):
    """
    Search Spotify for tracks by name or artist.

    Use this to find anchor tracks from any song on Spotify.
    """
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")

    tracks = search_tracks_advanced(q, limit=limit)

    # Format for frontend
    return [
        {
            "track_id": t.get("id"),
            "track": t.get("name"),
            "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
            "image_url": (t.get("album", {}).get("images", [{}])[0].get("url")
                         if t.get("album", {}).get("images") else None),
            "source": "spotify",
        }
        for t in tracks if t and t.get("id")
    ]


@app.get("/api/tracks/recent")
def tracks_recent(days: int = 7, limit: int = 20):
    """
    Get recently played tracks from your history.

    Great for picking anchor tracks based on what you've been listening to.
    """
    tracks = get_recent_tracks(days=days, limit=limit)
    return enrich_tracks_with_spotify_data(tracks)


@app.get("/api/tracks/history/search")
def tracks_history_search(q: str, limit: int = 20):
    """
    Search your listening history for tracks.

    Searches track names and artist names.
    """
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")

    tracks = search_user_tracks(q, limit=limit)
    return enrich_tracks_with_spotify_data(tracks)
