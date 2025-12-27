"""
Coherence scoring for playlist tracks.

Scores how well a candidate track fits with the vibe profile.
Higher scores = better fit.
"""

from typing import Dict, Set, Optional
from .vibe_profile import VibeProfile, feature_distance


# Scoring weights (from plan)
WEIGHT_FEATURE_SIMILARITY = 0.35
WEIGHT_GENRE_MATCH = 0.25
WEIGHT_ARTIST_RELATIONSHIP = 0.15
WEIGHT_RECENCY_BONUS = 0.10
WEIGHT_POPULARITY_BALANCE = 0.10
WEIGHT_DIVERSITY_PENALTY = 0.05


def score_feature_similarity(
    profile: VibeProfile,
    track_features: Optional[Dict]
) -> float:
    """
    Score based on audio feature similarity to vibe centroid.

    Returns 0-1, higher = more similar.
    """
    if not track_features:
        return 0.5  # Neutral if no features

    distance = feature_distance(profile, track_features)
    # Convert distance to similarity (invert)
    return max(0, 1 - distance)


def score_genre_match(
    profile: VibeProfile,
    track_genres: Set[str]
) -> float:
    """
    Score based on genre overlap with vibe profile.

    Uses weighted Jaccard-like similarity.
    Returns 0-1, higher = better match.
    """
    if not profile.genres:
        return 0.5  # Neutral if no vibe genres defined

    if not track_genres:
        return 0.2  # Penalize tracks with no genre info

    # Sum weights of matching genres
    match_score = 0.0
    for genre in track_genres:
        genre_lower = genre.lower()
        # Check for exact match or partial match
        if genre_lower in profile.genres:
            match_score += profile.genres[genre_lower]
        else:
            # Partial matching (e.g., "indie rock" matches "rock")
            for profile_genre, weight in profile.genres.items():
                if genre_lower in profile_genre or profile_genre in genre_lower:
                    match_score += weight * 0.5
                    break

    # If no matches at all, return low score
    if match_score == 0:
        return 0.1

    # Normalize to 0-1
    return min(1.0, match_score)


def score_artist_relationship(
    profile: VibeProfile,
    track_artist_ids: Set[str],
    related_artists_map: Dict[str, Set[str]]
) -> float:
    """
    Score based on artist relationship to anchor artists.

    - Same artist as anchor: 1.0
    - Related to anchor (1 hop): 0.7
    - Related to anchor (2 hops): 0.4
    - No relationship: 0.1 (penalize unrelated)

    Returns 0-1.
    """
    if not profile.anchor_artist_ids or not track_artist_ids:
        return 0.3  # Lower neutral score

    best_score = 0.1  # Default: no relationship (penalize)

    for track_artist in track_artist_ids:
        # Check if same artist as anchor
        if track_artist in profile.anchor_artist_ids:
            return 1.0  # Max score

        # Check 1-hop relationship
        for anchor_artist in profile.anchor_artist_ids:
            related = related_artists_map.get(anchor_artist, set())
            if track_artist in related:
                best_score = max(best_score, 0.7)
                continue

            # Check 2-hop relationship
            for related_artist in related:
                second_hop = related_artists_map.get(related_artist, set())
                if track_artist in second_hop:
                    best_score = max(best_score, 0.4)
                    break

    return best_score


def score_recency_bonus(
    track_id: str,
    recent_track_plays: Dict[str, int],
    max_plays: int = 10
) -> float:
    """
    Score bonus for recently played tracks.

    Returns 0-1, higher = more recently/frequently played.
    """
    if not recent_track_plays:
        return 0.5

    plays = recent_track_plays.get(track_id, 0)
    if plays == 0:
        return 0.3  # Slight bonus for discovery (not in recent plays)

    # Normalize by max plays, cap at 1.0
    return min(1.0, 0.5 + (plays / max_plays) * 0.5)


def score_popularity_balance(popularity: int) -> float:
    """
    Score that prefers hidden gems over mega-popular tracks.

    Popularity 0-100 from Spotify.
    Sweet spot: 30-60 (known but not overplayed)

    Returns 0-1.
    """
    if popularity is None:
        return 0.5

    # Bell curve centered around 45
    # Very popular (>80) or very obscure (<10) get lower scores
    if 30 <= popularity <= 60:
        return 1.0
    elif 20 <= popularity < 30 or 60 < popularity <= 70:
        return 0.8
    elif 10 <= popularity < 20 or 70 < popularity <= 80:
        return 0.6
    elif popularity < 10:
        return 0.4  # Too obscure might be low quality
    else:
        return 0.3  # Too popular / overplayed


def score_diversity_penalty(
    track_artist: str,
    selected_artists: Dict[str, int],
    max_per_artist: int = 3
) -> float:
    """
    Penalty for having too many tracks from same artist.

    Returns 0-1, lower = artist already well-represented.
    """
    if not selected_artists:
        return 1.0

    current_count = selected_artists.get(track_artist, 0)
    if current_count >= max_per_artist:
        return 0.0
    elif current_count == max_per_artist - 1:
        return 0.3
    elif current_count >= 1:
        return 0.6

    return 1.0


def compute_total_coherence(
    profile: VibeProfile,
    track: Dict,
    track_features: Optional[Dict],
    track_genres: Set[str],
    track_artist_ids: Set[str],
    related_artists_map: Dict[str, Set[str]],
    recent_track_plays: Dict[str, int],
    selected_artists: Dict[str, int],
) -> float:
    """
    Compute total coherence score for a candidate track.

    Returns weighted sum of all scoring components.
    Higher = better fit for playlist.
    """
    feature_score = score_feature_similarity(profile, track_features)
    genre_score = score_genre_match(profile, track_genres)
    artist_score = score_artist_relationship(
        profile, track_artist_ids, related_artists_map
    )
    recency_score = score_recency_bonus(
        track.get('id', ''),
        recent_track_plays
    )
    popularity_score = score_popularity_balance(track.get('popularity'))

    # Primary artist for diversity check
    primary_artist = ''
    if track.get('artists'):
        primary_artist = track['artists'][0].get('name', '')
    diversity_score = score_diversity_penalty(primary_artist, selected_artists)

    total = (
        WEIGHT_FEATURE_SIMILARITY * feature_score +
        WEIGHT_GENRE_MATCH * genre_score +
        WEIGHT_ARTIST_RELATIONSHIP * artist_score +
        WEIGHT_RECENCY_BONUS * recency_score +
        WEIGHT_POPULARITY_BALANCE * popularity_score +
        WEIGHT_DIVERSITY_PENALTY * diversity_score
    )

    return total


def get_coherence_breakdown(
    profile: VibeProfile,
    track: Dict,
    track_features: Optional[Dict],
    track_genres: Set[str],
    track_artist_ids: Set[str],
    related_artists_map: Dict[str, Set[str]],
    recent_track_plays: Dict[str, int],
    selected_artists: Dict[str, int],
) -> Dict[str, float]:
    """
    Get breakdown of coherence scores for debugging/display.

    Returns dict with individual component scores.
    """
    return {
        'feature_similarity': score_feature_similarity(profile, track_features),
        'genre_match': score_genre_match(profile, track_genres),
        'artist_relationship': score_artist_relationship(
            profile, track_artist_ids, related_artists_map
        ),
        'recency_bonus': score_recency_bonus(
            track.get('id', ''),
            recent_track_plays
        ),
        'popularity_balance': score_popularity_balance(track.get('popularity')),
        'diversity_penalty': score_diversity_penalty(
            track['artists'][0].get('name', '') if track.get('artists') else '',
            selected_artists
        ),
    }
