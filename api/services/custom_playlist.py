from typing import List, Dict, Set, Optional, Literal, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import random
import math
import re
import time
import unicodedata
from ..db import (
    get_all_tracks_with_counts, get_top_artists, get_top_genres, query_all_dbs,
    get_top_tracks, get_recent_listening, search_user_tracks
)
from ..spotify_client import (
    enrich_tracks_with_spotify_data, search_tracks_by_genre, get_audio_features,
    get_recommendations, get_artist_related, get_artist_top_tracks, search_artist,
    get_artist_albums, get_album_tracks, get_tracks_bulk, get_artists_bulk,
    search_tracks_advanced,
)
from ..lastfm_client import get_similar_artists, get_similar_tracks_batch
from .vibe_profile import build_vibe_profile, VibeProfile, get_top_genres as vibe_top_genres
from .coherence import compute_total_coherence, get_coherence_breakdown, score_popularity_balance
from .flow_ordering import order_playlist, FlowMode


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

    # Get all tracks from history (needed for both history selection and discovery filtering)
    all_tracks = get_all_tracks_with_counts("music")

    # === PART 1: Get tracks from listening history ===
    if history_count > 0:
        
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
        # Use ALL track IDs from user's history to avoid suggesting songs they've heard
        existing_ids = set(all_tracks.keys())
        existing_ids.update(t["track_id"] for t in result if t.get("track_id"))

        # Track artists - known artists from history
        known_artists = {t["artist"].lower().split(",")[0].strip() for t in all_tracks.values()}
        playlist_artists = {t["artist"].lower().split(",")[0].strip() for t in result}
        excluded = set(exclude_artists_lower)

        discovery_candidates = []

        def add_track(track: Dict, source: str, popularity_boost: int = 0) -> bool:
            """Try to add a track. Returns True if added."""
            track_id = track.get("id")
            if not track_id or track_id in existing_ids:
                return False

            track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
            first_artist = track_artists[0].split(",")[0].strip() if track_artists else ""

            # Skip explicitly excluded artists
            if first_artist in excluded:
                return False

            # Skip if we already have 2 tracks from this artist in the playlist
            artist_count = sum(1 for c in discovery_candidates if c.get("_artist_key") == first_artist)
            if artist_count >= 2:
                return False

            album = track.get("album", {})
            images = album.get("images", [])
            popularity = track.get("popularity", 50)

            discovery_candidates.append({
                "track_id": track_id,
                "track": track.get("name", "Unknown"),
                "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                "play_count": 0,
                "image_url": images[0]["url"] if images else None,
                "preview_url": track.get("preview_url"),
                "spotify_url": track.get("external_urls", {}).get("spotify"),
                "source": "discovery",
                "discovered_via": source,
                "popularity": popularity,
                "_artist_key": first_artist,
                "_is_new_artist": first_artist not in known_artists,
            })
            existing_ids.add(track_id)
            return True

        # === ANALYZE RECENT LISTENING ===
        recent = get_recent_listening(days=30, content_type="music")
        recent_artists = [a["artist"] for a in recent["artists"][:15]]
        recent_tracks = [t["track_id"] for t in recent["tracks"][:10] if t.get("track_id")]
        recent_genres = [g["genre"] for g in recent["genres"][:10]]

        # Get Spotify IDs for recent artists
        artist_id_map = {}  # name -> id
        for artist_name in recent_artists[:10]:
            artist_info = search_artist(artist_name)
            if artist_info and artist_info.get("id"):
                artist_id_map[artist_name] = artist_info["id"]

        recent_artist_ids = list(artist_id_map.values())

        # === STRATEGY 1: Deep recommendations from recent listening ===
        # Use recent tracks as seeds (what you're into NOW)
        if recent_tracks:
            for i in range(0, min(len(recent_tracks), 10), 5):
                if len(discovery_candidates) >= discovery_count:
                    break
                seeds = recent_tracks[i:i+5]
                recs = get_recommendations(seed_tracks=seeds, limit=100)
                # Prefer less popular tracks
                recs.sort(key=lambda t: t.get("popularity", 50))
                for track in recs:
                    if len(discovery_candidates) >= discovery_count:
                        break
                    # Skip very popular tracks (top 40 stuff)
                    if track.get("popularity", 0) > 70:
                        continue
                    add_track(track, "based on recent plays")

        # === STRATEGY 2: Deep dive into related artists (2-3 hops) ===
        if len(discovery_candidates) < discovery_count and recent_artist_ids:
            explored = set()
            queue = [(aid, 0, name) for name, aid in list(artist_id_map.items())[:5]]  # (id, depth, seed_name)

            while queue and len(discovery_candidates) < discovery_count:
                artist_id, depth, seed_name = queue.pop(0)

                if artist_id in explored or depth > 2:
                    continue
                explored.add(artist_id)

                # Get related artists
                related = get_artist_related(artist_id)
                random.shuffle(related)

                for rel in related[:6]:
                    rel_id = rel.get("id")
                    rel_name = rel.get("name", "")

                    if not rel_id or rel_id in explored:
                        continue

                    # Add to queue for deeper exploration
                    if depth < 2:
                        queue.append((rel_id, depth + 1, seed_name))

                    # Skip artists you already know well
                    if rel_name.lower() in known_artists:
                        continue

                    # Get album tracks (not just top tracks - deeper cuts!)
                    albums = get_artist_albums(rel_id, limit=3)
                    for album in albums:
                        if len(discovery_candidates) >= discovery_count:
                            break

                        album_tracks = get_album_tracks(album.get("id"))
                        # Sort by popularity ascending (find the hidden gems)
                        album_tracks.sort(key=lambda t: t.get("popularity", 50) if t else 100)

                        for track in album_tracks[:4]:  # Take up to 4 deep cuts per album
                            if not track or len(discovery_candidates) >= discovery_count:
                                break
                            # Prefer tracks that aren't the obvious singles
                            if track.get("popularity", 0) > 60:
                                continue
                            add_track(track, f"deep cut · {rel_name} (via {seed_name})")

                    # Also get some top tracks as fallback
                    if len(discovery_candidates) < discovery_count:
                        top = get_artist_top_tracks(rel_id)
                        for track in top[:2]:
                            if len(discovery_candidates) >= discovery_count:
                                break
                            add_track(track, f"similar to {seed_name}")

        # === STRATEGY 3: Genre-based discovery with low popularity filter ===
        if len(discovery_candidates) < discovery_count:
            search_genres = list(genres_lower) if genres_lower else recent_genres[:5]
            if not search_genres:
                search_genres = ["indie", "alternative", "folk", "electronic"]

            for genre in search_genres:
                if len(discovery_candidates) >= discovery_count:
                    break
                if any(ex in genre.lower() for ex in exclude_lower):
                    continue

                # Search with year filter for fresh music
                tracks = search_tracks_by_genre(genre, limit=50)
                # Sort by popularity to find hidden gems
                tracks.sort(key=lambda t: t.get("popularity", 50))

                for track in tracks:
                    if len(discovery_candidates) >= discovery_count:
                        break
                    if track.get("popularity", 0) > 50:  # Only low-popularity tracks
                        continue
                    add_track(track, f"hidden gem · {genre}")

        # === Sort final results: prioritize new artists + lower popularity ===
        discovery_candidates.sort(
            key=lambda t: (
                0 if t.get("_is_new_artist") else 1,  # New artists first
                t.get("popularity", 50)  # Then by popularity (lower = better)
            )
        )

        # Take the best ones
        discovery_candidates = discovery_candidates[:discovery_count]

        # Clean up internal keys
        for c in discovery_candidates:
            c.pop("_artist_key", None)
            c.pop("_is_new_artist", None)

        # Add audio features if requested
        if use_audio_features and discovery_candidates:
            discovery_ids = [c["track_id"] for c in discovery_candidates]
            discovery_features = get_audio_features(discovery_ids)
            discovery_features_map = {f["id"]: f for f in discovery_features if f}

            for c in discovery_candidates:
                features = discovery_features_map.get(c.get("track_id"), {})
                c["energy"] = features.get("energy")
                c["valence"] = features.get("valence")
                c["danceability"] = features.get("danceability")
                c["tempo"] = features.get("tempo")
                c["acousticness"] = features.get("acousticness")

        result.extend(discovery_candidates)
    
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


def _normalize_music_text(value: str) -> str:
    """Normalize artist/title metadata for cross-catalog exact matching."""
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def _primary_artist_name(value: str) -> str:
    """Return the first credited artist from the archive's display string."""
    return (value or "").split(",", 1)[0].strip()


def _track_key(artist: str, title: str) -> Tuple[str, str]:
    return (_normalize_music_text(artist), _normalize_music_text(title))


def _is_explicit_low_value_variant(title: str) -> bool:
    """Identify narrow, explicit catalog variants from title qualifiers.

    This intentionally does not guess whether an ordinary recording is a
    cover.  It only rejects discovery titles that label themselves as the
    common low-value speed/karaoke/tribute variants.
    """
    raw = (title or "").replace("–", "-").replace("—", "-")
    text = unicodedata.normalize("NFKD", raw).encode(
        "ascii", "ignore"
    ).decode().casefold()
    return bool(re.search(
        r"(?:[-–—]|\(|\[)\s*(?:"
        r"sped\s*up(?:\s+version)?|"
        r"slowed(?:\s+down)?(?:\s*(?:and|\+)\s*reverb)?|"
        r"nightcore(?:\s+version)?|"
        r"karaoke(?:\s+version)?|"
        r"(?:a\s+)?tribute(?:\s+to)?"
        r")\b",
        text,
    ))


def _spotify_track_matches(track: Dict, artist: str, title: str) -> bool:
    """Require the requested artist and title instead of trusting search rank."""
    artist_key, title_key = _track_key(artist, title)
    spotify_artists = {
        _normalize_music_text(item.get("name", ""))
        for item in track.get("artists", [])
    }
    spotify_title = _normalize_music_text(track.get("name", ""))
    if artist_key not in spotify_artists:
        return False
    if spotify_title == title_key:
        return True

    # Catalog titles often add a remaster/live suffix. Only accept a prefix
    # relation when the meaningful title is long enough to avoid loose matches.
    return len(title_key) >= 8 and (
        spotify_title.startswith(title_key) or title_key.startswith(spotify_title)
    )


@lru_cache(maxsize=1000)
def _resolve_spotify_track(artist: str, title: str) -> Optional[Dict]:
    """Resolve one Last.fm artist/title pair to the matching Spotify track."""
    safe_artist = (artist or "").replace('"', " ").strip()
    safe_title = (title or "").replace('"', " ").strip()
    if not safe_artist or not safe_title:
        return None
    results = search_tracks_advanced(
        f'track:"{safe_title}" artist:"{safe_artist}"',
        limit=8,
        market="CH",
    )
    exact = [
        track for track in results
        if track and _spotify_track_matches(track, safe_artist, safe_title)
    ]
    if not exact:
        return None
    exact.sort(
        key=lambda track: (
            _normalize_music_text(track.get("name", ""))
            != _normalize_music_text(safe_title),
            -int(track.get("popularity", 0) or 0),
        )
    )
    return exact[0]


def _artist_names(track: Dict) -> List[str]:
    return [
        artist.get("name", "").strip()
        for artist in track.get("artists", [])
        if artist.get("name")
    ]


def _candidate_artist_key(candidate: Dict) -> str:
    names = _artist_names(candidate.get("track", {}))
    return _normalize_music_text(names[0] if names else "")


def _candidate_artist_keys(candidate: Dict) -> Tuple[str, ...]:
    """Return every credited artist once, in Spotify credit order."""
    return tuple(dict.fromkeys(
        key
        for key in (
            _normalize_music_text(name)
            for name in _artist_names(candidate.get("track", {}))
        )
        if key
    ))


_EVIDENCE_PRIORITY = {
    "artist": 1,
    "anchor_artist": 2,
    "track": 3,
    "anchor": 4,
}


def _calibrated_relation(
    evidence_type: str,
    rank: int = 0,
    total: int = 1,
    raw_match: float = 0.0,
) -> float:
    """Put heterogeneous Last.fm evidence on a useful 0-1 scale.

    Strong direct track matches sit above artist-neighbour fallbacks, while
    weak raw matches are allowed to fall below them.  This keeps Last.fm's
    occasional low-confidence plateaus from masquerading as authoritative
    evidence and makes the public coherence threshold meaningful.
    """
    if evidence_type == "anchor":
        return 1.0
    if evidence_type == "anchor_artist":
        return 0.82

    denominator = max(total - 1, 1)
    rank_score = max(0.0, min(1.0, 1 - rank / denominator))
    match_score = max(0.0, min(1.0, float(raw_match or 0.0)))
    if evidence_type == "track":
        # Last.fm sometimes returns one strong neighbour followed by a long,
        # numerically identical low-confidence plateau.  A large fixed floor
        # made every item on that plateau look authoritative (and rank then
        # dominated the actual match value).  Keep excellent direct matches
        # excellent, while requiring the raw relation to do most of the work.
        return min(1.0, 0.20 + 0.70 * match_score + 0.10 * rank_score)
    # Artist-neighbour evidence is broader than a strong track edge, but it is
    # still a real Last.fm relationship.  Let roughly the well-ranked half of
    # a healthy artist graph clear the default .50 strictness; the previous
    # .58 ceiling admitted only the first few artists, which were commonly
    # already exhausted by familiar-track caps.
    return min(0.70, 0.38 + 0.20 * rank_score + 0.12 * match_score)


def _calibrated_affinity(
    relation: float,
    popularity: Optional[int],
    play_count: int = 0,
) -> float:
    familiarity = min(1.0, math.log1p(max(play_count, 0)) / math.log(12))
    popularity_score = score_popularity_balance(popularity)
    return max(
        0.0,
        min(1.0, 0.90 * relation + 0.06 * familiarity + 0.04 * popularity_score),
    )


def _generate_vibe_playlist_legacy(
    anchor_track_ids: List[str],
    track_count: int = 30,
    discovery_ratio: int = 50,
    flow_mode: FlowMode = "smooth",
    exclude_artists: List[str] = None,
    coherence_threshold: float = 0.50,
    max_per_anchor_artist: int = 3,
    max_per_similar_artist: int = 2,
) -> Dict:
    """
    Generate a coherent playlist based on anchor tracks.

    Args:
        anchor_track_ids: 1-5 track IDs that define the vibe
        track_count: Target number of tracks (10-100)
        discovery_ratio: Percentage of new music (0-100)
        flow_mode: "smooth", "energy_arc", or "shuffle"
        exclude_artists: Artists to exclude
        coherence_threshold: Minimum coherence score (0-1)
        max_per_anchor_artist: Max tracks per anchor artist in discovery
        max_per_similar_artist: Max tracks per similar artist in discovery

    Returns:
        Dict with:
        - tracks: ordered list of tracks
        - vibe_profile: the computed vibe profile
        - flow_stats: transition quality stats
    """
    exclude_artists = exclude_artists or []
    exclude_lower = {a.lower() for a in exclude_artists}

    # Use provided parameters
    MIN_COHERENCE_THRESHOLD = coherence_threshold
    MAX_PER_ANCHOR_ARTIST = max_per_anchor_artist
    MAX_PER_DISCOVERED_ARTIST = max_per_similar_artist

    # Validate anchor tracks
    if not anchor_track_ids or len(anchor_track_ids) > 5:
        raise ValueError("Need 1-5 anchor tracks")

    # === STEP 1: Build vibe profile from anchors ===
    anchor_tracks = get_tracks_bulk(anchor_track_ids)
    if not anchor_tracks:
        raise ValueError("Could not fetch anchor tracks")

    # Get audio features for anchors
    anchor_features = get_audio_features(anchor_track_ids)
    anchor_features_map = {f["id"]: f for f in anchor_features if f}

    # Get artist info for genre data
    anchor_artist_ids = set()
    for track in anchor_tracks:
        for artist in track.get("artists", []):
            if artist.get("id"):
                anchor_artist_ids.add(artist["id"])

    artists_data = get_artists_bulk(list(anchor_artist_ids))
    artist_genres = {a["id"]: a.get("genres", []) for a in artists_data if a}

    profile = build_vibe_profile(anchor_tracks, anchor_features, artist_genres)

    # === STEP 2: Generate candidate pool ===
    discovery_count = int(track_count * discovery_ratio / 100)
    history_count = track_count - discovery_count

    all_history = get_all_tracks_with_counts("music")
    existing_ids = set(anchor_track_ids)

    # Build related artists map for scoring
    related_artists_map: Dict[str, Set[str]] = {}
    for artist_id in list(profile.anchor_artist_ids)[:5]:
        related = get_artist_related(artist_id)
        related_artists_map[artist_id] = {r["id"] for r in related if r.get("id")}

    # Recent listening for recency scoring
    recent = get_recent_listening(days=30, content_type="music")
    recent_track_plays = {t["track_id"]: t["play_count"] for t in recent["tracks"] if t.get("track_id")}
    max_recent_plays = max(recent_track_plays.values()) if recent_track_plays else 10

    candidates = []

    # === HISTORY CANDIDATES ===
    if history_count > 0:
        history_ids = [tid for tid in all_history.keys() if tid and tid not in existing_ids]

        # Get full track data from Spotify
        history_spotify = get_tracks_bulk(history_ids[:500])  # Limit for API
        history_features = get_audio_features(history_ids[:500])
        history_features_map = {f["id"]: f for f in history_features if f}

        # Get genre info for history tracks
        history_artist_ids = set()
        for t in history_spotify:
            for a in t.get("artists", []):
                if a.get("id"):
                    history_artist_ids.add(a["id"])

        history_artists = get_artists_bulk(list(history_artist_ids)[:200])
        history_artist_genres = {a["id"]: set(a.get("genres", [])) for a in history_artists if a}

        # Get anchor artist names for boosting similar tracks
        anchor_artist_names = set()
        for track in anchor_tracks:
            for artist in track.get("artists", []):
                anchor_artist_names.add(artist.get("name", "").lower())

        for track in history_spotify:
            tid = track.get("id")
            if not tid or tid in existing_ids:
                continue

            # Check excluded artists
            track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
            if any(a in exclude_lower for a in track_artists):
                continue

            # Get track data
            track_artist_ids = {a.get("id") for a in track.get("artists", []) if a.get("id")}
            track_genres = set()
            for aid in track_artist_ids:
                track_genres.update(history_artist_genres.get(aid, set()))

            features = history_features_map.get(tid, {})

            # Check for vibe relevance - require SOME connection
            same_artist = any(a in anchor_artist_names for a in track_artists)

            # Check for shared genres (partial match counts)
            profile_genres_lower = {g.lower() for g in profile.genres.keys()}
            shared_genres = set()
            for tg in track_genres:
                tg_lower = tg.lower()
                if tg_lower in profile_genres_lower:
                    shared_genres.add(tg)
                else:
                    # Partial match
                    for pg in profile_genres_lower:
                        if pg in tg_lower or tg_lower in pg:
                            shared_genres.add(tg)
                            break

            has_genre_overlap = len(shared_genres) > 0

            # REQUIRE at least one connection: same artist OR genre overlap
            # This prevents random high-play-count tracks from sneaking in
            if not same_artist and not has_genre_overlap:
                continue

            # Boost for same artist as anchor
            same_artist_boost = 1.0 if same_artist else 0.0

            # Boost for shared genres with anchor
            genre_boost = len(shared_genres) * 0.3

            candidates.append({
                "track": track,
                "features": features,
                "genres": track_genres,
                "artist_ids": track_artist_ids,
                "source": "history",
                "play_count": all_history.get(tid, {}).get("play_count", 0),
                "_anchor_boost": same_artist_boost + genre_boost,
            })

    # === DISCOVERY CANDIDATES ===
    if discovery_count > 0:
        known_artist_names = {t["artist"].lower().split(",")[0].strip() for t in all_history.values()}
        top_vibe_genres = vibe_top_genres(profile, limit=5)

        # Get anchor artist names for matching
        anchor_artist_names = set()
        for track in anchor_tracks:
            for artist in track.get("artists", []):
                anchor_artist_names.add(artist.get("name", "").lower())

        # Strategy 1: Deep cuts from anchor artists (LIMITED - we want NEW artists too)
        anchor_artist_track_count = {}  # Track how many we add per anchor artist

        for anchor_artist_id in list(profile.anchor_artist_ids)[:5]:
            # Get albums
            albums = get_artist_albums(anchor_artist_id, limit=3)
            for album in albums:
                album_tracks = get_album_tracks(album.get("id"))
                # Sort by popularity to find hidden gems
                album_tracks.sort(key=lambda t: t.get("popularity", 50) if t else 100)
                for track in album_tracks[:3]:  # Deep cuts from each album
                    if not track:
                        continue
                    tid = track.get("id")
                    if not tid or tid in existing_ids:
                        continue

                    # Limit tracks per anchor artist
                    if anchor_artist_track_count.get(anchor_artist_id, 0) >= MAX_PER_ANCHOR_ARTIST:
                        break

                    track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                    if any(a in exclude_lower for a in track_artists):
                        continue

                    existing_ids.add(tid)
                    anchor_artist_track_count[anchor_artist_id] = anchor_artist_track_count.get(anchor_artist_id, 0) + 1
                    artist_name = track.get("artists", [{}])[0].get("name", "Unknown")
                    candidates.append({
                        "track": track,
                        "features": {},
                        "genres": set(top_vibe_genres),  # Inherit anchor genres
                        "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                        "source": "discovery",
                        "via": f"deep cut · {artist_name}",
                    })

        # Strategy 2: Similar artists via Last.fm (Spotify API is restricted)
        discovered_artist_count = {}  # Limit tracks per discovered artist

        for anchor_name in list(anchor_artist_names)[:3]:
            # Get similar artists from Last.fm
            similar = get_similar_artists(anchor_name.title(), limit=15)

            for sim_artist in similar:
                sim_name = sim_artist.get("name", "")
                if not sim_name:
                    continue

                # Skip if already at limit for this artist
                if discovered_artist_count.get(sim_name.lower(), 0) >= MAX_PER_DISCOVERED_ARTIST:
                    continue

                # Skip if it's an anchor artist or excluded
                if sim_name.lower() in anchor_artist_names or sim_name.lower() in exclude_lower:
                    continue

                # Find this artist on Spotify
                spotify_artist = search_artist(sim_name)
                if not spotify_artist:
                    continue

                artist_id = spotify_artist.get("id")
                if not artist_id:
                    continue

                # Get top tracks from this similar artist
                top_tracks = get_artist_top_tracks(artist_id)
                for track in top_tracks[:3]:
                    if not track:
                        continue
                    tid = track.get("id")
                    if not tid or tid in existing_ids:
                        continue

                    # Check limit
                    if discovered_artist_count.get(sim_name.lower(), 0) >= MAX_PER_DISCOVERED_ARTIST:
                        break

                    track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                    if any(a in exclude_lower for a in track_artists):
                        continue

                    existing_ids.add(tid)
                    discovered_artist_count[sim_name.lower()] = discovered_artist_count.get(sim_name.lower(), 0) + 1

                    candidates.append({
                        "track": track,
                        "features": {},
                        "genres": set(top_vibe_genres),  # Inherit vibe genres
                        "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                        "source": "discovery",
                        "via": f"similar to {anchor_name.title()}",
                    })

        # NOTE: Removed generic genre search - it finds unrelated tracks
        # Discovery now relies on: anchor artist deep cuts + related artists

        # Fetch audio features for discovery candidates without them
        discovery_without_features = [c for c in candidates if c["source"] == "discovery" and not c["features"]]
        if discovery_without_features:
            disc_ids = [c["track"]["id"] for c in discovery_without_features[:200]]
            disc_features = get_audio_features(disc_ids)
            disc_features_map = {f["id"]: f for f in disc_features if f}
            for c in discovery_without_features:
                c["features"] = disc_features_map.get(c["track"]["id"], {})

    # === STEP 3: Score all candidates for coherence ===
    selected_artists: Dict[str, int] = {}

    for candidate in candidates:
        score = compute_total_coherence(
            profile=profile,
            track=candidate["track"],
            track_features=candidate.get("features"),
            track_genres=candidate.get("genres", set()),
            track_artist_ids=candidate.get("artist_ids", set()),
            related_artists_map=related_artists_map,
            recent_track_plays=recent_track_plays,
            selected_artists=selected_artists,
        )
        # Add anchor boost for history tracks (same artist/genre as anchor)
        anchor_boost = candidate.get("_anchor_boost", 0)
        score += anchor_boost * 0.3  # Weight the boost
        candidate["coherence_score"] = score

    # Filter out low-scoring candidates (unrelated tracks)
    candidates = [c for c in candidates if c["coherence_score"] >= MIN_COHERENCE_THRESHOLD]

    # Sort by coherence score
    candidates.sort(key=lambda c: c["coherence_score"], reverse=True)

    # === STEP 4: Select balanced set ===
    selected = []
    history_selected = 0
    discovery_selected = 0

    for candidate in candidates:
        if len(selected) >= track_count:
            break

        is_history = candidate["source"] == "history"

        # Enforce ratios
        if is_history and history_selected >= history_count:
            continue
        if not is_history and discovery_selected >= discovery_count:
            continue

        # Check artist diversity
        track = candidate["track"]
        artist_name = track.get("artists", [{}])[0].get("name", "")
        if selected_artists.get(artist_name, 0) >= 3:
            continue

        selected.append(candidate)
        selected_artists[artist_name] = selected_artists.get(artist_name, 0) + 1

        if is_history:
            history_selected += 1
        else:
            discovery_selected += 1

    # === STEP 5: Order for flow ===
    selected_tracks = [c["track"] for c in selected]
    features_map = {c["track"]["id"]: c.get("features", {}) for c in selected}
    genres_map = {c["track"]["id"]: c.get("genres", set()) for c in selected}

    ordered_tracks = order_playlist(
        tracks=selected_tracks,
        features_map=features_map,
        genres_map=genres_map,
        flow_mode=flow_mode,
    )

    # === STEP 6: Format output ===
    # Build map from candidate data
    candidate_map = {c["track"]["id"]: c for c in selected}

    result_tracks = []
    for track in ordered_tracks:
        tid = track.get("id")
        candidate = candidate_map.get(tid, {})
        features = candidate.get("features", {})
        album = track.get("album", {})
        images = album.get("images", [])

        result_tracks.append({
            "track_id": tid,
            "track": track.get("name"),
            "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
            "image_url": images[0]["url"] if images else None,
            "preview_url": track.get("preview_url"),
            "spotify_url": track.get("external_urls", {}).get("spotify"),
            "source": candidate.get("source", "unknown"),
            "discovered_via": candidate.get("via"),
            "coherence_score": round(candidate.get("coherence_score", 0), 3),
            "energy": features.get("energy"),
            "valence": features.get("valence"),
            "tempo": features.get("tempo"),
            "play_count": candidate.get("play_count", 0),
        })

    # Compute flow stats
    from .flow_ordering import compute_playlist_flow_stats
    flow_stats = compute_playlist_flow_stats(ordered_tracks, features_map, genres_map)

    return {
        "tracks": result_tracks,
        "vibe_profile": {
            "anchor_count": len(anchor_track_ids),
            "has_audio_features": profile.has_audio_features,
            "top_genres": vibe_top_genres(profile, limit=5),
            "target_energy": profile.target_energy,
            "target_valence": profile.target_valence,
            "target_tempo": profile.target_tempo,
        },
        "flow_stats": flow_stats,
        "counts": {
            "history": history_selected,
            "discovery": discovery_selected,
            "total": len(result_tracks),
        },
    }


def generate_vibe_playlist(
    anchor_track_ids: List[str],
    track_count: int = 30,
    discovery_ratio: int = 50,
    flow_mode: FlowMode = "smooth",
    exclude_artists: List[str] = None,
    coherence_threshold: float = 0.50,
    max_per_anchor_artist: int = 3,
    max_per_similar_artist: int = 2,
) -> Dict:
    """Generate a fair, evidence-backed blend of several anchor tracks.

    Every candidate keeps a calibrated affinity to every anchor that produced
    it. Pool construction and final selection are both balanced by anchor, so
    an anchor with a larger or easier-to-resolve Last.fm neighbourhood cannot
    silently turn a multi-anchor request into a single-artist radio station.
    """
    exclude_keys = {_normalize_music_text(name) for name in (exclude_artists or [])}
    if not anchor_track_ids or len(anchor_track_ids) > 5:
        raise ValueError("Need 1-5 anchor tracks")
    if len(set(anchor_track_ids)) != len(anchor_track_ids):
        raise ValueError("Anchor track IDs must be unique")
    if not 10 <= track_count <= 100:
        raise ValueError("track_count must be between 10 and 100")
    if not 0 <= discovery_ratio <= 100:
        raise ValueError("discovery_ratio must be between 0 and 100")
    if not 0 <= coherence_threshold <= 1:
        raise ValueError("coherence_threshold must be between 0 and 1")
    if not 0 <= max_per_anchor_artist <= 10:
        raise ValueError("max_per_anchor_artist must be between 0 and 10")
    if not 1 <= max_per_similar_artist <= 10:
        raise ValueError("max_per_similar_artist must be between 1 and 10")

    distinct_anchor_ids = list(dict.fromkeys(anchor_track_ids))
    fetched_anchors = get_tracks_bulk(distinct_anchor_ids)
    fetched_anchor_map = {
        track.get("id"): track for track in fetched_anchors if track and track.get("id")
    }
    anchor_tracks = [
        fetched_anchor_map[track_id]
        for track_id in distinct_anchor_ids
        if track_id in fetched_anchor_map
    ]
    missing_anchor_ids = [
        track_id for track_id in distinct_anchor_ids
        if track_id not in fetched_anchor_map
    ]
    if missing_anchor_ids:
        raise ValueError(
            "Could not fetch every requested anchor track "
            f"({len(missing_anchor_ids)} missing)"
        )

    anchor_specs: List[Dict] = []
    for track in anchor_tracks:
        artist_names = _artist_names(track)
        anchor_specs.append({
            "id": track["id"],
            "track": track,
            "title": track.get("name", ""),
            "artists": artist_names,
            "primary_artist": artist_names[0] if artist_names else "Unknown artist",
        })
    anchor_ids = [spec["id"] for spec in anchor_specs]
    anchor_by_id = {spec["id"]: spec for spec in anchor_specs}
    stable_anchor_ids = sorted(anchor_ids)
    anchor_artist_keys = {
        _normalize_music_text(artist)
        for spec in anchor_specs
        for artist in spec["artists"]
    }
    anchor_artist_anchors: Dict[str, Set[str]] = {}
    anchor_artist_catalog: Dict[str, Dict] = {}
    for spec in anchor_specs:
        for artist in spec["track"].get("artists", []):
            artist_key = _normalize_music_text(artist.get("name", ""))
            if not artist_key:
                continue
            anchor_artist_anchors.setdefault(artist_key, set()).add(spec["id"])
            # Spotify artist IDs let us seed the actual catalog for every
            # credited artist, including a secondary credit that Last.fm may
            # return no track-neighbourhood for at all.
            if artist.get("id"):
                anchor_artist_catalog.setdefault(artist_key, artist)
    excluded_anchor_artists = sorted(anchor_artist_keys & exclude_keys)
    if excluded_anchor_artists:
        raise ValueError(
            "An excluded artist is also used by an anchor track; remove the "
            "artist from exclusions or remove that anchor"
        )

    # Evidence maps retain all anchors instead of keeping only the maximum.
    artist_evidence: Dict[str, Dict[str, Dict]] = {}
    artist_display_names: Dict[str, str] = {}
    track_evidence: Dict[Tuple[str, str], Dict[str, Dict]] = {}
    track_metadata: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def put_evidence(
        store: Dict,
        key,
        anchor_id: str,
        relation: float,
        evidence_type: str,
        rank: int,
        raw_match: Optional[float] = None,
    ) -> None:
        by_anchor = store.setdefault(key, {})
        current = by_anchor.get(anchor_id)
        incoming = {
            "relation": relation,
            "type": evidence_type,
            "rank": rank,
            "raw_match": raw_match,
        }
        if current is None or (
            relation, _EVIDENCE_PRIORITY[evidence_type]
        ) > (
            current["relation"], _EVIDENCE_PRIORITY[current["type"]]
        ):
            by_anchor[anchor_id] = incoming

    track_query_anchors: Dict[Tuple[str, str], List[str]] = {}
    for spec in anchor_specs:
        for artist_name in spec["artists"]:
            artist_key = _normalize_music_text(artist_name)
            artist_display_names.setdefault(artist_key, artist_name)
            put_evidence(
                artist_evidence,
                artist_key,
                spec["id"],
                _calibrated_relation("anchor_artist"),
                "anchor_artist",
                -1,
            )
            similar = get_similar_artists(artist_name, limit=40)
            for rank, item in enumerate(similar):
                name = item.get("name", "").strip()
                key = _normalize_music_text(name)
                if not key or key in exclude_keys:
                    continue
                artist_display_names.setdefault(key, name)
                put_evidence(
                    artist_evidence,
                    key,
                    spec["id"],
                    _calibrated_relation(
                        "artist", rank, len(similar), item.get("match", 0)
                    ),
                    "artist",
                    rank,
                )

            pair = (artist_name, spec["title"])
            track_query_anchors.setdefault(pair, []).append(spec["id"])

    similar_tracks_by_query = get_similar_tracks_batch(
        list(track_query_anchors),
        limit=60,
        max_workers=min(6, len(track_query_anchors)),
    )
    for query, similar_tracks in similar_tracks_by_query.items():
        for rank, item in enumerate(similar_tracks):
            artist = item.get("artist", "").strip()
            title = item.get("name", "").strip()
            key = _track_key(artist, title)
            if not all(key) or key[0] in exclude_keys:
                continue
            track_metadata.setdefault(key, (artist, title))
            for anchor_id in track_query_anchors.get(query, []):
                put_evidence(
                    track_evidence,
                    key,
                    anchor_id,
                    _calibrated_relation(
                        "track", rank, len(similar_tracks), item.get("match", 0)
                    ),
                    "track",
                    rank,
                    float(item.get("match", 0) or 0),
                )

    def evidence_for_names(
        artist_names: List[str],
        title: str,
        popularity: Optional[int],
        play_count: int,
    ) -> Tuple[
        Dict[str, float],
        Dict[str, str],
        Dict[str, float],
        Dict[str, float],
    ]:
        best: Dict[str, Dict] = {}
        artist_support_affinities: Dict[str, float] = {}

        def consider(by_anchor: Dict[str, Dict]) -> None:
            for anchor_id, evidence in by_anchor.items():
                current = best.get(anchor_id)
                if current is None or (
                    evidence["relation"], _EVIDENCE_PRIORITY[evidence["type"]]
                ) > (
                    current["relation"], _EVIDENCE_PRIORITY[current["type"]]
                ):
                    best[anchor_id] = evidence

        for artist_name in artist_names:
            by_artist = artist_evidence.get(_normalize_music_text(artist_name), {})
            for anchor_id, evidence in by_artist.items():
                artist_support_affinities[anchor_id] = max(
                    artist_support_affinities.get(anchor_id, 0),
                    _calibrated_affinity(
                        evidence["relation"], popularity, play_count
                    ),
                )
            consider(by_artist)
            consider(track_evidence.get(_track_key(artist_name, title), {}))

        affinities = {
            anchor_id: _calibrated_affinity(
                evidence["relation"], popularity, play_count
            )
            for anchor_id, evidence in best.items()
        }
        evidence_types = {
            anchor_id: evidence["type"] for anchor_id, evidence in best.items()
        }
        raw_matches = {
            anchor_id: evidence["raw_match"]
            for anchor_id, evidence in best.items()
            if evidence.get("raw_match") is not None
        }
        return (
            affinities,
            evidence_types,
            raw_matches,
            artist_support_affinities,
        )

    all_history = get_all_tracks_with_counts("music")
    known_track_ids = set(all_history)
    known_track_keys = {
        _track_key(_primary_artist_name(row.get("artist", "")), row.get("track", ""))
        for row in all_history.values()
    }
    requested_discovery = int(track_count * discovery_ratio / 100)
    requested_history = track_count - requested_discovery
    warnings: List[str] = []

    candidates: List[Dict] = []
    candidate_by_id: Dict[str, Dict] = {}
    candidate_by_key: Dict[Tuple[str, str], Dict] = {}

    def add_candidate(
        track: Dict,
        source: str,
        play_count: int = 0,
        anchor_id: Optional[str] = None,
        origin_track_key: Optional[Tuple[str, str]] = None,
    ) -> bool:
        track_id = track.get("id")
        artist_names = _artist_names(track)
        if not track_id or not artist_names:
            return False
        if (
            source == "discovery"
            and anchor_id is None
            and _is_explicit_low_value_variant(track.get("name", ""))
        ):
            return False
        if {
            _normalize_music_text(name) for name in artist_names
        } & exclude_keys:
            return False
        semantic_key = _track_key(artist_names[0], track.get("name", ""))
        (
            affinities,
            evidence_types,
            raw_matches,
            artist_support_affinities,
        ) = evidence_for_names(
            artist_names,
            track.get("name", ""),
            track.get("popularity"),
            play_count,
        )
        # Spotify may append a remaster/live suffix that our exact resolver
        # intentionally accepts. Preserve the Last.fm key that led to that
        # catalog match instead of recomputing evidence from the altered title.
        if origin_track_key:
            for related_anchor_id, evidence in track_evidence.get(
                origin_track_key, {}
            ).items():
                score = _calibrated_affinity(
                    evidence["relation"], track.get("popularity"), play_count
                )
                current_type = evidence_types.get(related_anchor_id, "artist")
                if related_anchor_id not in affinities or (
                    score, _EVIDENCE_PRIORITY[evidence["type"]]
                ) > (
                    affinities.get(related_anchor_id, 0),
                    _EVIDENCE_PRIORITY[current_type],
                ):
                    affinities[related_anchor_id] = score
                    evidence_types[related_anchor_id] = evidence["type"]
                    if evidence.get("raw_match") is not None:
                        raw_matches[related_anchor_id] = evidence["raw_match"]
        if anchor_id:
            affinities[anchor_id] = 1.0
            evidence_types[anchor_id] = "anchor"
        if not affinities:
            return False

        # Distinct requested anchors are mandatory even when Spotify exposes
        # two IDs for the same normalized artist/title (for example clean and
        # explicit catalog variants). Semantic dedupe still applies to every
        # non-anchor candidate.
        existing = candidate_by_id.get(track_id)
        if not anchor_id:
            existing = existing or candidate_by_key.get(semantic_key)
        if existing:
            changed = False
            for related_anchor_id, score in affinities.items():
                old_type = existing["evidence_types"].get(related_anchor_id, "artist")
                new_type = evidence_types[related_anchor_id]
                if (
                    score, _EVIDENCE_PRIORITY[new_type]
                ) > (
                    existing["anchor_affinities"].get(related_anchor_id, 0),
                    _EVIDENCE_PRIORITY[old_type],
                ):
                    existing["anchor_affinities"][related_anchor_id] = score
                    existing["evidence_types"][related_anchor_id] = new_type
                    if related_anchor_id in raw_matches:
                        existing["raw_matches"][related_anchor_id] = raw_matches[
                            related_anchor_id
                        ]
                    changed = True
            for related_anchor_id, score in artist_support_affinities.items():
                existing["artist_support_affinities"][related_anchor_id] = max(
                    existing["artist_support_affinities"].get(
                        related_anchor_id, 0
                    ),
                    score,
                )
            if changed:
                existing["coherence_score"] = max(existing["anchor_affinities"].values())
            return False

        candidate = {
            "track": track,
            "features": {},
            "genres": set(),
            "source": source,
            "play_count": play_count,
            "anchor_affinities": affinities,
            "evidence_types": evidence_types,
            "raw_matches": raw_matches,
            "artist_support_affinities": dict(artist_support_affinities),
            "coherence_score": max(affinities.values()),
            "is_anchor": anchor_id is not None,
        }
        candidates.append(candidate)
        candidate_by_id[track_id] = candidate
        candidate_by_key.setdefault(semantic_key, candidate)
        return True

    # Anchors are mandatory and retain their own identity even if their
    # neighbourhood overlaps another seed.
    for spec in anchor_specs:
        history_row = all_history.get(spec["id"], {})
        add_candidate(
            spec["track"],
            "history",
            int(history_row.get("play_count", 0) or 0),
            anchor_id=spec["id"],
        )

    # Pre-rank archive rows separately for every anchor before the bounded
    # Spotify metadata fetch. This avoids global truncation by a large anchor.
    history_entries: List[Dict] = []
    for track_id, row in all_history.items():
        if not track_id or track_id in candidate_by_id:
            continue
        artist_names = [
            name.strip() for name in row.get("artist", "").split(",")
            if name.strip()
        ]
        if not artist_names or {
            _normalize_music_text(name) for name in artist_names
        } & exclude_keys:
            continue
        play_count = int(row.get("play_count", 0) or 0)
        (
            affinities,
            evidence_types,
            raw_matches,
            artist_support_affinities,
        ) = evidence_for_names(
            artist_names, row.get("track", ""), None, play_count
        )
        if not affinities:
            continue
        history_entries.append({
            "track_id": track_id,
            "row": row,
            "play_count": play_count,
            "affinities": affinities,
            "evidence_types": evidence_types,
            "raw_matches": raw_matches,
            "artist_support_affinities": artist_support_affinities,
        })

    history_fetch_budget = max(100, requested_history * 10)
    history_queues = {
        anchor_id: sorted(
            [
                entry for entry in history_entries
                if anchor_id in entry["affinities"]
                # Spotify popularity can add up to .02 over the neutral
                # pre-fetch estimate. Keep borderline archive rows until the
                # enriched score can be checked exactly.
                and entry["affinities"][anchor_id] + 0.02
                >= coherence_threshold
            ],
            key=lambda entry: (
                entry["affinities"][anchor_id],
                _EVIDENCE_PRIORITY[entry["evidence_types"][anchor_id]],
                entry["play_count"],
                entry["track_id"],
            ),
            reverse=True,
        )
        for anchor_id in anchor_ids
    }
    history_fetch_ids: List[str] = []
    fetched_history_ids: Set[str] = set()
    history_positions = {anchor_id: 0 for anchor_id in anchor_ids}
    while len(history_fetch_ids) < history_fetch_budget:
        progress = False
        for related_anchor_id in stable_anchor_ids:
            queue = history_queues[related_anchor_id]
            position = history_positions[related_anchor_id]
            while position < len(queue) and queue[position]["track_id"] in fetched_history_ids:
                position += 1
            history_positions[related_anchor_id] = position
            if position >= len(queue):
                continue
            track_id = queue[position]["track_id"]
            history_positions[related_anchor_id] += 1
            fetched_history_ids.add(track_id)
            history_fetch_ids.append(track_id)
            progress = True
            if len(history_fetch_ids) >= history_fetch_budget:
                break
        if not progress:
            break

    history_row_by_id = {entry["track_id"]: entry for entry in history_entries}
    for track in get_tracks_bulk(history_fetch_ids):
        entry = history_row_by_id.get(track.get("id"))
        if entry:
            add_candidate(track, "history", entry["play_count"])

    # Seed the real catalog for *every* credited anchor artist.  Last.fm may
    # have no track-neighbourhood for a secondary credit (the live failure was
    # Giant Rooks on a jointly credited anchor), so artist evidence alone is
    # not enough: without an explicit catalog fetch there is nothing for the
    # selector to represent.
    anchor_catalog_candidate_ids: Dict[str, Set[str]] = {
        artist_key: set() for artist_key in anchor_artist_catalog
    }
    catalog_results: List[Tuple[str, List[Dict]]] = []
    if anchor_artist_catalog:
        with ThreadPoolExecutor(
            max_workers=min(6, len(anchor_artist_catalog))
        ) as executor:
            future_map = {
                executor.submit(
                    get_artist_top_tracks,
                    artist.get("id"),
                    market="CH",
                ): artist_key
                for artist_key, artist in anchor_artist_catalog.items()
            }
            for future in as_completed(future_map):
                artist_key = future_map[future]
                try:
                    catalog_results.append((artist_key, future.result() or []))
                except Exception:
                    catalog_results.append((artist_key, []))

    for artist_key, top_tracks in sorted(catalog_results):
        for track in top_tracks[:5]:
            if not track or not track.get("id"):
                continue
            track_id = track["id"]
            history_row = all_history.get(track_id, {})
            source = "history" if track_id in known_track_ids else "discovery"
            add_candidate(
                track,
                source,
                int(history_row.get("play_count", 0) or 0),
            )
            names = _artist_names(track)
            semantic_key = _track_key(
                names[0] if names else "", track.get("name", "")
            )
            retained = candidate_by_id.get(track_id) or candidate_by_key.get(
                semantic_key
            )
            if retained and artist_key in _candidate_artist_keys(retained):
                anchor_catalog_candidate_ids[artist_key].add(
                    retained["track"]["id"]
                )

    def artist_limit_for_key(artist_key: str) -> int:
        return (
            max_per_anchor_artist
            if artist_key in anchor_artist_keys
            else max_per_similar_artist
        )

    def fits_artist_caps(candidate: Dict, counts: Dict[str, int]) -> bool:
        keys = _candidate_artist_keys(candidate)
        return bool(keys) and all(
            counts.get(key, 0) < artist_limit_for_key(key)
            for key in keys
        )

    def count_candidate_artists(candidate: Dict, counts: Dict[str, int]) -> None:
        for key in _candidate_artist_keys(candidate):
            counts[key] = counts.get(key, 0) + 1

    # Estimate how many requested history slots can actually survive the same
    # threshold and artist caps used by final selection. Discovery acquisition
    # only provisions the resulting shortfall, so a 0%-new playlist with ample
    # history does not make dozens of unnecessary Spotify searches.
    provisional_artist_counts: Dict[str, int] = {}
    selectable_history_capacity = 0
    for candidate in sorted(
        [item for item in candidates if item["source"] == "history"],
        key=lambda item: (item["is_anchor"], item["coherence_score"]),
        reverse=True,
    ):
        artist_keys = _candidate_artist_keys(candidate)
        if not artist_keys:
            continue
        if candidate["is_anchor"]:
            selectable_history_capacity += 1
            count_candidate_artists(candidate, provisional_artist_counts)
            continue
        if candidate["coherence_score"] < coherence_threshold:
            continue
        if not fits_artist_caps(candidate, provisional_artist_counts):
            continue
        selectable_history_capacity += 1
        count_candidate_artists(candidate, provisional_artist_counts)
        if selectable_history_capacity >= requested_history:
            break

    history_shortfall = max(0, requested_history - selectable_history_capacity)
    remote_fill_target = requested_discovery + history_shortfall

    def direct_lead_can_qualify(
        key: Tuple[str, str],
        related_anchor_id: str,
    ) -> bool:
        direct = track_evidence[key][related_anchor_id]["relation"]
        # A weak track edge can still resolve to an artist with independently
        # strong artist-neighbour evidence.  Include that known support in the
        # pre-network upper bound; unknown Spotify co-credits cannot be known
        # until resolution and are intentionally outside this cheap gate.
        artist = artist_evidence.get(key[0], {}).get(related_anchor_id, {})
        potential_relation = max(direct, artist.get("relation", 0))
        return _calibrated_affinity(potential_relation, 45, 0) >= coherence_threshold

    # Resolve an equal number of direct-track leads from each anchor.
    track_queues = {
        anchor_id: sorted(
            [
                key for key, by_anchor in track_evidence.items()
                if anchor_id in by_anchor
                # Popularity can contribute at most .04 for an unseen track.
                # Do not spend a Spotify search on a Last.fm lead whose known
                # relation cannot possibly clear the requested strictness.
                and direct_lead_can_qualify(key, anchor_id)
            ],
            key=lambda key: (
                track_evidence[key][anchor_id]["relation"],
                -track_evidence[key][anchor_id]["rank"],
                key,
            ),
            reverse=True,
        )
        for anchor_id in anchor_ids
    }
    seen_resolve_keys: Set[Tuple[str, str]] = set()
    track_positions = {anchor_id: 0 for anchor_id in anchor_ids}
    direct_attempts = 0
    direct_resolution_truncated = False

    # Grow related-artist pools round-robin until the cap-aware allocator can
    # fill both the requested discoveries and any proven history shortfall.
    artist_queues = {
        anchor_id: sorted(
            [
                key for key, by_anchor in artist_evidence.items()
                if anchor_id in by_anchor
                and key not in anchor_artist_keys
                and key not in exclude_keys
            ],
            key=lambda key: (
                artist_evidence[key][anchor_id]["relation"],
                -artist_evidence[key][anchor_id]["rank"],
                key,
            ),
            reverse=True,
        )
        for anchor_id in anchor_ids
    }
    artist_positions = {anchor_id: 0 for anchor_id in anchor_ids}
    attempted_artists: Set[str] = set()
    max_artist_attempts = min(
        80,
        max(
            20,
            math.ceil(max(track_count - len(anchor_specs), 0) / 2)
            + 8 * len(anchor_specs),
        ),
    )
    acquisition_deadline = time.monotonic() + 45.0
    # Reserve most of the shared acquisition budget for artist-supported
    # paths.  A slow or unresolved direct graph must not starve them before
    # their first request is made.
    direct_acquisition_deadline = min(
        acquisition_deadline,
        time.monotonic() + 12.0,
    )
    max_artist_fallback_affinity = _calibrated_affinity(
        _calibrated_relation("artist", 0, 40, 1.0),
        45,
        0,
    )
    artist_fallback_possible = (
        coherence_threshold <= max_artist_fallback_affinity
    )

    remote_base, remote_remainder = divmod(
        remote_fill_target, max(len(anchor_ids), 1)
    )
    remote_quotas = {
        anchor_id: remote_base + (1 if index < remote_remainder else 0)
        for index, anchor_id in enumerate(stable_anchor_ids)
    }

    # A direct Last.fm track graph is useful evidence, but it is still one
    # retrieval path.  Soft-cap candidates supported *only* by that path at
    # roughly half an anchor lane so an abundant graph cannot crowd out artist
    # neighbours or credited-artist catalog tracks.  The cap is relaxed later
    # only if the supported pool genuinely cannot fill the requested length.
    unsupported_direct_limits = {
        anchor_id: max(
            1,
            math.ceil(track_count / max(len(anchor_ids), 1) * 0.5),
        )
        for anchor_id in anchor_ids
    }

    def is_unsupported_direct(candidate: Dict, related_anchor_id: str) -> bool:
        artist_support = candidate.get("artist_support_affinities", {})
        return (
            candidate["evidence_types"].get(related_anchor_id) == "track"
            and (
                related_anchor_id not in artist_support
                or artist_support[related_anchor_id] < coherence_threshold
            )
        )

    def allocation_key(candidate: Dict, related_anchor_id: str):
        return (
            not is_unsupported_direct(candidate, related_anchor_id),
            candidate["anchor_affinities"].get(related_anchor_id, 0),
            _EVIDENCE_PRIORITY[
                candidate["evidence_types"].get(related_anchor_id, "artist")
            ],
            candidate.get("play_count", 0),
            candidate["track"].get("id", ""),
        )

    def plan_assignments(
        pool: List[Dict],
        anchor_needs: Dict[str, int],
        current_artist_counts: Dict[str, int],
        excluded_ids: Optional[Set[str]] = None,
        max_assignments: Optional[int] = None,
        unsupported_counts: Optional[Dict[str, int]] = None,
        enforce_unsupported_limits: bool = False,
    ) -> List[Tuple[Dict, str]]:
        """Maximum-cardinality candidate/anchor assignment via residual flow.

        A real augmenting flow is important here: degree-greedy allocation can
        consume a shared bridge for the wrong anchor even when a complete fair
        matching exists. Primary-artist capacities are represented in the
        network; every credited artist is checked again when the plan is
        applied, with a re-plan if a secondary credit is already capped.
        """
        excluded_ids = excluded_ids or set()
        unsupported_counts = unsupported_counts or {
            anchor_id: 0 for anchor_id in anchor_ids
        }
        needs = {
            anchor_id: max(0, int(anchor_needs.get(anchor_id, 0)))
            for anchor_id in anchor_ids
        }
        flow_limit = min(
            sum(needs.values()),
            max_assignments if max_assignments is not None else sum(needs.values()),
        )
        if flow_limit <= 0:
            return []

        eligible_by_track: Dict[str, List[str]] = {}
        candidate_by_track: Dict[str, Dict] = {}
        for candidate in pool:
            track_id = candidate["track"].get("id")
            if (
                not track_id
                or track_id in excluded_ids
                or not fits_artist_caps(candidate, current_artist_counts)
            ):
                continue
            eligible = [
                anchor_id for anchor_id in stable_anchor_ids
                if needs[anchor_id] > 0
                and anchor_id in candidate["anchor_affinities"]
                and candidate["anchor_affinities"][anchor_id]
                >= coherence_threshold
            ]
            if eligible:
                candidate_by_track[track_id] = candidate
                eligible_by_track[track_id] = eligible
        if not candidate_by_track:
            return []

        option_counts = {
            anchor_id: sum(
                anchor_id in eligible for eligible in eligible_by_track.values()
            )
            for anchor_id in anchor_ids
        }
        ranked_track_ids = sorted(
            candidate_by_track,
            key=lambda track_id: (
                max(
                    allocation_key(candidate_by_track[track_id], anchor_id)
                    for anchor_id in eligible_by_track[track_id]
                ),
                -len(eligible_by_track[track_id]),
                track_id,
            ),
            reverse=True,
        )

        graph: Dict[Tuple, List[List]] = {}

        def edges(node: Tuple) -> List[List]:
            return graph.setdefault(node, [])

        def add_edge(left: Tuple, right: Tuple, capacity: int) -> List:
            forward = [right, len(edges(right)), capacity]
            backward = [left, len(edges(left)), 0]
            edges(left).append(forward)
            edges(right).append(backward)
            return forward

        source = ("source",)
        goal = ("goal",)
        sink = ("sink",)
        artist_tracks: Dict[str, List[str]] = {}
        for track_id in ranked_track_ids:
            primary_key = _candidate_artist_key(candidate_by_track[track_id])
            artist_tracks.setdefault(primary_key, []).append(track_id)
        track_rank = {
            track_id: index for index, track_id in enumerate(ranked_track_ids)
        }
        for artist_key in sorted(
            artist_tracks,
            key=lambda key: (
                min(track_rank[track_id] for track_id in artist_tracks[key]),
                key,
            ),
        ):
            remaining_cap = max(
                0,
                artist_limit_for_key(artist_key)
                - current_artist_counts.get(artist_key, 0),
            )
            if remaining_cap <= 0:
                continue
            artist_node = ("artist", artist_key)
            add_edge(source, artist_node, remaining_cap)
            for track_id in artist_tracks[artist_key]:
                candidate_node = ("candidate", track_id)
                add_edge(artist_node, candidate_node, 1)
                candidate = candidate_by_track[track_id]
                for anchor_id in sorted(
                    eligible_by_track[track_id],
                    key=lambda item: (
                        option_counts[item],
                        -candidate["anchor_affinities"].get(item, 0),
                        item,
                    ),
                ):
                    if (
                        enforce_unsupported_limits
                        and is_unsupported_direct(candidate, anchor_id)
                    ):
                        add_edge(
                            candidate_node,
                            ("unsupported", anchor_id),
                            1,
                        )
                    else:
                        add_edge(candidate_node, ("anchor", anchor_id), 1)
        if enforce_unsupported_limits:
            for anchor_id in stable_anchor_ids:
                remaining = max(
                    0,
                    unsupported_direct_limits[anchor_id]
                    - unsupported_counts.get(anchor_id, 0),
                )
                if remaining:
                    add_edge(
                        ("unsupported", anchor_id),
                        ("anchor", anchor_id),
                        remaining,
                    )
        for anchor_id in stable_anchor_ids:
            if needs[anchor_id] > 0:
                add_edge(("anchor", anchor_id), goal, needs[anchor_id])
        add_edge(goal, sink, flow_limit)

        total_flow = 0
        while total_flow < flow_limit:
            levels = {source: 0}
            queue = [source]
            for node in queue:
                for right, _, capacity in edges(node):
                    if capacity > 0 and right not in levels:
                        levels[right] = levels[node] + 1
                        queue.append(right)
            if sink not in levels:
                break
            positions: Dict[Tuple, int] = {}

            def send(node: Tuple, amount: int) -> int:
                if node == sink:
                    return amount
                edge_list = edges(node)
                while positions.get(node, 0) < len(edge_list):
                    index = positions.get(node, 0)
                    right, reverse_index, capacity = edge_list[index]
                    if capacity > 0 and levels.get(right) == levels[node] + 1:
                        sent = send(right, min(amount, capacity))
                        if sent:
                            edge_list[index][2] -= sent
                            edges(right)[reverse_index][2] += sent
                            return sent
                    positions[node] = index + 1
                return 0

            while total_flow < flow_limit:
                sent = send(source, flow_limit - total_flow)
                if not sent:
                    break
                total_flow += sent

        assignments: List[Tuple[Dict, str]] = []
        for track_id in ranked_track_ids:
            candidate_node = ("candidate", track_id)
            for right, reverse_index, capacity in edges(candidate_node):
                if (
                    right[:1] in (("anchor",), ("unsupported",))
                    and capacity == 0
                    and edges(right)[reverse_index][2] == 1
                ):
                    assignments.append((candidate_by_track[track_id], right[1]))
                    break
        assignments.sort(
            key=lambda item: (
                option_counts[item[1]],
                len(eligible_by_track[item[0]["track"]["id"]]),
                tuple(-value if isinstance(value, (int, float)) else value
                      for value in allocation_key(item[0], item[1])[:-1]),
                item[0]["track"]["id"],
            )
        )
        return assignments

    def discovery_allocation_capacity() -> Tuple[int, Dict[str, int]]:
        """Prove distinct, cap-aware capacity with feasible shared matching."""
        pool = [
            candidate for candidate in candidates
            if candidate["source"] == "discovery"
            and candidate["coherence_score"] >= coherence_threshold
        ]
        used_ids: Set[str] = set()
        blocked_ids: Set[str] = set()
        capacity_artist_counts = dict(provisional_artist_counts)
        capacity_by_anchor = {anchor_id: 0 for anchor_id in anchor_ids}
        capacity_unsupported_counts = {
            anchor_id: 0 for anchor_id in anchor_ids
        }

        def apply_capacity_plan(plan: List[Tuple[Dict, str]]) -> int:
            added = 0
            for candidate, anchor_id in plan:
                track_id = candidate["track"].get("id")
                if not track_id or track_id in used_ids:
                    continue
                if not fits_artist_caps(candidate, capacity_artist_counts):
                    blocked_ids.add(track_id)
                    continue
                used_ids.add(track_id)
                count_candidate_artists(candidate, capacity_artist_counts)
                capacity_by_anchor[anchor_id] += 1
                if is_unsupported_direct(candidate, anchor_id):
                    capacity_unsupported_counts[anchor_id] += 1
                added += 1
            return added

        while len(used_ids) < remote_fill_target:
            needs = {
                anchor_id: max(
                    0, remote_quotas[anchor_id] - capacity_by_anchor[anchor_id]
                )
                for anchor_id in anchor_ids
            }
            if not any(needs.values()):
                break
            plan = plan_assignments(
                pool,
                needs,
                capacity_artist_counts,
                used_ids | blocked_ids,
                unsupported_counts=capacity_unsupported_counts,
                enforce_unsupported_limits=True,
            )
            if not plan or not apply_capacity_plan(plan):
                break

        while len(used_ids) < remote_fill_target:
            remaining = remote_fill_target - len(used_ids)
            relaxed_needs = {anchor_id: remaining for anchor_id in anchor_ids}
            plan = plan_assignments(
                pool,
                relaxed_needs,
                capacity_artist_counts,
                used_ids | blocked_ids,
                max_assignments=remaining,
                unsupported_counts=capacity_unsupported_counts,
                enforce_unsupported_limits=True,
            )
            if not plan or not apply_capacity_plan(plan):
                break

        return len(used_ids), capacity_by_anchor

    # Resolve direct Last.fm track leads in bounded, fair batches. Capacity is
    # re-evaluated after every batch, so healthy catalogs stop early while a
    # run of unresolved leads does not hide valid matches later in the queues.
    max_direct_attempts = min(
        300,
        max(60, remote_fill_target * 4) if remote_fill_target else 0,
    )
    capacity_total, capacity_by_anchor = discovery_allocation_capacity()

    def needs_more_discovery_capacity() -> bool:
        return (
            capacity_total < remote_fill_target
            or any(
                capacity_by_anchor[anchor_id] < remote_quotas[anchor_id]
                for anchor_id in anchor_ids
            )
        )

    def resolve_direct_until(deadline: float) -> None:
        nonlocal direct_attempts, capacity_total, capacity_by_anchor
        while (
            remote_fill_target > 0
            and needs_more_discovery_capacity()
            and direct_attempts < max_direct_attempts
            and time.monotonic() < deadline
        ):
            batch_keys: List[Tuple[str, str]] = []
            batch_limit = min(6, max_direct_attempts - direct_attempts)
            while len(batch_keys) < batch_limit:
                progress = False
                for related_anchor_id in stable_anchor_ids:
                    queue = track_queues[related_anchor_id]
                    position = track_positions[related_anchor_id]
                    while (
                        position < len(queue)
                        and queue[position] in seen_resolve_keys
                    ):
                        position += 1
                    track_positions[related_anchor_id] = position
                    if position >= len(queue):
                        continue
                    key = queue[position]
                    track_positions[related_anchor_id] += 1
                    seen_resolve_keys.add(key)
                    batch_keys.append(key)
                    progress = True
                    if len(batch_keys) >= batch_limit:
                        break
                if not progress:
                    break
            if not batch_keys:
                break

            direct_attempts += len(batch_keys)
            exact_results: List[Tuple[Tuple[str, str], Optional[Dict]]] = []
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_map = {
                    executor.submit(
                        _resolve_spotify_track,
                        track_metadata[key][0],
                        track_metadata[key][1],
                    ): key
                    for key in batch_keys
                    if key in track_metadata
                }
                for future in as_completed(future_map):
                    key = future_map[future]
                    try:
                        exact_results.append((key, future.result()))
                    except Exception:
                        continue

            for origin_track_key, track in sorted(
                exact_results, key=lambda item: item[0]
            ):
                if not track or track.get("id") in known_track_ids:
                    continue
                artist_names = _artist_names(track)
                semantic_key = _track_key(
                    artist_names[0] if artist_names else "",
                    track.get("name", ""),
                )
                if semantic_key in known_track_keys:
                    continue
                add_candidate(
                    track,
                    "discovery",
                    origin_track_key=origin_track_key,
                )
            capacity_total, capacity_by_anchor = discovery_allocation_capacity()

    resolve_direct_until(direct_acquisition_deadline)

    while artist_fallback_possible and needs_more_discovery_capacity() and (
        len(attempted_artists) < max_artist_attempts
        and time.monotonic() < acquisition_deadline
    ):
        # Search several independent artist paths concurrently.  Sequential
        # search+top-tracks calls let one slow anchor consume the shared 45s
        # budget before the other anchors were even attempted.
        artist_batch: List[str] = []
        batch_limit = min(
            6,
            max_artist_attempts - len(attempted_artists),
        )
        while len(artist_batch) < batch_limit:
            progress = False
            for related_anchor_id in sorted(
                stable_anchor_ids,
                key=lambda anchor_id: (capacity_by_anchor[anchor_id], anchor_id),
            ):
                if (
                    capacity_by_anchor[related_anchor_id]
                    >= remote_quotas[related_anchor_id]
                    and capacity_total >= remote_fill_target
                ):
                    continue
                queue = artist_queues[related_anchor_id]
                position = artist_positions[related_anchor_id]
                while (
                    position < len(queue)
                    and queue[position] in attempted_artists
                ):
                    position += 1
                artist_positions[related_anchor_id] = position
                if position >= len(queue):
                    continue
                artist_key = queue[position]
                artist_positions[related_anchor_id] += 1
                attempted_artists.add(artist_key)
                artist_batch.append(artist_key)
                progress = True
                if len(artist_batch) >= batch_limit:
                    break
            if not progress:
                break
        if not artist_batch:
            break

        def fetch_artist_tracks(artist_key: str) -> Tuple[str, List[Dict]]:
            canonical_name = artist_display_names.get(artist_key, artist_key)
            spotify_artist = search_artist(canonical_name)
            if (
                not spotify_artist
                or _normalize_music_text(spotify_artist.get("name", ""))
                != artist_key
            ):
                return artist_key, []
            return (
                artist_key,
                get_artist_top_tracks(
                    spotify_artist.get("id"), market="CH"
                )[:3],
            )

        fetched_artist_tracks: List[Tuple[str, List[Dict]]] = []
        # Spotify's development-mode rate limit is low; two concurrent lookup
        # paths hide ordinary latency without creating a retry storm.
        with ThreadPoolExecutor(max_workers=min(2, len(artist_batch))) as executor:
            futures = [
                executor.submit(fetch_artist_tracks, artist_key)
                for artist_key in artist_batch
            ]
            for future in as_completed(futures):
                try:
                    fetched_artist_tracks.append(future.result())
                except Exception:
                    continue

        for _, tracks in sorted(fetched_artist_tracks):
            for track in tracks:
                if not track or track.get("id") in known_track_ids:
                    continue
                names = _artist_names(track)
                if _track_key(
                    names[0] if names else "", track.get("name", "")
                ) in known_track_keys:
                    continue
                add_candidate(track, "discovery")
        capacity_total, capacity_by_anchor = discovery_allocation_capacity()

    # If artist-supported paths are unavailable or still leave a genuine
    # shortage, give the remaining shared budget back to viable direct leads.
    # This preserves exact length for strong direct-only catalogs without
    # allowing them to starve artist acquisition at the start.
    if needs_more_discovery_capacity() and time.monotonic() < acquisition_deadline:
        resolve_direct_until(acquisition_deadline)

    untried_direct_leads = any(
        any(key not in seen_resolve_keys for key in track_queues[anchor_id])
        for anchor_id in anchor_ids
    )
    direct_resolution_truncated = untried_direct_leads and (
        direct_attempts >= max_direct_attempts
        or time.monotonic() >= acquisition_deadline
    )

    if needs_more_discovery_capacity() and (
        direct_resolution_truncated
        or len(attempted_artists) >= max_artist_attempts
        or time.monotonic() >= acquisition_deadline
    ):
        warnings.append(
            "Discovery acquisition reached its bounded request/time budget; "
            "selection continued with every qualified candidate already found."
        )

    qualified_candidates = [
        candidate for candidate in candidates
        if candidate["is_anchor"]
        or candidate["coherence_score"] >= coherence_threshold
    ]
    selected: List[Dict] = []
    selected_ids: Set[str] = set()
    artist_counts: Dict[str, int] = {}
    unsupported_direct_counts = {anchor_id: 0 for anchor_id in anchor_ids}
    source_anchor_counts = {
        "history": {anchor_id: 0 for anchor_id in anchor_ids},
        "discovery": {anchor_id: 0 for anchor_id in anchor_ids},
    }

    def select(
        candidate: Dict,
        related_anchor_id: str,
        force: bool = False,
        enforce_unsupported_limit: bool = False,
    ) -> bool:
        track = candidate["track"]
        track_id = track.get("id")
        artist_keys = _candidate_artist_keys(candidate)
        if not track_id or track_id in selected_ids or not artist_keys:
            return False
        if not force:
            if related_anchor_id not in candidate["anchor_affinities"]:
                return False
            if candidate["anchor_affinities"][related_anchor_id] < coherence_threshold:
                return False
        if not force and not fits_artist_caps(candidate, artist_counts):
            return False
        if (
            not force
            and enforce_unsupported_limit
            and is_unsupported_direct(candidate, related_anchor_id)
            and unsupported_direct_counts[related_anchor_id]
            >= unsupported_direct_limits[related_anchor_id]
        ):
            return False

        candidate["primary_anchor_id"] = related_anchor_id
        candidate["primary_anchor_name"] = anchor_by_id[related_anchor_id]["primary_artist"]
        candidate["coherence_score"] = candidate["anchor_affinities"].get(
            related_anchor_id,
            candidate["coherence_score"],
        )
        evidence_type = candidate["evidence_types"].get(related_anchor_id, "artist")
        anchor_name = candidate["primary_anchor_name"]
        if candidate["is_anchor"]:
            candidate["via"] = "anchor"
        elif candidate["source"] == "history":
            label = "track match" if evidence_type == "track" else "similar"
            candidate["via"] = f"familiar · {label} to {anchor_name}"
        else:
            label = "track match" if evidence_type == "track" else "similar artist"
            candidate["via"] = f"{label} · {anchor_name}"

        selected.append(candidate)
        selected_ids.add(track_id)
        count_candidate_artists(candidate, artist_counts)
        if is_unsupported_direct(candidate, related_anchor_id):
            unsupported_direct_counts[related_anchor_id] += 1
        source_anchor_counts[candidate["source"]][related_anchor_id] += 1
        return True

    for spec in anchor_specs:
        candidate = candidate_by_id.get(spec["id"])
        if candidate:
            select(candidate, spec["id"], force=True)

    history_target = max(requested_history, len(selected))
    discovery_target = max(0, track_count - history_target)
    if history_target != requested_history:
        warnings.append(
            "Anchor tracks require more familiar slots than requested; "
            "the discovery target was reduced."
        )

    source_targets = {
        "history": history_target,
        "discovery": discovery_target,
    }

    def selected_source_count(source: str) -> int:
        return sum(source_anchor_counts[source].values())

    def represented_anchor_artist_keys() -> Set[str]:
        return {
            artist_key
            for candidate in selected
            if not candidate["is_anchor"]
            for artist_key in _candidate_artist_keys(candidate)
            if artist_key in anchor_artist_keys
        }

    # Reserve one real, non-anchor catalog track for every credited artist
    # when a qualified candidate, source slot, and artist cap make that
    # possible.  The anchor itself deliberately does not satisfy this promise.
    def catalog_options_for_artist(artist_key: str) -> List[Tuple]:
        options: List[Tuple] = []
        for track_id in anchor_catalog_candidate_ids.get(artist_key, set()):
            candidate = candidate_by_id.get(track_id)
            if (
                not candidate
                or candidate["is_anchor"]
                or track_id in selected_ids
                or artist_key not in _candidate_artist_keys(candidate)
                or selected_source_count(candidate["source"])
                >= source_targets[candidate["source"]]
                or not fits_artist_caps(candidate, artist_counts)
            ):
                continue
            eligible_anchors = [
                anchor_id
                for anchor_id in anchor_artist_anchors.get(artist_key, set())
                if candidate["anchor_affinities"].get(anchor_id, -1)
                >= coherence_threshold
            ]
            if not eligible_anchors:
                continue
            related_anchor_id = min(
                eligible_anchors,
                key=lambda anchor_id: (
                    source_anchor_counts["history"][anchor_id]
                    + source_anchor_counts["discovery"][anchor_id],
                    -candidate["anchor_affinities"].get(anchor_id, 0),
                    anchor_id,
                ),
            )
            options.append((
                allocation_key(candidate, related_anchor_id),
                candidate,
                related_anchor_id,
            ))
        return options

    while True:
        missing_artist_keys = sorted(
            anchor_artist_keys - represented_anchor_artist_keys()
        )
        if not missing_artist_keys:
            break
        option_groups = [
            (catalog_options_for_artist(artist_key), artist_key)
            for artist_key in missing_artist_keys
        ]
        option_groups = [item for item in option_groups if item[0]]
        if not option_groups:
            break
        options, chosen_artist_key = min(
            option_groups,
            key=lambda item: (len(item[0]), item[1]),
        )
        _, candidate, related_anchor_id = max(
            options,
            key=lambda item: (item[0], item[1]["track"].get("id", "")),
        )
        if not select(
            candidate,
            related_anchor_id,
            enforce_unsupported_limit=True,
        ):
            # Reaching this branch means the option became stale during a
            # prior multi-credit reservation.  Remove it from this requirement
            # and let the next scarcity pass try another catalog track.
            anchor_catalog_candidate_ids[chosen_artist_key].discard(
                candidate["track"]["id"]
            )

    def balanced_source_quotas(total: int, source: str) -> Dict[str, int]:
        """Water-fill this source against the mix already selected."""
        quotas = dict(source_anchor_counts[source])
        projected_totals = {
            anchor_id: (
                source_anchor_counts["history"][anchor_id]
                + source_anchor_counts["discovery"][anchor_id]
            )
            for anchor_id in anchor_ids
        }
        slots = max(0, total - sum(quotas.values()))
        for _ in range(slots):
            anchor_id = min(
                stable_anchor_ids,
                key=lambda item: (projected_totals[item], item),
            )
            quotas[anchor_id] += 1
            projected_totals[anchor_id] += 1
        return quotas

    def allocate_source(
        source: str,
        target: int,
        *,
        enforce_unsupported_limits: bool,
        allow_redistribution: bool,
        warn_on_anchor_shortage: bool,
    ) -> None:
        quotas = balanced_source_quotas(target, source)
        pool = [
            candidate for candidate in qualified_candidates
            if candidate["source"] == source and not candidate["is_anchor"]
        ]
        # First satisfy each anchor's fair share with a true augmenting
        # assignment. This can reroute a shared candidate to preserve the only
        # remaining option for another anchor.
        blocked_ids: Set[str] = set()
        while sum(source_anchor_counts[source].values()) < target:
            needs = {
                anchor_id: max(
                    0,
                    quotas[anchor_id]
                    - source_anchor_counts[source][anchor_id],
                )
                for anchor_id in anchor_ids
            }
            if not any(needs.values()):
                break
            plan = plan_assignments(
                pool,
                needs,
                artist_counts,
                selected_ids | blocked_ids,
                unsupported_counts=unsupported_direct_counts,
                enforce_unsupported_limits=enforce_unsupported_limits,
            )
            if not plan:
                break
            progress = False
            for candidate, related_anchor_id in plan:
                if select(
                    candidate,
                    related_anchor_id,
                    enforce_unsupported_limit=enforce_unsupported_limits,
                ):
                    progress = True
                else:
                    blocked_ids.add(candidate["track"].get("id"))
            if not progress:
                break

        if not allow_redistribution:
            return

        # Only after quotas cannot be met, redistribute genuine shortages.
        quota_shortages = {
            anchor_id: quotas[anchor_id] - source_anchor_counts[source][anchor_id]
            for anchor_id in anchor_ids
            if source_anchor_counts[source][anchor_id] < quotas[anchor_id]
        }
        if (
            warn_on_anchor_shortage
            and quota_shortages
            and sum(source_anchor_counts[source].values()) < target
        ):
            shortage_labels = ", ".join(
                f"{anchor_by_id[anchor_id]['primary_artist']} ({count})"
                for anchor_id, count in sorted(quota_shortages.items())
            )
            warnings.append(
                f"{source.capitalize()} anchor shortages were redistributed only "
                f"after their qualified pools were exhausted: {shortage_labels}."
            )
        while sum(source_anchor_counts[source].values()) < target:
            options = []
            for candidate in pool:
                if candidate["track"].get("id") in selected_ids:
                    continue
                eligible_anchors = [
                    anchor_id for anchor_id in anchor_ids
                    if anchor_id in candidate["anchor_affinities"]
                    and candidate["anchor_affinities"][anchor_id] >= coherence_threshold
                    and not (
                        enforce_unsupported_limits
                        and is_unsupported_direct(candidate, anchor_id)
                        and unsupported_direct_counts[anchor_id]
                        >= unsupported_direct_limits[anchor_id]
                    )
                ]
                if not eligible_anchors:
                    continue
                related_anchor_id = min(
                    eligible_anchors,
                    key=lambda anchor_id: (
                        source_anchor_counts[source][anchor_id],
                        -candidate["anchor_affinities"].get(anchor_id, 0),
                        anchor_id,
                    ),
                )
                options.append((
                    -source_anchor_counts[source][related_anchor_id],
                    allocation_key(candidate, related_anchor_id),
                    candidate,
                    related_anchor_id,
                ))
            if not options:
                break
            _, _, candidate, related_anchor_id = max(
                options, key=lambda item: item[:2]
            )
            if not select(
                candidate,
                related_anchor_id,
                enforce_unsupported_limit=enforce_unsupported_limits,
            ):
                # It cannot become selectable after more artist caps accrue.
                pool.remove(candidate)

    unsupported_relaxed_for: Set[str] = set()
    for source, target in (
        ("history", history_target),
        ("discovery", discovery_target),
    ):
        # Stage one preserves fair anchor quotas while honoring the supported
        # evidence soft cap.  Do not redistribute yet: an underfilled anchor
        # must get the first chance to use a relaxed direct path.
        allocate_source(
            source,
            target,
            enforce_unsupported_limits=True,
            allow_redistribution=False,
            warn_on_anchor_shortage=False,
        )
        before_relaxation = dict(unsupported_direct_counts)
        if selected_source_count(source) < target:
            allocate_source(
                source,
                target,
                enforce_unsupported_limits=False,
                allow_redistribution=True,
                warn_on_anchor_shortage=True,
            )
            unsupported_relaxed_for.update(
                anchor_id
                for anchor_id in anchor_ids
                if unsupported_direct_counts[anchor_id]
                > max(
                    unsupported_direct_limits[anchor_id],
                    before_relaxation[anchor_id],
                )
            )

    # If either requested source is genuinely exhausted, fill the remaining
    # length from the other source and disclose that redistribution.
    while len(selected) < track_count:
        options = []
        total_anchor_counts = {
            anchor_id: (
                source_anchor_counts["history"][anchor_id]
                + source_anchor_counts["discovery"][anchor_id]
            )
            for anchor_id in anchor_ids
        }
        for candidate in qualified_candidates:
            if candidate["track"].get("id") in selected_ids:
                continue
            eligible_anchors = [
                anchor_id for anchor_id in anchor_ids
                if anchor_id in candidate["anchor_affinities"]
                and candidate["anchor_affinities"][anchor_id] >= coherence_threshold
            ]
            if not eligible_anchors:
                continue
            capped_anchors = [
                anchor_id for anchor_id in eligible_anchors
                if not is_unsupported_direct(candidate, anchor_id)
                or unsupported_direct_counts[anchor_id]
                < unsupported_direct_limits[anchor_id]
            ]
            within_soft_cap = bool(capped_anchors)
            if capped_anchors:
                eligible_anchors = capped_anchors
            related_anchor_id = min(
                eligible_anchors,
                key=lambda anchor_id: (
                    total_anchor_counts[anchor_id],
                    -candidate["anchor_affinities"].get(anchor_id, 0),
                    anchor_id,
                ),
            )
            options.append((
                -total_anchor_counts[related_anchor_id],
                allocation_key(candidate, related_anchor_id),
                candidate,
                related_anchor_id,
                within_soft_cap,
            ))
        if not options:
            break
        if any(item[4] for item in options):
            options = [item for item in options if item[4]]
        _, _, candidate, related_anchor_id, within_soft_cap = max(
            options, key=lambda item: item[:2]
        )
        if (
            not within_soft_cap
            and is_unsupported_direct(candidate, related_anchor_id)
        ):
            unsupported_relaxed_for.add(related_anchor_id)
        if not select(candidate, related_anchor_id):
            qualified_candidates.remove(candidate)

    if unsupported_relaxed_for:
        labels = ", ".join(
            anchor_by_id[anchor_id]["primary_artist"]
            for anchor_id in sorted(unsupported_relaxed_for)
        )
        warnings.append(
            "Direct-evidence soft cap relaxed only after supported candidate "
            f"pools were exhausted: {labels}."
        )

    actual_history = sum(candidate["source"] == "history" for candidate in selected)
    actual_discovery = sum(candidate["source"] == "discovery" for candidate in selected)
    if actual_history < requested_history:
        shortage = requested_history - actual_history
        replacement = max(0, actual_discovery - requested_discovery)
        if replacement >= shortage and len(selected) >= track_count:
            fill_note = " discovery filled the shortage."
        elif replacement:
            fill_note = f" discovery partially filled {replacement} of {shortage} slots."
        else:
            fill_note = " those slots remain unfilled."
        warnings.append(
            f"Only {actual_history} of {requested_history} requested familiar tracks "
            f"had strong enough evidence;{fill_note}"
        )
    if actual_discovery < requested_discovery:
        shortage = requested_discovery - actual_discovery
        replacement = max(0, actual_history - requested_history)
        if replacement >= shortage and len(selected) >= track_count:
            fill_note = " familiar tracks filled the shortage."
        elif replacement:
            fill_note = f" familiar tracks partially filled {replacement} of {shortage} slots."
        else:
            fill_note = " those slots remain unfilled."
        warnings.append(
            f"Only {actual_discovery} of {requested_discovery} requested discoveries "
            f"had strong enough evidence;{fill_note}"
        )
    if len(selected) < track_count:
        warnings.append(
            f"Returned {len(selected)} of {track_count} tracks because the qualified "
            "candidate pools were exhausted."
        )

    selected_artist_ids = list(dict.fromkeys(
        artist.get("id")
        for candidate in selected
        for artist in candidate["track"].get("artists", [])
        if artist.get("id")
    ))
    artist_rows = get_artists_bulk(selected_artist_ids)
    selected_artist_by_id = {
        artist.get("id"): artist for artist in artist_rows if artist
    }
    genre_by_artist = {
        artist.get("id"): set(artist.get("genres", []))
        for artist in artist_rows if artist
    }
    for candidate in selected:
        for artist in candidate["track"].get("artists", []):
            candidate["genres"].update(genre_by_artist.get(artist.get("id"), set()))

    anchor_artist_ids = list(dict.fromkeys(
        artist.get("id")
        for spec in anchor_specs
        for artist in spec["track"].get("artists", [])
        if artist.get("id")
    ))
    anchor_artist_rows = [
        selected_artist_by_id[artist_id]
        for artist_id in anchor_artist_ids
        if artist_id in selected_artist_by_id
    ]
    anchor_artist_by_id = {
        artist.get("id"): artist for artist in anchor_artist_rows if artist
    }
    genre_queues: Dict[str, List[str]] = {}
    for spec in anchor_specs:
        genres: List[str] = []
        for credited_artist in spec["track"].get("artists", []):
            artist = anchor_artist_by_id.get(credited_artist.get("id"), {})
            for genre in artist.get("genres", []):
                if genre not in genres:
                    genres.append(genre)
        genre_queues[spec["id"]] = genres

    anchor_genres: List[str] = []
    genre_positions = {anchor_id: 0 for anchor_id in anchor_ids}
    while True:
        progress = False
        for anchor_id in anchor_ids:
            queue = genre_queues[anchor_id]
            position = genre_positions[anchor_id]
            while position < len(queue) and queue[position] in anchor_genres:
                position += 1
            genre_positions[anchor_id] = position
            if position >= len(queue):
                continue
            anchor_genres.append(queue[position])
            genre_positions[anchor_id] += 1
            progress = True
        if not progress:
            break
    artists_with_genres = {
        artist.get("id") for artist in anchor_artist_rows if artist.get("genres")
    }
    genre_covered_anchors = sum(
        any(
            artist.get("id") in artists_with_genres
            for artist in spec["track"].get("artists", [])
        )
        for spec in anchor_specs
    )
    if genre_covered_anchors < len(anchor_specs):
        warnings.append(
            "Spotify genre metadata was unavailable for "
            f"{len(anchor_specs) - genre_covered_anchors} of {len(anchor_specs)} anchors; "
            "the displayed genres are partial and were not used as similarity evidence."
        )

    selected_tracks = [candidate["track"] for candidate in selected]
    features_map = {
        candidate["track"]["id"]: candidate["features"] for candidate in selected
    }
    genres_map = {
        candidate["track"]["id"]: candidate["genres"] for candidate in selected
    }
    group_map = {
        candidate["track"]["id"]: candidate["primary_anchor_id"]
        for candidate in selected
    }
    affinities_map = {
        candidate["track"]["id"]: candidate["anchor_affinities"]
        for candidate in selected
    }
    ordered_tracks = order_playlist(
        selected_tracks,
        features_map,
        genres_map,
        flow_mode,
        group_map=group_map,
        max_group_run=3,
        affinities_map=affinities_map,
    )
    candidate_map = {candidate["track"]["id"]: candidate for candidate in selected}

    result_tracks = []
    for track in ordered_tracks:
        candidate = candidate_map[track["id"]]
        album = track.get("album", {})
        images = album.get("images", [])
        result_tracks.append({
            "track_id": track.get("id"),
            "track": track.get("name"),
            "artist": ", ".join(_artist_names(track)),
            "image_url": images[0]["url"] if images else None,
            "preview_url": track.get("preview_url"),
            "spotify_url": track.get("external_urls", {}).get("spotify"),
            "source": candidate["source"],
            "discovered_via": candidate.get("via"),
            "coherence_score": round(candidate["coherence_score"], 3),
            "evidence_raw_match": (
                round(
                    candidate.get("raw_matches", {}).get(
                        candidate["primary_anchor_id"]
                    ),
                    6,
                )
                if candidate.get("raw_matches", {}).get(
                    candidate["primary_anchor_id"]
                ) is not None
                else None
            ),
            "primary_anchor_id": candidate["primary_anchor_id"],
            "primary_anchor_name": candidate["primary_anchor_name"],
            "anchor_affinities": {
                anchor_id: round(candidate["anchor_affinities"][anchor_id], 3)
                for anchor_id in anchor_ids
                if anchor_id in candidate["anchor_affinities"]
            },
            "energy": None,
            "valence": None,
            "tempo": None,
            "play_count": candidate.get("play_count", 0),
        })

    from .flow_ordering import compute_playlist_flow_stats
    flow_stats = compute_playlist_flow_stats(ordered_tracks, features_map, genres_map)
    if flow_mode == "shuffle":
        flow_stats["ordering_basis"] = "shuffle"
    elif any(features_map.values()):
        flow_stats["ordering_basis"] = "audio_features"
    elif len(anchor_ids) == 1:
        flow_stats["ordering_basis"] = "artist_similarity"
    else:
        flow_stats["ordering_basis"] = "multi_anchor_similarity"

    anchor_mix = []
    for spec in anchor_specs:
        related_tracks = [
            track for track in result_tracks
            if track["primary_anchor_id"] == spec["id"]
        ]
        anchor_mix.append({
            "anchor_track_id": spec["id"],
            "anchor_track": spec["title"],
            "anchor_artist": ", ".join(spec["artists"]),
            "count": len(related_tracks),
            "history": sum(track["source"] == "history" for track in related_tracks),
            "discovery": sum(track["source"] == "discovery" for track in related_tracks),
        })

    return {
        "tracks": result_tracks,
        "vibe_profile": {
            "anchor_count": len(anchor_tracks),
            "has_audio_features": any(features_map.values()),
            "top_genres": anchor_genres[:5],
            "target_energy": None,
            "target_valence": None,
            "target_tempo": None,
        },
        "flow_stats": flow_stats,
        "counts": {
            "history": actual_history,
            "discovery": actual_discovery,
            "total": len(result_tracks),
            "requested_history": requested_history,
            "requested_discovery": requested_discovery,
        },
        "anchor_mix": anchor_mix,
        "warnings": warnings,
    }
