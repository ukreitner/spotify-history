from typing import List, Dict, Set, Optional
from datetime import datetime, timedelta
import random
from ..db import get_all_tracks_with_counts, get_top_artists, get_top_genres, query_all_dbs
from ..spotify_client import enrich_tracks_with_spotify_data, search_tracks_by_genre, get_audio_features


def score_track_by_features(
    track: Dict,
    features: Dict,
    targets: Dict[str, tuple],
) -> float:
    """
    Score how well a track matches target audio features.
    targets: dict of feature_name -> (min, max) or (target,) for single value
    Returns score 0-1 where 1 is perfect match.
    """
    if not features:
        return 0.5  # Neutral score if no features available

    weights = {
        'energy': 1.0,
        'valence': 1.0,
        'danceability': 0.8,
        'tempo': 0.5,
        'acousticness': 0.7,
    }

    total_weight = 0
    total_score = 0

    for feature, weight in weights.items():
        if feature not in targets:
            continue

        target_range = targets[feature]
        actual = features.get(feature)
        if actual is None:
            continue

        # Normalize tempo to 0-1 range (60-200 BPM)
        if feature == 'tempo':
            actual = max(0, min(1, (actual - 60) / 140))
            target_range = (
                max(0, min(1, (target_range[0] - 60) / 140)),
                max(0, min(1, (target_range[1] - 60) / 140)),
            )

        min_val, max_val = target_range

        # Check if within range
        if min_val <= actual <= max_val:
            score = 1.0
        else:
            # Score based on distance from range
            if actual < min_val:
                distance = min_val - actual
            else:
                distance = actual - max_val
            score = max(0, 1 - distance * 2)  # Penalty for being outside range

        total_weight += weight
        total_score += weight * score

    if total_weight == 0:
        return 0.5

    feature_score = total_score / total_weight

    # Bonus for play count (familiar tracks score slightly higher)
    play_bonus = 0.1 * min(track.get('play_count', 0) / 10, 1)

    return min(1.0, feature_score + play_bonus)


def generate_custom_playlist(
    genres: List[str] = None,
    exclude_genres: List[str] = None,
    min_plays: int = 1,
    max_days: int = 365,
    discovery_ratio: int = 30,
    artist_filter: str = "all",
    limit: int = 30,
    # Audio feature filters (0-100 scale, None means no filter)
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
    exclude_artists: List[str] = None,
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
        energy_min/max: Energy level filter (0-100)
        valence_min/max: Mood filter (0=sad, 100=happy)
        danceability_min/max: Danceability filter (0-100)
        tempo_min/max: Tempo in BPM
        acousticness_min/max: Acoustic vs electronic (0-100)
        year_min/max: Release year filter
        exclude_artists: List of artist names to exclude
    """
    genres = genres or []
    exclude_genres = exclude_genres or []
    exclude_artists = exclude_artists or []
    genres_lower = {g.lower() for g in genres}
    exclude_lower = {g.lower() for g in exclude_genres}
    exclude_artists_lower = {a.lower() for a in exclude_artists}

    # Build audio feature targets dict
    feature_targets: Dict[str, tuple] = {}
    if energy_min is not None or energy_max is not None:
        feature_targets['energy'] = (
            (energy_min or 0) / 100,
            (energy_max or 100) / 100,
        )
    if valence_min is not None or valence_max is not None:
        feature_targets['valence'] = (
            (valence_min or 0) / 100,
            (valence_max or 100) / 100,
        )
    if danceability_min is not None or danceability_max is not None:
        feature_targets['danceability'] = (
            (danceability_min or 0) / 100,
            (danceability_max or 100) / 100,
        )
    if tempo_min is not None or tempo_max is not None:
        feature_targets['tempo'] = (
            tempo_min or 60,
            tempo_max or 200,
        )
    if acousticness_min is not None or acousticness_max is not None:
        feature_targets['acousticness'] = (
            (acousticness_min or 0) / 100,
            (acousticness_max or 100) / 100,
        )

    use_audio_features = bool(feature_targets)
    
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

            # Check excluded artists
            first_artist = artist_lower.split(",")[0].strip()
            if first_artist in exclude_artists_lower:
                continue

            # For diverse mode, track artist counts
            if artist_filter == "diverse":
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

        # If using audio features, fetch them and score/filter
        if use_audio_features and candidates:
            track_ids = [c["track_id"] for c in candidates if c.get("track_id")]
            audio_features = get_audio_features(track_ids)
            features_map = {f["id"]: f for f in audio_features if f}

            # Score and filter candidates
            scored_candidates = []
            for c in candidates:
                tid = c.get("track_id")
                features = features_map.get(tid, {})
                score = score_track_by_features(c, features, feature_targets)

                # Add features to track data for frontend display
                c["energy"] = features.get("energy")
                c["valence"] = features.get("valence")
                c["danceability"] = features.get("danceability")
                c["tempo"] = features.get("tempo")
                c["acousticness"] = features.get("acousticness")
                c["score"] = score

                # Only include tracks that score above threshold
                if score >= 0.3:
                    scored_candidates.append(c)

            # Sort by score (best matches first)
            scored_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            result.extend(scored_candidates[:history_count])
        else:
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
        # Add explicitly excluded artists
        existing_artists.update(exclude_artists_lower)

        discovered = []
        discovery_candidates = []
        genres_to_search = search_genres[:8] if search_genres else ["pop", "rock", "indie", "electronic"]

        for genre in genres_to_search:
            if len(discovery_candidates) >= discovery_count * 3:  # Get extra for filtering
                break

            # Check if genre should be excluded
            if any(ex in genre.lower() or genre.lower() in ex for ex in exclude_lower):
                continue

            search_results = search_tracks_by_genre(genre, limit=30)
            random.shuffle(search_results)  # Add variety

            for track in search_results:
                if len(discovery_candidates) >= discovery_count * 3:
                    break

                track_id = track.get("id")
                if not track_id or track_id in existing_ids:
                    continue

                # Skip artists we already have or excluded
                track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                if any(a.split(",")[0].strip() in existing_artists for a in track_artists):
                    continue

                album = track.get("album", {})
                images = album.get("images", [])

                discovery_candidates.append({
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

        # If using audio features, filter discovery tracks too
        if use_audio_features and discovery_candidates:
            discovery_ids = [c["track_id"] for c in discovery_candidates]
            discovery_features = get_audio_features(discovery_ids)
            discovery_features_map = {f["id"]: f for f in discovery_features if f}

            for c in discovery_candidates:
                tid = c.get("track_id")
                features = discovery_features_map.get(tid, {})
                score = score_track_by_features(c, features, feature_targets)

                # Add features to track data
                c["energy"] = features.get("energy")
                c["valence"] = features.get("valence")
                c["danceability"] = features.get("danceability")
                c["tempo"] = features.get("tempo")
                c["acousticness"] = features.get("acousticness")
                c["score"] = score

                if score >= 0.3:
                    discovered.append(c)

            # Sort by score
            discovered.sort(key=lambda x: x.get("score", 0), reverse=True)
            result.extend(discovered[:discovery_count])
        else:
            result.extend(discovery_candidates[:discovery_count])
    
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
