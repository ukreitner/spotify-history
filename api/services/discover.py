"""Artist discovery grounded in the listener's actual favorite artists."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from math import log1p
import re
import unicodedata
from typing import Dict, List

from ..db import get_all_artist_ids, get_all_tracks_with_counts, get_recent_listening, get_top_artists
from ..lastfm_client import get_similar_artists
from ..spotify_client import get_artist_top_tracks, search_artist, search_tracks_by_artist


def normalize_artist_name(name: str) -> str:
    """Normalize artist names enough to reject Spotify search impostors."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def get_seed_artists(limit: int = 10) -> List[str]:
    """Blend current listening with durable favorites for less one-note results."""
    seeds: List[str] = []
    recent = get_recent_listening(days=90, content_type="music")
    all_time = get_top_artists(limit=25, content_type="music")

    for row in [*recent.get("artists", [])[:6], *all_time]:
        name = row.get("artist", "").split(", ")[0].strip()
        if name and name.lower() not in {seed.lower() for seed in seeds}:
            seeds.append(name)
        if len(seeds) >= limit:
            break
    return seeds


def get_similarity_candidates(seeds: List[str]) -> List[Dict]:
    """Aggregate Last.fm similarity across seeds instead of trusting genre search."""
    aggregate: Dict[str, Dict] = {}

    with ThreadPoolExecutor(max_workers=min(8, len(seeds))) as executor:
        futures = {
            executor.submit(get_similar_artists, seed, 30): (seed, rank)
            for rank, seed in enumerate(seeds)
        }
        for future in as_completed(futures):
            seed, rank = futures[future]
            seed_weight = 1 / (1 + 0.12 * rank)
            try:
                similar_artists = future.result()
            except Exception:
                continue

            for similar in similar_artists:
                name = similar.get("name", "").strip()
                match = float(similar.get("match", 0) or 0)
                if not name or match < 0.12:
                    continue

                key = normalize_artist_name(name)
                if not key:
                    continue
                candidate = aggregate.setdefault(
                    key,
                    {"artist_name": name, "similarity_score": 0.0, "sources": []},
                )
                candidate["similarity_score"] += match * seed_weight
                candidate["sources"].append({"artist": seed, "match": match})

    for candidate in aggregate.values():
        candidate["sources"].sort(key=lambda source: source["match"], reverse=True)
        # Independent agreement from multiple favorites is a strong taste signal.
        candidate["similarity_score"] += 0.18 * min(len(candidate["sources"]) - 1, 3)

    return sorted(
        aggregate.values(),
        key=lambda candidate: candidate["similarity_score"],
        reverse=True,
    )


@lru_cache(maxsize=8)
def discover_new_artists(limit: int = 20) -> List[Dict]:
    """Return novel, diverse artists related to the user's real favorites."""
    known_names = {normalize_artist_name(name) for name in get_all_artist_ids("music")}
    known_track_ids = set(get_all_tracks_with_counts("music"))
    seeds = get_seed_artists()
    candidates = get_similarity_candidates(seeds)
    resolved: List[Dict] = []

    # Resolve a bounded pool through Spotify and require an exact artist-name
    # match. This rejects the metadata impostors produced by broad genre search.
    for candidate in candidates[:60]:
        if len(resolved) >= max(limit * 2, 30):
            break

        name = candidate["artist_name"]
        key = normalize_artist_name(name)
        if key in known_names:
            continue

        spotify_artist = search_artist(name)
        if not spotify_artist:
            continue
        spotify_name = spotify_artist.get("name", "")
        if normalize_artist_name(spotify_name) != key:
            continue

        artist_id = spotify_artist.get("id")
        if not artist_id:
            continue

        tracks = get_artist_top_tracks(artist_id, market="CH")
        if not tracks:
            tracks = [
                track
                for track in search_tracks_by_artist(spotify_name, limit=10)
                if any(artist.get("id") == artist_id for artist in track.get("artists", []))
            ]
        sample = next(
            (track for track in tracks if track.get("id") not in known_track_ids),
            tracks[0] if tracks else None,
        )
        if not sample:
            continue

        images = spotify_artist.get("images", [])
        popularity = int(spotify_artist.get("popularity", 0) or 0)
        sources = candidate["sources"]
        source_names = []
        for source in sources:
            if source["artist"] not in source_names:
                source_names.append(source["artist"])

        # Similarity dominates; popularity is only a small confidence prior so
        # the list does not collapse into either stars or metadata ghosts.
        quality_score = candidate["similarity_score"] + 0.08 * log1p(popularity)
        resolved.append({
            "artist_id": artist_id,
            "artist_name": spotify_name,
            "genres": spotify_artist.get("genres", [])[:4],
            "image_url": images[0]["url"] if images else None,
            "popularity": popularity,
            "relevance": round(quality_score, 4),
            "sample_track": sample.get("name"),
            "sample_track_id": sample.get("id"),
            "preview_url": sample.get("preview_url"),
            "seed_artist": ", ".join(source_names[:2]),
            "found_via_genre": None,
            "_primary_seed": source_names[0] if source_names else "",
        })

    resolved.sort(key=lambda artist: artist["relevance"], reverse=True)

    # Prevent one favorite from monopolizing the screen while retaining a
    # fallback if the listener has a very narrow similarity graph.
    selected: List[Dict] = []
    deferred: List[Dict] = []
    seed_counts: Dict[str, int] = {}
    for artist in resolved:
        seed = artist["_primary_seed"]
        if seed_counts.get(seed, 0) >= 3:
            deferred.append(artist)
            continue
        seed_counts[seed] = seed_counts.get(seed, 0) + 1
        selected.append(artist)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        selected.extend(deferred[: limit - len(selected)])

    for artist in selected:
        artist.pop("_primary_seed", None)
    return selected[:limit]
