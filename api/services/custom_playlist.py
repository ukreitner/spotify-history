from typing import List, Dict, Set, Optional, Literal
from datetime import datetime, timedelta
import random
from ..db import (
    get_all_tracks_with_counts, get_top_artists, get_top_genres, query_all_dbs,
    get_top_tracks, get_recent_listening, search_user_tracks
)
from ..spotify_client import (
    enrich_tracks_with_spotify_data, search_tracks_by_genre, get_audio_features,
    get_recommendations, get_artist_related, get_artist_top_tracks, search_artist,
    get_artist_albums, get_album_tracks, get_tracks_bulk, get_artists_bulk
)
from .vibe_profile import build_vibe_profile, VibeProfile, get_top_genres as vibe_top_genres
from .coherence import compute_total_coherence, get_coherence_breakdown
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


def generate_vibe_playlist(
    anchor_track_ids: List[str],
    track_count: int = 30,
    discovery_ratio: int = 50,
    flow_mode: FlowMode = "smooth",
    exclude_artists: List[str] = None,
) -> Dict:
    """
    Generate a coherent playlist based on anchor tracks.

    Args:
        anchor_track_ids: 1-5 track IDs that define the vibe
        track_count: Target number of tracks (10-100)
        discovery_ratio: Percentage of new music (0-100)
        flow_mode: "smooth", "energy_arc", or "shuffle"
        exclude_artists: Artists to exclude

    Returns:
        Dict with:
        - tracks: ordered list of tracks
        - vibe_profile: the computed vibe profile
        - flow_stats: transition quality stats
    """
    exclude_artists = exclude_artists or []
    exclude_lower = {a.lower() for a in exclude_artists}

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

        # Strategy 1: Deep cuts from anchor artists (albums not just top tracks)
        for anchor_artist_id in list(profile.anchor_artist_ids)[:5]:
            # Get albums
            albums = get_artist_albums(anchor_artist_id, limit=5)
            for album in albums:
                album_tracks = get_album_tracks(album.get("id"))
                # Sort by popularity to find hidden gems
                album_tracks.sort(key=lambda t: t.get("popularity", 50) if t else 100)
                for track in album_tracks[:5]:  # Deep cuts from each album
                    if not track:
                        continue
                    tid = track.get("id")
                    if not tid or tid in existing_ids:
                        continue

                    track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                    if any(a in exclude_lower for a in track_artists):
                        continue

                    existing_ids.add(tid)
                    artist_name = track.get("artists", [{}])[0].get("name", "Unknown")
                    candidates.append({
                        "track": track,
                        "features": {},
                        "genres": set(top_vibe_genres),  # Inherit anchor genres
                        "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                        "source": "discovery",
                        "via": f"deep cut · {artist_name}",
                    })

            # Also get top tracks (more popular but still relevant)
            top = get_artist_top_tracks(anchor_artist_id)
            for track in top[:5]:
                if not track:
                    continue
                tid = track.get("id")
                if not tid or tid in existing_ids:
                    continue

                track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                if any(a in exclude_lower for a in track_artists):
                    continue

                existing_ids.add(tid)
                artist_name = track.get("artists", [{}])[0].get("name", "Unknown")
                candidates.append({
                    "track": track,
                    "features": {},
                    "genres": set(top_vibe_genres),
                    "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                    "source": "discovery",
                    "via": f"top track · {artist_name}",
                })

        # Strategy 2: Related artists (if API works)
        for anchor_artist_id in list(profile.anchor_artist_ids)[:3]:
            related = related_artists_map.get(anchor_artist_id, set())
            for rel_id in list(related)[:5]:
                # Get albums from related artist
                albums = get_artist_albums(rel_id, limit=2)
                for album in albums:
                    album_tracks = get_album_tracks(album.get("id"))
                    album_tracks.sort(key=lambda t: t.get("popularity", 50) if t else 100)
                    for track in album_tracks[:3]:
                        if not track:
                            continue
                        tid = track.get("id")
                        if not tid or tid in existing_ids:
                            continue

                        track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                        if any(a in exclude_lower for a in track_artists):
                            continue

                        existing_ids.add(tid)
                        artist_name = track.get("artists", [{}])[0].get("name", "Unknown")
                        candidates.append({
                            "track": track,
                            "features": {},
                            "genres": set(),
                            "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                            "source": "discovery",
                            "via": f"similar · {artist_name}",
                        })

        # Strategy 3: Genre search (targeted with anchor info)
        for genre in top_vibe_genres:
            # Search with artist names to get similar music
            for anchor_name in list(anchor_artist_names)[:2]:
                query = f"genre:{genre}"
                genre_tracks = search_tracks_by_genre(genre, limit=50)
                # Prefer lower popularity tracks (hidden gems)
                genre_tracks.sort(key=lambda t: t.get("popularity", 50))

                for track in genre_tracks:
                    tid = track.get("id")
                    if not tid or tid in existing_ids:
                        continue

                    track_artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                    if any(a in exclude_lower for a in track_artists):
                        continue

                    # Skip if already know this artist well
                    first_artist = track_artists[0] if track_artists else ""
                    if first_artist in known_artist_names:
                        continue

                    existing_ids.add(tid)
                    candidates.append({
                        "track": track,
                        "features": {},
                        "genres": {genre},
                        "artist_ids": {a.get("id") for a in track.get("artists", []) if a.get("id")},
                        "source": "discovery",
                        "via": f"genre · {genre}",
                    })

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

    # Minimum coherence threshold - filter out unrelated tracks
    MIN_COHERENCE_THRESHOLD = 0.35

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
