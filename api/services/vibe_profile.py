"""
Vibe profile for anchor-based playlist generation.

A VibeProfile captures the "vibe" of selected anchor tracks through:
- Audio feature centroid (energy, valence, tempo, etc.)
- Genre distribution
- Artist relationships
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
import statistics


@dataclass
class VibeProfile:
    """Profile representing the target vibe for a playlist."""

    # Anchor track IDs that define the vibe
    anchor_ids: List[str]

    # Audio feature centroid (average of anchors)
    target_energy: Optional[float] = None
    target_valence: Optional[float] = None
    target_tempo: Optional[float] = None
    target_danceability: Optional[float] = None
    target_acousticness: Optional[float] = None
    target_instrumentalness: Optional[float] = None

    # Genre distribution from anchors
    genres: Dict[str, float] = field(default_factory=dict)  # genre -> weight

    # Artist IDs from anchors (for related artist lookups)
    anchor_artist_ids: Set[str] = field(default_factory=set)

    # Feature availability flag
    has_audio_features: bool = False


def compute_feature_centroid(features: List[Dict]) -> Dict[str, float]:
    """
    Compute the centroid (average) of audio features.

    Args:
        features: List of audio feature dicts from Spotify API

    Returns:
        Dict with averaged feature values
    """
    if not features:
        return {}

    feature_keys = [
        'energy', 'valence', 'tempo', 'danceability',
        'acousticness', 'instrumentalness', 'loudness', 'speechiness'
    ]

    centroid = {}
    for key in feature_keys:
        values = [f.get(key) for f in features if f and f.get(key) is not None]
        if values:
            centroid[key] = statistics.mean(values)

    return centroid


def build_vibe_profile(
    anchor_tracks: List[Dict],
    audio_features: List[Dict],
    artist_genres: Dict[str, List[str]]
) -> VibeProfile:
    """
    Build a VibeProfile from anchor tracks.

    Args:
        anchor_tracks: List of track dicts with id, artists, etc.
        audio_features: Audio features for the anchor tracks
        artist_genres: Dict mapping artist_id -> list of genres

    Returns:
        VibeProfile representing the target vibe
    """
    anchor_ids = [t['id'] for t in anchor_tracks if t.get('id')]

    # Extract artist IDs
    anchor_artist_ids = set()
    for track in anchor_tracks:
        for artist in track.get('artists', []):
            if artist.get('id'):
                anchor_artist_ids.add(artist['id'])

    # Compute feature centroid
    centroid = compute_feature_centroid(audio_features)
    has_features = bool(centroid)

    # Aggregate genres from artists
    genre_counts: Dict[str, int] = {}
    for artist_id, genres in artist_genres.items():
        for genre in genres:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

    # Normalize genre weights
    total = sum(genre_counts.values()) or 1
    genre_weights = {g: c / total for g, c in genre_counts.items()}

    return VibeProfile(
        anchor_ids=anchor_ids,
        target_energy=centroid.get('energy'),
        target_valence=centroid.get('valence'),
        target_tempo=centroid.get('tempo'),
        target_danceability=centroid.get('danceability'),
        target_acousticness=centroid.get('acousticness'),
        target_instrumentalness=centroid.get('instrumentalness'),
        genres=genre_weights,
        anchor_artist_ids=anchor_artist_ids,
        has_audio_features=has_features,
    )


def get_top_genres(profile: VibeProfile, limit: int = 5) -> List[str]:
    """Get the top genres from a vibe profile."""
    sorted_genres = sorted(profile.genres.items(), key=lambda x: x[1], reverse=True)
    return [g for g, _ in sorted_genres[:limit]]


def feature_distance(profile: VibeProfile, track_features: Dict) -> float:
    """
    Compute distance between a track's features and the vibe profile centroid.

    Lower is better (more similar to anchors).
    Returns 0.5 if no features available (neutral score).
    """
    if not profile.has_audio_features or not track_features:
        return 0.5

    distances = []

    # Energy (0-1)
    if profile.target_energy is not None and track_features.get('energy') is not None:
        distances.append(abs(profile.target_energy - track_features['energy']))

    # Valence (0-1)
    if profile.target_valence is not None and track_features.get('valence') is not None:
        distances.append(abs(profile.target_valence - track_features['valence']))

    # Tempo (normalize to 0-1 range, assume 60-180 BPM typical)
    if profile.target_tempo is not None and track_features.get('tempo') is not None:
        tempo_diff = abs(profile.target_tempo - track_features['tempo']) / 120
        distances.append(min(tempo_diff, 1.0))

    # Danceability (0-1)
    if profile.target_danceability is not None and track_features.get('danceability') is not None:
        distances.append(abs(profile.target_danceability - track_features['danceability']))

    # Acousticness (0-1)
    if profile.target_acousticness is not None and track_features.get('acousticness') is not None:
        distances.append(abs(profile.target_acousticness - track_features['acousticness']))

    if not distances:
        return 0.5

    return statistics.mean(distances)
