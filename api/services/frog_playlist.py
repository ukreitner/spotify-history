"""
Boiling the Frog Playlist Generator.

Creates a playlist that smoothly transitions from one track to another
using A* pathfinding over Last.fm's track similarity graph.
"""

import heapq
import queue
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Callable, List, Dict, Optional, Set, Tuple
from ..lastfm_client import get_similar_tracks, get_similar_tracks_batch
from ..spotify_client import search_tracks_advanced, get_tracks_bulk

# Last.fm's ``match`` value is a ranking signal, not a probability. Real,
# musically tight indie-folk/pop transitions commonly sit around 0.12-0.30.
# Values below this floor still return as best-effort routes with a warning.
MIN_FROG_TRANSITION = 0.12


def compute_heuristic(
    track_key: Tuple[str, str],
    end_key: Tuple[str, str],
    end_neighborhood: Dict[Tuple[str, str], float],
    end_2hop: Set[Tuple[str, str]],
) -> float:
    """
    Estimate remaining cost from track to end.
    Must be admissible (never overestimate) for optimal path.
    """
    # Direct hit - we're at the goal
    if track_key == end_key:
        return 0

    # 1-hop away - in end's similar tracks
    if track_key in end_neighborhood:
        # Return the actual distance (1 - match score)
        return 1 - end_neighborhood[track_key]

    # 2-hop away - in end's 2nd-degree similar
    if track_key in end_2hop:
        return 0.3  # At least 2 edges away, conservative estimate

    # Unknown - assume 3+ hops
    return 0.5  # Admissible: real path likely costs more


def astar_find_path(
    start: Dict,
    end: Dict,
    max_iterations: int = 1000,
    progress_callback=None,
) -> Optional[List[Dict]]:
    """
    Find shortest path from start track to end track using bidirectional search.

    Args:
        start: Dict with 'artist' and 'name' keys
        end: Dict with 'artist' and 'name' keys
        max_iterations: Maximum nodes to expand before giving up
        progress_callback: Optional callback(iteration, visited_count, current_track, best_h)

    Returns:
        List of track dicts representing the path, or None if no path found
    """
    print(f"[BiA*] Starting bidirectional search: {start['artist']} - {start['name']} → {end['artist']} - {end['name']}")

    start_key = (start["artist"].lower(), start["name"].lower())
    end_key = (end["artist"].lower(), end["name"].lower())

    if start_key == end_key:
        return [start]

    # Forward search (from start)
    counter_f = 0
    open_f = [(0, counter_f, 0, start_key, start, [start])]
    visited_f: Dict[Tuple[str, str], List[Dict]] = {}
    g_scores_f: Dict[Tuple[str, str], float] = {start_key: 0}

    # Backward search (from end)
    counter_b = 0
    open_b = [(0, counter_b, 0, end_key, end, [end])]
    visited_b: Dict[Tuple[str, str], List[Dict]] = {}
    g_scores_b: Dict[Tuple[str, str], float] = {end_key: 0}

    iterations = 0
    SIMILAR_LIMIT = 30

    while (open_f or open_b) and iterations < max_iterations:
        # Expand forward
        if open_f:
            iterations += 1
            _, _, g, current_key, current, path = heapq.heappop(open_f)

            if current_key not in visited_f:
                visited_f[current_key] = path

                if current_key in visited_b:
                    backward_path = visited_b[current_key]
                    complete_path = path[:-1] + list(reversed(backward_path))
                    print(f"[BiA*] Found path in {iterations} iterations!")
                    return complete_path

                similar = get_similar_tracks(current["artist"], current["name"], limit=SIMILAR_LIMIT)
                for neighbor in similar:
                    neighbor_key = (neighbor["artist"].lower(), neighbor["name"].lower())
                    if neighbor_key in visited_f:
                        continue
                    edge_cost = 1 - neighbor["match"]
                    new_g = g + edge_cost
                    if neighbor_key not in g_scores_f or new_g < g_scores_f[neighbor_key]:
                        g_scores_f[neighbor_key] = new_g
                        counter_f += 1
                        heapq.heappush(open_f, (new_g, counter_f, new_g, neighbor_key, neighbor, path + [neighbor]))

        # Expand backward
        if open_b:
            iterations += 1
            _, _, g, current_key, current, path = heapq.heappop(open_b)

            if current_key not in visited_b:
                visited_b[current_key] = path

                if current_key in visited_f:
                    forward_path = visited_f[current_key]
                    complete_path = forward_path[:-1] + list(reversed(path))
                    print(f"[BiA*] Found path in {iterations} iterations!")
                    return complete_path

                similar = get_similar_tracks(current["artist"], current["name"], limit=SIMILAR_LIMIT)
                for neighbor in similar:
                    neighbor_key = (neighbor["artist"].lower(), neighbor["name"].lower())
                    if neighbor_key in visited_b:
                        continue
                    edge_cost = 1 - neighbor["match"]
                    new_g = g + edge_cost
                    if neighbor_key not in g_scores_b or new_g < g_scores_b[neighbor_key]:
                        g_scores_b[neighbor_key] = new_g
                        counter_b += 1
                        heapq.heappush(open_b, (new_g, counter_b, new_g, neighbor_key, neighbor, path + [neighbor]))

        if iterations % 50 == 0:
            print(f"[BiA*] iter={iterations} | fwd={len(visited_f)} | bwd={len(visited_b)} | queues={len(open_f)}+{len(open_b)}")

    print(f"[BiA*] NO PATH FOUND after {iterations} iterations")
    return None


def resolve_to_spotify(artist: str, track: str) -> Optional[Dict]:
    """
    Find a track on Spotify given artist and track name from Last.fm.

    Returns Spotify track dict or None if not found.
    """
    query = f"{track} {artist}"
    results = search_tracks_advanced(query, limit=5)

    if not results:
        return None

    wanted_artist = _normalized_text(artist)
    wanted_track = _normalized_track_name(track)
    best: Optional[Tuple[float, Dict]] = None

    for result in results:
        result_track = _normalized_track_name(result.get("name", ""))
        artist_scores = [
            _text_similarity(wanted_artist, _normalized_text(item.get("name", "")))
            for item in result.get("artists", [])
        ]
        artist_score = max(artist_scores, default=0.0)
        track_score = _text_similarity(wanted_track, result_track)

        # Requiring both halves prevents a popular but unrelated first search
        # result from silently becoming a giant musical jump.
        if artist_score < 0.58 or track_score < 0.62:
            continue

        score = (artist_score * 0.55) + (track_score * 0.45)
        if best is None or score > best[0]:
            best = (score, result)

    return best[1] if best else None


def _normalized_text(value: str) -> str:
    """Normalize artist/title text for stable identity and fuzzy matching."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _normalized_track_name(value: str) -> str:
    """Ignore common release labels while retaining the actual song title."""
    value = re.sub(
        r"\s*[\(\[][^)\]]*(?:remaster|remix|version|edit|live|mono|stereo)[^)\]]*[\)\]]",
        "",
        value or "",
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s*[-–—]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|remix|radio edit|live).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return _normalized_text(value)


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right).ratio()


def track_key(track: Dict) -> Tuple[str, str]:
    """Canonical Last.fm identity for a route node."""
    return (
        _normalized_text(track.get("artist", "")),
        _normalized_track_name(track.get("name", "")),
    )


def _spotify_id(track: Optional[Dict]) -> Optional[str]:
    return track.get("id") if track else None


def _adjacency_for(
    nodes: List[Dict],
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
    similarity_fetcher: Callable,
    limit: int,
) -> None:
    """Populate normalized similarity lists for any nodes not already cached."""
    missing = [node for node in nodes if track_key(node) not in cache]
    if not missing:
        return

    requests = [(node["artist"], node["name"]) for node in missing]
    results = similarity_fetcher(requests, limit=limit, max_workers=min(20, len(requests)))

    for node, request_key in zip(missing, requests):
        normalized_neighbors: Dict[Tuple[str, str], Tuple[Dict, float]] = {}
        for neighbor in results.get(request_key, []):
            key = track_key(neighbor)
            if not all(key) or key == track_key(node):
                continue
            score = max(0.0, min(1.0, float(neighbor.get("match", 0.0))))
            previous = normalized_neighbors.get(key)
            if previous is None or score > previous[1]:
                normalized_neighbors[key] = (neighbor, score)
        cache[track_key(node)] = normalized_neighbors


def _transition_similarity(
    left: Dict,
    right: Dict,
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
) -> float:
    """Best observed directional Last.fm similarity for an adjacent pair."""
    left_key = track_key(left)
    right_key = track_key(right)
    observations: List[float] = []

    if right_key in cache.get(left_key, {}):
        observations.append(cache[left_key][right_key][1])
    if left_key in cache.get(right_key, {}):
        observations.append(cache[right_key][left_key][1])

    # A node's match is the edge score recorded by the original graph search.
    if not observations:
        fallback = right.get("match")
        if fallback is not None:
            observations.append(float(fallback))

    return max(observations, default=0.0)


def _candidate_insertions(
    route: List[Dict],
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
    used_keys: Set[Tuple[str, str]],
    max_per_artist: Optional[int] = None,
) -> List[Tuple[Tuple[float, ...], int, Dict, float, float]]:
    """
    Rank one-song subdivisions of every edge.

    The first ranking term is the resulting route's weakest hop. This makes
    bottleneck quality, rather than a deceptively good average, the primary
    optimization target.
    """
    edge_scores = [
        _transition_similarity(route[index], route[index + 1], cache)
        for index in range(len(route) - 1)
    ]
    artist_counts = Counter(track_key(node)[0] for node in route)
    insertions = []

    for index, (left, right) in enumerate(zip(route, route[1:])):
        left_key = track_key(left)
        right_key = track_key(right)
        left_neighbors = cache.get(left_key, {})
        right_neighbors = cache.get(right_key, {})

        # Start with common neighbors. Once a candidate's own neighborhood has
        # been fetched, the broader union also admits asymmetric Last.fm links.
        candidate_keys = set(left_neighbors) | set(right_neighbors)
        other_scores = edge_scores[:index] + edge_scores[index + 1:]
        other_floor = min(other_scores, default=1.0)

        for candidate_key in candidate_keys:
            if candidate_key in used_keys:
                continue

            candidate_entry = left_neighbors.get(candidate_key) or right_neighbors.get(candidate_key)
            if not candidate_entry:
                continue
            candidate = candidate_entry[0]
            artist = candidate_key[0]
            if max_per_artist is not None and artist_counts[artist] >= max_per_artist:
                continue

            left_score = _transition_similarity(left, candidate, cache)
            right_score = _transition_similarity(candidate, right, cache)
            if left_score <= 0 or right_score <= 0:
                continue

            local_floor = min(left_score, right_score)
            resulting_floor = min(other_floor, local_floor)
            edge_relief = local_floor - edge_scores[index]
            same_neighbor_artist = int(
                artist in {track_key(left)[0], track_key(right)[0]}
            )
            rank = (
                resulting_floor,
                -same_neighbor_artist,
                -artist_counts[artist],
                local_floor,
                (left_score + right_score) / 2,
                edge_relief,
            )
            insertions.append((rank, index, candidate, left_score, right_score))

    return sorted(insertions, key=lambda item: item[0], reverse=True)


def _broaden_candidate_graph(
    route: List[Dict],
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
    used_keys: Set[Tuple[str, str]],
    similarity_fetcher: Callable,
    limit: int,
    candidates_per_endpoint: int = 12,
) -> None:
    """
    Fetch promising one-sided neighbors so asymmetric two-sided links appear.

    Last.fm's ranked lists are not perfectly symmetric. A candidate omitted
    from B's top list can still rate B highly in its own list.
    """
    edge_scores = [
        (_transition_similarity(route[index], route[index + 1], cache), index)
        for index in range(len(route) - 1)
    ]
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    for _, index in sorted(edge_scores)[: min(5, len(edge_scores))]:
        left, right = route[index], route[index + 1]
        for endpoint in (left, right):
            neighbors = sorted(
                cache.get(track_key(endpoint), {}).values(),
                key=lambda item: item[1],
                reverse=True,
            )
            for candidate, _ in neighbors[:candidates_per_endpoint]:
                key = track_key(candidate)
                if key in used_keys or key in seen or key in cache:
                    continue
                seen.add(key)
                candidates.append(candidate)

    _adjacency_for(candidates, cache, similarity_fetcher, limit)


def _resolve_candidates_batch(
    insertions: List[Tuple[Tuple[float, ...], int, Dict, float, float]],
    resolver_cache: Dict[Tuple[str, str], Optional[Dict]],
    spotify_resolver: Callable[[str, str], Optional[Dict]],
    max_workers: int = 8,
) -> None:
    """Resolve candidate tracks on Spotify concurrently and cache failures."""
    candidates: Dict[Tuple[str, str], Dict] = {}
    for _, _, candidate, _, _ in insertions:
        key = track_key(candidate)
        if key not in resolver_cache:
            candidates[key] = candidate

    if not candidates:
        return

    def resolve_one(item):
        key, candidate = item
        return key, spotify_resolver(candidate["artist"], candidate["name"])

    with ThreadPoolExecutor(max_workers=min(max_workers, len(candidates))) as executor:
        futures = [executor.submit(resolve_one, item) for item in candidates.items()]
        for future in as_completed(futures):
            try:
                key, spotify_track = future.result()
                resolver_cache[key] = spotify_track
            except Exception:
                # A failed Spotify lookup makes this candidate unusable, but
                # should not abort the whole route.
                continue

    for key in candidates:
        resolver_cache.setdefault(key, None)


def expand_path_to_exact_length(
    path: List[Dict],
    target_length: int,
    *,
    spotify_resolver: Callable[[str, str], Optional[Dict]] = resolve_to_spotify,
    similarity_fetcher: Callable = get_similar_tracks_batch,
    similarity_limit: int = 100,
    max_seconds: float = 45.0,
    progress_callback: Optional[Callable[[Dict], None]] = None,
) -> Tuple[Optional[List[Dict]], Dict]:
    """
    Grow a valid graph path to exactly ``target_length`` distinct Spotify songs.

    Each insertion subdivides an existing edge with a track similar to both
    neighbors. The global bottleneck is optimized first, followed by local
    smoothness and artist diversity.
    """
    if target_length < 2:
        return None, {"error": "The requested route must contain at least two songs."}

    started_at = time.monotonic()
    route = [dict(node) for node in path]
    adjacency: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]] = {}
    resolver_cache: Dict[Tuple[str, str], Optional[Dict]] = {}
    _adjacency_for(route, adjacency, similarity_fetcher, similarity_limit)

    # Shortest-path searches can occasionally return more nodes than a small
    # request. Contract only where the new neighboring pair has an observed
    # similarity link, optimizing the resulting bottleneck at each removal.
    while len(route) > target_length:
        removals = []
        for index in range(1, len(route) - 1):
            bridge_score = _transition_similarity(route[index - 1], route[index + 1], adjacency)
            if bridge_score <= 0:
                continue
            candidate = route[:index] + route[index + 1:]
            scores = [
                _transition_similarity(candidate[pos], candidate[pos + 1], adjacency)
                for pos in range(len(candidate) - 1)
            ]
            removals.append((min(scores, default=0.0), sum(scores), index))

        if not removals:
            return None, {
                "error": (
                    f"Could not contract the discovered path to exactly "
                    f"{target_length} smooth songs."
                ),
                "built_length": len(route),
            }
        _, _, remove_index = max(removals)
        route.pop(remove_index)

    used_keys = {track_key(node) for node in route}
    used_spotify_ids = {
        spotify_id
        for spotify_id in (_spotify_id(node.get("_spotify")) for node in route)
        if spotify_id
    }
    batch_number = 0

    while len(route) < target_length:
        elapsed = time.monotonic() - started_at
        if elapsed >= max_seconds:
            return None, {
                "built_length": len(route),
                "timed_out": True,
                "error": (
                    f"Stopped after {max_seconds:.0f} seconds with {len(route)} of "
                    f"{target_length} tracks. Try again—the similarity cache will "
                    "make the next run faster."
                ),
            }

        remaining = target_length - len(route)
        desired_additions = min(remaining, max(1, len(route) - 1), 12)
        selected = []

        for discovery_round in range(3):
            insertions = _candidate_insertions(
                route,
                adjacency,
                used_keys,
                max_per_artist=None,
            )

            # Resolve a small set of strong alternatives for each edge. This
            # keeps recommendation quality while avoiding one serial Spotify
            # request per inserted song.
            candidate_pool = []
            options_per_edge: Counter = Counter()
            pooled_keys: Set[Tuple[str, str]] = set()
            max_pool_size = max(12, desired_additions * 3)
            for insertion in insertions:
                edge_index = insertion[1]
                candidate_key = track_key(insertion[2])
                if (
                    options_per_edge[edge_index] >= 3
                    or candidate_key in pooled_keys
                ):
                    continue
                candidate_pool.append(insertion)
                options_per_edge[edge_index] += 1
                pooled_keys.add(candidate_key)
                if len(candidate_pool) >= max_pool_size:
                    break

            _resolve_candidates_batch(
                candidate_pool,
                resolver_cache,
                spotify_resolver,
            )

            selected_edges: Set[int] = set()
            selected_keys: Set[Tuple[str, str]] = set()
            selected_spotify_ids: Set[str] = set()
            for rank, edge_index, candidate, left_score, right_score in candidate_pool:
                key = track_key(candidate)
                spotify_track = resolver_cache.get(key)
                spotify_track_id = _spotify_id(spotify_track)
                if (
                    edge_index in selected_edges
                    or key in selected_keys
                    or not spotify_track_id
                    or spotify_track_id in used_spotify_ids
                    or spotify_track_id in selected_spotify_ids
                ):
                    continue
                selected.append(
                    (rank, edge_index, dict(candidate), spotify_track, left_score, right_score)
                )
                selected_edges.add(edge_index)
                selected_keys.add(key)
                selected_spotify_ids.add(spotify_track_id)
                if len(selected) >= desired_additions:
                    break

            if selected:
                break

            _broaden_candidate_graph(
                route,
                adjacency,
                used_keys,
                similarity_fetcher,
                similarity_limit,
                candidates_per_endpoint=12 * (discovery_round + 1),
            )

        if not selected:
            return None, {
                "error": (
                    f"Could not find enough distinct Spotify tracks to complete "
                    f"this {target_length}-song route."
                ),
                "built_length": len(route),
            }

        new_nodes = []
        for _, edge_index, candidate, spotify_track, _, _ in sorted(
            selected,
            key=lambda item: item[1],
            reverse=True,
        ):
            candidate["_spotify"] = spotify_track
            route.insert(edge_index + 1, candidate)
            used_keys.add(track_key(candidate))
            used_spotify_ids.add(spotify_track["id"])
            new_nodes.append(candidate)

        # All newly inserted nodes are fetched in one parallel Last.fm round.
        _adjacency_for(new_nodes, adjacency, similarity_fetcher, similarity_limit)
        batch_number += 1

        transition_scores = [
            _transition_similarity(route[index], route[index + 1], adjacency)
            for index in range(len(route) - 1)
        ]
        weakest_so_far = min(transition_scores, default=1.0)
        if progress_callback:
            progress_callback({
                "type": "progress",
                "phase": "expanding",
                "message": f"Built {len(route)} of {target_length} tracks",
                "built_length": len(route),
                "target_length": target_length,
                "batch": batch_number,
                "elapsed_seconds": round(time.monotonic() - started_at, 1),
                "weakest_transition": round(weakest_so_far, 4),
            })

    transition_scores = [
        _transition_similarity(route[index], route[index + 1], adjacency)
        for index in range(len(route) - 1)
    ]
    metrics = {
        "weakest_transition": round(min(transition_scores, default=1.0), 4),
        "average_transition": round(
            sum(transition_scores) / len(transition_scores),
            4,
        ) if transition_scores else 1.0,
        "transition_scores": [round(score, 4) for score in transition_scores],
    }
    metrics["meets_smoothness_target"] = (
        metrics["weakest_transition"] >= MIN_FROG_TRANSITION
    )
    if not metrics["meets_smoothness_target"]:
        metrics["quality_warning"] = (
            f"Best route found; its weakest hop is "
            f"{metrics['weakest_transition']:.0%}, below the "
            f"{MIN_FROG_TRANSITION:.0%} smoothness target."
        )

    return route, metrics


def resolve_path_to_spotify(path: List[Dict]) -> List[Dict]:
    """
    Convert Last.fm track path to Spotify tracks.

    Args:
        path: List of dicts with 'artist' and 'name' from Last.fm

    Returns:
        List of Spotify track dicts with full metadata
    """
    spotify_tracks = []
    seen_ids: Set[str] = set()

    for track in path:
        spotify_track = resolve_to_spotify(track["artist"], track["name"])
        if spotify_track and spotify_track.get("id"):
            track_id = spotify_track["id"]
            if track_id not in seen_ids:
                seen_ids.add(track_id)
                spotify_tracks.append(spotify_track)

    return spotify_tracks


def sample_evenly(path: List, target_length: int) -> List:
    """
    Sample evenly spaced items from a path to reach target length.
    Always includes first and last items.
    """
    if len(path) <= target_length:
        return path

    if target_length <= 2:
        return [path[0], path[-1]]

    # Calculate step size
    step = (len(path) - 1) / (target_length - 1)

    result = []
    for i in range(target_length):
        idx = int(i * step)
        result.append(path[idx])

    # Ensure last item is included
    result[-1] = path[-1]

    return result


def _resolve_spine(
    path: List[Dict],
    start_spotify: Dict,
    end_spotify: Dict,
) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Resolve every skeleton node before it is counted toward route length."""
    if len(path) < 2:
        return None, "The route skeleton did not contain both endpoints."

    resolved: List[Dict] = []
    used_ids: Set[str] = set()
    for index, raw_node in enumerate(path):
        node = dict(raw_node)
        if index == 0:
            spotify_track = start_spotify
        elif index == len(path) - 1:
            spotify_track = end_spotify
        else:
            spotify_track = resolve_to_spotify(node["artist"], node["name"])

        spotify_track_id = _spotify_id(spotify_track)
        if not spotify_track_id:
            return None, (
                f"Could not find the bridge track {node['artist']} - "
                f"{node['name']} on Spotify."
            )
        if spotify_track_id in used_ids:
            return None, "The route skeleton contained duplicate Spotify tracks."

        node["_spotify"] = spotify_track
        used_ids.add(spotify_track_id)
        resolved.append(node)

    return resolved, None


def _build_exact_result(
    path: List[Dict],
    start_spotify: Dict,
    end_spotify: Dict,
    track_count: int,
    progress_callback: Optional[Callable[[Dict], None]] = None,
    max_seconds: float = 45.0,
) -> Dict:
    """Resolve, expand, score, and format an exact-length Frog route."""
    spine_length = len(path)
    spine, error = _resolve_spine(path, start_spotify, end_spotify)
    if not spine:
        return {
            "tracks": [],
            "path_length": 0,
            "sampled_length": 0,
            "requested_length": track_count,
            "success": False,
            "error": error or "Could not resolve the route skeleton on Spotify.",
        }

    exact_path, quality = expand_path_to_exact_length(
        spine,
        track_count,
        progress_callback=progress_callback,
        max_seconds=max_seconds,
    )
    if not exact_path:
        return {
            "tracks": [],
            "path_length": 0,
            "sampled_length": quality.get("built_length", len(spine)),
            "requested_length": track_count,
            "spine_length": spine_length,
            "success": False,
            "timed_out": quality.get("timed_out", False),
            "error": quality.get("error", "Could not build the requested route."),
        }

    scores = quality["transition_scores"]
    spotify_tracks = []
    for index, node in enumerate(exact_path):
        role = "start" if index == 0 else ("end" if index == len(exact_path) - 1 else "bridge")
        transition = None if index == 0 else scores[index - 1]
        spotify_tracks.append(
            format_track(
                node["_spotify"],
                index,
                role,
                transition_similarity=transition,
            )
        )

    return {
        "tracks": spotify_tracks,
        "path_length": len(exact_path),
        "sampled_length": len(spotify_tracks),
        "requested_length": track_count,
        "spine_length": spine_length,
        "weakest_transition": quality["weakest_transition"],
        "average_transition": quality["average_transition"],
        "meets_smoothness_target": quality["meets_smoothness_target"],
        "quality_warning": quality.get("quality_warning"),
        "success": len(spotify_tracks) == track_count,
    }


def generate_frog_playlist(
    start_track_id: str,
    end_track_id: str,
    track_count: int = 20,
) -> Dict:
    """
    Generate a playlist that transitions from start track to end track.

    Args:
        start_track_id: Spotify track ID for start
        end_track_id: Spotify track ID for end
        track_count: Target number of tracks in playlist

    Returns:
        Dict with:
        - tracks: List of track dicts with Spotify metadata
        - path_length: Original path length before sampling
        - iterations: Number of A* iterations used
        - success: Whether a path was found
    """
    # Get start and end track info from Spotify
    tracks_data = get_tracks_bulk([start_track_id, end_track_id])

    if len(tracks_data) < 2:
        return {
            "tracks": [],
            "path_length": 0,
            "iterations": 0,
            "success": False,
            "error": "Could not fetch start or end track from Spotify",
        }

    start_spotify = tracks_data[0]
    end_spotify = tracks_data[1]

    # Extract artist and track names for Last.fm lookup
    start = {
        "artist": start_spotify.get("artists", [{}])[0].get("name", ""),
        "name": start_spotify.get("name", ""),
        "spotify": start_spotify,
    }
    end = {
        "artist": end_spotify.get("artists", [{}])[0].get("name", ""),
        "name": end_spotify.get("name", ""),
        "spotify": end_spotify,
    }

    if not start["artist"] or not start["name"] or not end["artist"] or not end["name"]:
        return {
            "tracks": [],
            "path_length": 0,
            "iterations": 0,
            "success": False,
            "error": "Missing artist or track name",
        }

    # Use the same bounded, deduplicated search as the streaming endpoint so
    # API clients do not fall back to the old one-request-at-a-time traversal.
    path = None
    for event in astar_find_path_streaming(start, end):
        if event.get("type") == "result":
            path = event.get("path")

    if not path:
        return {
            "tracks": [],
            "path_length": 0,
            "iterations": 0,
            "success": False,
            "error": "No path found between tracks. They may be too different.",
        }

    return _build_exact_result(path, start_spotify, end_spotify, track_count)


def generate_frog_playlist_streaming(
    start_track_id: str,
    end_track_id: str,
    track_count: int = 20,
):
    """
    Generate a frog playlist with streaming progress updates.

    Yields progress events during A* search, then final result.
    """
    # Get start and end track info from Spotify
    tracks_data = get_tracks_bulk([start_track_id, end_track_id])

    if len(tracks_data) < 2:
        yield {
            "type": "error",
            "error": "Could not fetch start or end track from Spotify",
        }
        return

    start_spotify = tracks_data[0]
    end_spotify = tracks_data[1]

    # Extract artist and track names for Last.fm lookup
    start = {
        "artist": start_spotify.get("artists", [{}])[0].get("name", ""),
        "name": start_spotify.get("name", ""),
        "spotify": start_spotify,
    }
    end = {
        "artist": end_spotify.get("artists", [{}])[0].get("name", ""),
        "name": end_spotify.get("name", ""),
        "spotify": end_spotify,
    }

    if not start["artist"] or not start["name"] or not end["artist"] or not end["name"]:
        yield {
            "type": "error",
            "error": "Missing artist or track name",
        }
        return

    # Yield initial progress
    yield {
        "type": "progress",
        "phase": "init",
        "message": f"Finding path: {start['artist']} → {end['artist']}",
        "start_track": f"{start['artist']} - {start['name']}",
        "end_track": f"{end['artist']} - {end['name']}",
    }

    # Find path using A* with progress callback
    def progress_callback(iteration, visited, queue_size, best_h, current):
        return {
            "type": "progress",
            "phase": "search",
            "iteration": iteration,
            "visited": visited,
            "queue_size": queue_size,
            "best_h": best_h,
            "current_track": f"{current['artist'][:25]} - {current['name'][:30]}",
        }

    path = None
    for event in astar_find_path_streaming(start, end, progress_callback):
        if event.get("type") == "result":
            path = event.get("path")
        else:
            yield event

    if not path:
        yield {
            "type": "error",
            "error": "No path found between tracks. They may be too different.",
        }
        return

    spine_length = len(path)

    yield {
        "type": "progress",
        "phase": "expanding",
        "message": (
            f"Found a {spine_length}-song spine. Growing it into exactly "
            f"{track_count} tiny hops..."
        ),
    }

    expansion_events: queue.Queue = queue.Queue()
    expansion_budget = 35.0 if track_count <= 30 else 50.0

    # Route expansion performs blocking Spotify and Last.fm calls. Run it in a
    # worker so the SSE response can continue to report real batch progress.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _build_exact_result,
            path,
            start_spotify,
            end_spotify,
            track_count,
            expansion_events.put,
            expansion_budget,
        )
        last_heartbeat = time.monotonic()
        while not future.done():
            try:
                yield expansion_events.get(timeout=0.75)
                last_heartbeat = time.monotonic()
            except queue.Empty:
                if time.monotonic() - last_heartbeat >= 3.0:
                    yield {
                        "type": "progress",
                        "phase": "expanding",
                        "message": "Checking the smoothest available bridge tracks...",
                    }
                    last_heartbeat = time.monotonic()

        while not expansion_events.empty():
            yield expansion_events.get_nowait()
        result = future.result()

    if not result.get("success"):
        yield {
            "type": "error",
            "error": result.get("error", "Could not build the requested route."),
        }
        return

    yield {"type": "result", **result}


def astar_find_path_streaming(
    start: Dict,
    end: Dict,
    progress_callback=None,
    max_iterations: int = 500,
    max_seconds: float = 35.0,
):
    """
    Bidirectional search with PARALLEL API calls for speed.

    Uses batch expansion to fetch similar tracks for multiple nodes at once.
    Returns best path found within time/iteration limits.
    """
    start_time = time.monotonic()

    print(f"[BiA*] Starting parallel bidirectional search: {start['artist']} - {start['name']} → {end['artist']} - {end['name']}")

    yield {
        "type": "progress",
        "phase": "neighborhood",
        "message": "Initializing bidirectional search...",
    }

    start_key = track_key(start)
    end_key = track_key(end)

    if start_key == end_key:
        yield {"type": "result", "path": [start], "iterations": 0}
        return

    # Settings for speed vs coverage tradeoff
    SIMILAR_LIMIT = 30  # Tracks per node
    BATCH_SIZE = 10  # Expand 10 nodes in parallel per side (up to 20 API calls per batch)

    # Forward search state
    open_f = [(0, 0, start_key, start, [start])]
    visited_f: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    discovered_f: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {
        start_key: (0.0, [start])
    }
    best_g_f: Dict[Tuple[str, str], float] = {start_key: 0.0}
    counter_f = 0

    # Backward search state
    open_b = [(0, 0, end_key, end, [end])]
    visited_b: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    discovered_b: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {
        end_key: (0.0, [end])
    }
    best_g_b: Dict[Tuple[str, str], float] = {end_key: 0.0}
    counter_b = 0

    iterations = 0

    yield {
        "type": "progress",
        "phase": "neighborhood",
        "message": "Starting parallel search...",
        "neighborhood_1hop": 0,
        "neighborhood_2hop": 0,
    }

    while (open_f or open_b) and iterations < max_iterations:
        # Check time limit
        elapsed = time.monotonic() - start_time
        if elapsed > max_seconds:
            print(f"[BiA*] Time limit reached ({elapsed:.1f}s)")
            break

        iterations += 1

        # Collect nodes to expand in batch
        to_expand_f = []
        batch_seen_f: Set[Tuple[str, str]] = set()
        while open_f and len(to_expand_f) < BATCH_SIZE:
            g, _, key, data, path = heapq.heappop(open_f)
            if (
                key not in visited_f
                and key not in batch_seen_f
                and g <= best_g_f.get(key, float("inf"))
            ):
                batch_seen_f.add(key)
                to_expand_f.append((g, key, data, path))

        to_expand_b = []
        batch_seen_b: Set[Tuple[str, str]] = set()
        while open_b and len(to_expand_b) < BATCH_SIZE:
            g, _, key, data, path = heapq.heappop(open_b)
            if (
                key not in visited_b
                and key not in batch_seen_b
                and g <= best_g_b.get(key, float("inf"))
            ):
                batch_seen_b.add(key)
                to_expand_b.append((g, key, data, path))

        if not to_expand_f and not to_expand_b:
            break

        # Mark visited and check for meeting point BEFORE fetching neighbors
        for g, key, data, path in to_expand_f:
            visited_f[key] = (g, path)
            if key in discovered_b:
                _, path_b = discovered_b[key]
                complete_path = path[:-1] + list(reversed(path_b))
                print(f"[BiA*] Found path in {iterations} batches!")
                yield {"type": "result", "path": complete_path, "iterations": iterations}
                return

        for g, key, data, path in to_expand_b:
            visited_b[key] = (g, path)
            if key in discovered_f:
                _, path_f = discovered_f[key]
                complete_path = path_f[:-1] + list(reversed(path))
                print(f"[BiA*] Found path in {iterations} batches!")
                yield {"type": "result", "path": complete_path, "iterations": iterations}
                return

        # Fetch neighbors in PARALLEL
        tracks_to_fetch = []
        track_info = {}  # Map (artist, track) -> (direction, key, data, path)

        for _, key, data, path in to_expand_f:
            t = (data["artist"], data["name"])
            tracks_to_fetch.append(t)
            track_info[t] = ("f", key, data, path)

        for _, key, data, path in to_expand_b:
            t = (data["artist"], data["name"])
            tracks_to_fetch.append(t)
            track_info[t] = ("b", key, data, path)

        if tracks_to_fetch:
            # PARALLEL API CALLS (up to 20 concurrent)
            batch_results = get_similar_tracks_batch(tracks_to_fetch, limit=SIMILAR_LIMIT, max_workers=20)

            # Process results
            for track_tuple, similar in batch_results.items():
                direction, parent_key, parent_data, parent_path = track_info[track_tuple]
                parent_g = (
                    best_g_f[parent_key]
                    if direction == "f"
                    else best_g_b[parent_key]
                )

                for neighbor in similar:
                    neighbor_key = track_key(neighbor)
                    if not all(neighbor_key):
                        continue
                    edge_cost = 1 - neighbor["match"]
                    new_g = parent_g + edge_cost
                    new_path = parent_path + [neighbor]

                    if direction == "f":
                        if new_g >= best_g_f.get(neighbor_key, float("inf")):
                            continue
                        best_g_f[neighbor_key] = new_g
                        discovered_f[neighbor_key] = (new_g, new_path)
                        if neighbor_key in discovered_b:
                            _, path_b = discovered_b[neighbor_key]
                            complete_path = new_path[:-1] + list(reversed(path_b))
                            print(f"[BiA*] Frontiers met in {iterations} batches!")
                            yield {
                                "type": "result",
                                "path": complete_path,
                                "iterations": iterations,
                            }
                            return
                        counter_f += 1
                        heapq.heappush(
                            open_f,
                            (new_g, counter_f, neighbor_key, neighbor, new_path),
                        )
                    else:
                        if new_g >= best_g_b.get(neighbor_key, float("inf")):
                            continue
                        best_g_b[neighbor_key] = new_g
                        discovered_b[neighbor_key] = (new_g, new_path)
                        if neighbor_key in discovered_f:
                            _, path_f = discovered_f[neighbor_key]
                            complete_path = path_f[:-1] + list(reversed(new_path))
                            print(f"[BiA*] Frontiers met in {iterations} batches!")
                            yield {
                                "type": "result",
                                "path": complete_path,
                                "iterations": iterations,
                            }
                            return
                        counter_b += 1
                        heapq.heappush(
                            open_b,
                            (new_g, counter_b, neighbor_key, neighbor, new_path),
                        )

        # Progress update
        if progress_callback:
            total_visited = len(visited_f) + len(visited_b)
            total_queue = len(open_f) + len(open_b)
            progress_pct = min(0.9, total_visited / 100)
            current = (
                to_expand_f[0][2]
                if to_expand_f
                else (to_expand_b[0][2] if to_expand_b else start)
            )
            yield progress_callback(iterations, total_visited, total_queue, 1 - progress_pct, current)

    # No direct path found - try to find closest meeting point
    print(f"[BiA*] No direct path after {iterations} batches, checking for close approaches...")

    # Find any overlap between visited sets
    overlap = set(visited_f.keys()) & set(visited_b.keys())
    if overlap:
        # Find the overlap with minimum total cost
        best_meeting = None
        best_cost = float('inf')
        for key in overlap:
            g_f, path_f = visited_f[key]
            g_b, path_b = visited_b[key]
            cost = g_f + g_b
            if cost < best_cost:
                best_cost = cost
                best_meeting = (path_f, path_b)

        if best_meeting:
            path_f, path_b = best_meeting
            complete_path = path_f[:-1] + list(reversed(path_b))
            print(f"[BiA*] Found late meeting point! Path length: {len(complete_path)}")
            yield {"type": "result", "path": complete_path, "iterations": iterations}
            return

    # If still no path, find closest approach and try to bridge via a popular intermediate
    # Just return None for now - user can try different tracks
    print(f"[BiA*] NO PATH FOUND - genres may be too different")
    yield {"type": "result", "path": None, "iterations": iterations}


def format_track(
    spotify_track: Dict,
    position: int,
    role: str,
    transition_similarity: Optional[float] = None,
) -> Dict:
    """Format a Spotify track for the response."""
    album = spotify_track.get("album", {})
    images = album.get("images", [])

    return {
        "track_id": spotify_track.get("id"),
        "track": spotify_track.get("name"),
        "artist": ", ".join(a.get("name", "") for a in spotify_track.get("artists", [])),
        "album": album.get("name"),
        "image_url": images[0]["url"] if images else None,
        "preview_url": spotify_track.get("preview_url"),
        "spotify_url": spotify_track.get("external_urls", {}).get("spotify"),
        "position": position,
        "role": role,
        "transition_similarity": transition_similarity,
    }
