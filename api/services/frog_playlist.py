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

# The browser renders a sampled search trace, not the search engine's complete
# working set. Keeping that distinction explicit prevents a difficult search
# from accumulating tens of thousands of rich node/edge dictionaries in the
# streaming generator before the final SSE result is sent.
FROG_EXPLORATION_MAX_NODES = 600
FROG_EXPLORATION_MAX_EDGES = 1200

# Search entries retain complete paths so exact-route reconstruction remains
# simple and reliable. Bound both unique discovered states and the live
# frontier to keep an unusually disconnected pair from growing memory until
# the process is killed.
FROG_SEARCH_MAX_STATES_PER_DIRECTION = 3000
FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION = 1200

# Repair only needs a broad local neighborhood, not every candidate returned
# by three 100-track similarity lists.
FROG_REPAIR_MAX_CANDIDATES = 64


class _BoundedSearchFrontier:
    """A deterministic min-priority frontier with a hard live-entry limit."""

    def __init__(self, max_entries: int):
        self.max_entries = max(1, int(max_entries))
        self._entries: Dict[int, Tuple] = {}
        self._key_tokens: Dict[Tuple[str, str], int] = {}
        self._min_heap: List[Tuple[float, int]] = []
        self._max_heap: List[Tuple[float, int, int]] = []

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def _remove(self, token: int) -> None:
        entry = self._entries.pop(token, None)
        if entry is not None:
            self._key_tokens.pop(entry[2], None)

    def _compact_if_needed(self) -> None:
        # Eviction and score improvements leave lazy-deleted heap records.
        # Rebuild at a fixed threshold so the backing heaps are bounded too.
        heap_limit = self.max_entries * 2
        if len(self._min_heap) >= heap_limit:
            self._min_heap = [
                (entry[0], token)
                for token, entry in self._entries.items()
            ]
            heapq.heapify(self._min_heap)
        if len(self._max_heap) >= heap_limit:
            self._max_heap = [
                (-entry[0], -entry[1], token)
                for token, entry in self._entries.items()
            ]
            heapq.heapify(self._max_heap)

    def _worst_token(self) -> Optional[int]:
        while self._max_heap:
            _, _, token = self._max_heap[0]
            if token in self._entries:
                return token
            heapq.heappop(self._max_heap)
        return None

    def push(
        self,
        g_score: float,
        token: int,
        key: Tuple[str, str],
        data: Dict,
        path: List[Dict],
    ) -> bool:
        """Keep the best bounded set; return whether this entry survived."""
        entry = (g_score, token, key, data, path)
        previous_token = self._key_tokens.get(key)
        if previous_token is not None:
            previous = self._entries[previous_token]
            if (g_score, token) >= (previous[0], previous[1]):
                return False
            self._remove(previous_token)

        if len(self._entries) >= self.max_entries:
            worst_token = self._worst_token()
            if worst_token is None:
                return False
            worst = self._entries[worst_token]
            if (g_score, token) >= (worst[0], worst[1]):
                return False
            self._remove(worst_token)

        self._compact_if_needed()
        self._entries[token] = entry
        self._key_tokens[key] = token
        heapq.heappush(self._min_heap, (g_score, token))
        heapq.heappush(self._max_heap, (-g_score, -token, token))
        return True

    def pop(self) -> Tuple:
        """Pop the best live entry, skipping lazily deleted heap records."""
        while self._min_heap:
            _, token = heapq.heappop(self._min_heap)
            entry = self._entries.get(token)
            if entry is None:
                continue
            self._remove(token)
            return entry
        raise IndexError("pop from an empty search frontier")


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
    print("[BiA*] Starting bidirectional search")

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


def graph_node_id(track: Dict) -> str:
    """Stable browser-safe identity shared by search and final route graphs."""
    return "::".join(track_key(track))


def _exploration_node_priority(node: Dict, preferred_node_ids: Set[str]) -> Tuple:
    """Stable retention order for a bounded Frog search trace."""
    node_id = str(node.get("id", ""))
    state = str(node.get("state", ""))
    direction = str(node.get("direction", ""))
    if direction == "route" or node.get("route_position") is not None:
        tier = 0
    elif state in {"start", "end"}:
        tier = 1
    elif state == "meeting":
        tier = 2
    elif node_id in preferred_node_ids and state == "expanded":
        tier = 3
    elif node_id in preferred_node_ids:
        tier = 4
    elif state == "expanded":
        tier = 5
    else:
        tier = 6

    route_position = node.get("route_position")
    if not isinstance(route_position, (int, float)):
        route_position = float("inf")
    depth = node.get("depth")
    if not isinstance(depth, (int, float)):
        depth = float("inf")
    return (tier, route_position, depth, node_id)


def _budget_exploration_graph(
    nodes: List[Dict],
    edges: List[Dict],
    *,
    max_nodes: int = FROG_EXPLORATION_MAX_NODES,
    max_edges: int = FROG_EXPLORATION_MAX_EDGES,
    preferred_node_ids: Optional[Set[str]] = None,
) -> Dict:
    """
    Return a deterministic, referentially valid sample of an exploration graph.

    Route/end-point/meeting nodes win retention first, followed by the active
    frontier and expanded nodes. Edges are retained only when both endpoints
    survive, so a capped payload never contains dangling links.
    """
    max_nodes = max(0, int(max_nodes))
    max_edges = max(0, int(max_edges))
    preferred = set(preferred_node_ids or ())
    node_map = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    edge_map = {
        str(edge["id"]): edge
        for edge in edges
        if isinstance(edge, dict) and edge.get("id")
    }

    ordered_nodes = sorted(
        node_map.values(),
        key=lambda node: _exploration_node_priority(node, preferred),
    )
    retained_nodes = ordered_nodes[:max_nodes]
    retained_node_ids = {str(node["id"]) for node in retained_nodes}
    node_rank = {
        str(node["id"]): index
        for index, node in enumerate(retained_nodes)
    }

    valid_edges = [
        edge
        for edge in edge_map.values()
        if str(edge.get("source", "")) in retained_node_ids
        and str(edge.get("target", "")) in retained_node_ids
    ]

    def edge_priority(edge: Dict) -> Tuple:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        kind = str(edge.get("kind", ""))
        direction = str(edge.get("direction", ""))
        if kind == "route" or direction == "route":
            tier = 0
        elif source in preferred or target in preferred:
            tier = 1
        else:
            tier = 2
        try:
            similarity = float(edge.get("similarity", 0.0))
        except (TypeError, ValueError):
            similarity = 0.0
        return (
            tier,
            max(node_rank[source], node_rank[target]),
            -similarity,
            str(edge.get("id", "")),
        )

    retained_edges = sorted(valid_edges, key=edge_priority)[:max_edges]
    return {
        "nodes": retained_nodes,
        "edges": retained_edges,
        "retained_nodes": len(retained_nodes),
        "retained_edges": len(retained_edges),
        "omitted_nodes": max(0, len(node_map) - len(retained_nodes)),
        "omitted_edges": max(0, len(edge_map) - len(retained_edges)),
        "node_limit": max_nodes,
        "edge_limit": max_edges,
        "truncated": (
            len(retained_nodes) < len(node_map)
            or len(retained_edges) < len(edge_map)
        ),
    }


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


def _observed_transition_similarity(
    left: Dict,
    right: Dict,
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
) -> float:
    """Similarity supported by an explicit Last.fm edge in either direction."""
    return _transition_evidence(left, right, cache)["observed_similarity"]


def _transition_evidence(
    left: Dict,
    right: Dict,
    cache: Dict[Tuple[str, str], Dict[Tuple[str, str], Tuple[Dict, float]]],
) -> Dict:
    """
    Describe the directional Last.fm evidence for one transition.

    Last.fm similarity lists are directional and can disagree. The observed
    similarity preserves the existing optimistic ``max`` behavior for display,
    while the conservative similarity uses the weaker directional observation
    whenever both directions are present.
    """
    left_key = track_key(left)
    right_key = track_key(right)
    left_to_right = None
    right_to_left = None

    if right_key in cache.get(left_key, {}):
        left_to_right = cache[left_key][right_key][1]
    if left_key in cache.get(right_key, {}):
        right_to_left = cache[right_key][left_key][1]

    observations = [
        score
        for score in (left_to_right, right_to_left)
        if score is not None
    ]
    return {
        "left_to_right": left_to_right,
        "right_to_left": right_to_left,
        "direction_count": len(observations),
        "bidirectional": len(observations) == 2,
        "observed_similarity": max(observations, default=0.0),
        "conservative_similarity": min(observations, default=0.0),
    }


def _format_alternative_edge_evidence(
    evidence: Dict,
    *,
    forward_label: str,
    reverse_label: str,
) -> Dict:
    """Return compact, JSON-safe evidence without implying audio analysis."""
    observations = []
    if evidence["left_to_right"] is not None:
        observations.append({
            "direction": forward_label,
            "similarity": round(evidence["left_to_right"], 4),
        })
    if evidence["right_to_left"] is not None:
        observations.append({
            "direction": reverse_label,
            "similarity": round(evidence["right_to_left"], 4),
        })

    return {
        "observations": observations,
        "direction_count": evidence["direction_count"],
        "bidirectional": evidence["bidirectional"],
        "observed_similarity": round(evidence["observed_similarity"], 4),
        "conservative_similarity": round(
            evidence["conservative_similarity"],
            4,
        ),
    }


def _alternative_reason(
    left_conservative: float,
    right_conservative: float,
    bidirectional_hops: int,
) -> str:
    """Explain the ranking using only explicit Last.fm graph evidence."""
    if abs(left_conservative - right_conservative) <= 0.01:
        limiting = "the two sides are balanced"
        bottleneck = min(left_conservative, right_conservative)
    elif left_conservative < right_conservative:
        limiting = "the left hop is the limiting side"
        bottleneck = left_conservative
    else:
        limiting = "the right hop is the limiting side"
        bottleneck = right_conservative

    if bidirectional_hops == 2:
        support = "both hops were observed in both directions"
    elif bidirectional_hops == 1:
        support = (
            "one hop was observed in both directions and the other in one "
            "direction"
        )
    else:
        support = "each hop has one observed direction"

    return (
        "Explicit Last.fm links connect this track to both neighbors; "
        f"{limiting} at a conservative {round(bottleneck * 100)}%. "
        f"Confidence reflects that {support}."
    )


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
    route_graph_nodes = []
    route_graph_edges = []
    for index, node in enumerate(exact_path):
        role = "start" if index == 0 else ("end" if index == len(exact_path) - 1 else "bridge")
        transition = None if index == 0 else scores[index - 1]
        formatted = format_track(
            node["_spotify"],
            index,
            role,
            transition_similarity=transition,
        )
        spotify_tracks.append(formatted)
        route_graph_nodes.append({
            "id": graph_node_id(node),
            "artist": formatted["artist"],
            "track": formatted["track"],
            "direction": "route",
            "depth": index,
            "state": role,
            "route_position": index,
            "track_id": formatted["track_id"],
            "image_url": formatted["image_url"],
        })
        if index:
            source_id = graph_node_id(exact_path[index - 1])
            target_id = graph_node_id(node)
            route_graph_edges.append({
                "id": f"route:{source_id}>{target_id}",
                "source": source_id,
                "target": target_id,
                "similarity": transition,
                "direction": "route",
                "kind": "route",
            })

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
        "exploration": {
            "nodes": route_graph_nodes,
            "edges": route_graph_edges,
        },
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
    search_limited = False
    for event in astar_find_path_streaming(start, end):
        if event.get("type") == "result":
            path = event.get("path")
            search_limited = bool(event.get("limited_by_budget", False))

    if not path:
        return {
            "tracks": [],
            "path_length": 0,
            "iterations": 0,
            "success": False,
            "error": (
                "Search reached its safe exploration limit. Try closer tracks."
                if search_limited
                else "No path found between tracks. They may be too different."
            ),
            "search_limited": search_limited,
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
    def progress_callback(
        iteration,
        visited,
        queue_size,
        best_h,
        current,
        exploration,
    ):
        return {
            "type": "progress",
            "phase": "search",
            "iteration": iteration,
            "visited": visited,
            "queue_size": queue_size,
            "best_h": best_h,
            "current_track": f"{current['artist'][:25]} - {current['name'][:30]}",
            "exploration": exploration,
        }

    path = None
    search_limited = False
    exploration_nodes: Dict[str, Dict] = {}
    exploration_edges: Dict[str, Dict] = {}
    preferred_search_nodes = {
        graph_node_id(start),
        graph_node_id(end),
    }
    search_total_nodes = 0
    search_total_edges = 0
    for event in astar_find_path_streaming(start, end, progress_callback):
        if event.get("type") == "result":
            path = event.get("path")
            search_limited = bool(event.get("limited_by_budget", False))
        else:
            exploration = event.get("exploration", {})
            search_total_nodes = max(
                search_total_nodes,
                int(exploration.get("total_nodes", 0) or 0),
            )
            search_total_edges = max(
                search_total_edges,
                int(exploration.get("total_edges", 0) or 0),
            )
            focus_node_id = exploration.get("focus_node_id")
            active_preferred = set(preferred_search_nodes)
            if focus_node_id:
                active_preferred.add(str(focus_node_id))
            active_preferred.update(
                str(node["id"])
                for node in exploration.get("nodes", [])
                if node.get("id")
            )
            bounded = _budget_exploration_graph(
                [
                    *exploration_nodes.values(),
                    *exploration.get("nodes", []),
                ],
                [
                    *exploration_edges.values(),
                    *exploration.get("edges", []),
                ],
                preferred_node_ids=active_preferred,
            )
            exploration_nodes = {
                node["id"]: node
                for node in bounded["nodes"]
            }
            exploration_edges = {
                edge["id"]: edge
                for edge in bounded["edges"]
            }
            # The progress event remains a compact delta for existing clients.
            # These fields disclose the total search size and the bounded
            # server-side trace without forcing clients to render either one.
            exploration["retained_nodes"] = len(exploration_nodes)
            exploration["retained_edges"] = len(exploration_edges)
            exploration["node_limit"] = FROG_EXPLORATION_MAX_NODES
            exploration["edge_limit"] = FROG_EXPLORATION_MAX_EDGES
            exploration["budget_truncated"] = bounded["truncated"]
            yield event

    if not path:
        yield {
            "type": "error",
            "error": (
                "Search reached its safe exploration limit. Try closer tracks."
                if search_limited
                else "No path found between tracks. They may be too different."
            ),
            "search_limited": search_limited,
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

    route_exploration = result.get("exploration", {})
    search_node_ids = set(exploration_nodes)
    search_edge_ids = set(exploration_edges)
    route_node_ids = {
        str(node["id"])
        for node in route_exploration.get("nodes", [])
        if node.get("id")
    }
    bounded = _budget_exploration_graph(
        [
            *exploration_nodes.values(),
            *route_exploration.get("nodes", []),
        ],
        [
            *exploration_edges.values(),
            *route_exploration.get("edges", []),
        ],
        preferred_node_ids=preferred_search_nodes | route_node_ids,
    )
    final_node_ids = {
        str(node["id"])
        for node in bounded["nodes"]
        if node.get("id")
    }
    final_edge_ids = {
        str(edge["id"])
        for edge in bounded["edges"]
        if edge.get("id")
    }
    # Route nodes are prioritized when the final mixed graph is sampled, so
    # count search IDs only after that last budget pass. Computing this before
    # the merge understated omissions whenever route nodes displaced search
    # nodes at the cap.
    retained_search_nodes = len(search_node_ids & final_node_ids)
    retained_search_edges = len(search_edge_ids & final_edge_ids)
    result["exploration"] = {
        **bounded,
        # Totals deliberately describe the search. The exact-length route is
        # reported separately because route expansion can add songs that were
        # never part of the original bidirectional frontier.
        "total_nodes": max(search_total_nodes, retained_search_nodes),
        "total_edges": max(search_total_edges, retained_search_edges),
        "totals_scope": "search",
        "route_nodes": len(route_exploration.get("nodes", [])),
        "route_edges": len(route_exploration.get("edges", [])),
        "retained_search_nodes": retained_search_nodes,
        "retained_search_edges": retained_search_edges,
        "omitted_search_nodes": max(
            0,
            search_total_nodes - retained_search_nodes,
        ),
        "omitted_search_edges": max(
            0,
            search_total_edges - retained_search_edges,
        ),
        "sampled": (
            search_total_nodes > retained_search_nodes
            or search_total_edges > retained_search_edges
        ),
    }

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

    print("[BiA*] Starting parallel bidirectional search")

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
    open_f = _BoundedSearchFrontier(
        FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION,
    )
    open_f.push(0.0, 0, start_key, start, [start])
    visited_f: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    discovered_f: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {
        start_key: (0.0, [start])
    }
    best_g_f: Dict[Tuple[str, str], float] = {start_key: 0.0}
    counter_f = 0

    # Backward search state
    open_b = _BoundedSearchFrontier(
        FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION,
    )
    open_b.push(0.0, 0, end_key, end, [end])
    visited_b: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    discovered_b: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {
        end_key: (0.0, [end])
    }
    best_g_b: Dict[Tuple[str, str], float] = {end_key: 0.0}
    counter_b = 0
    sampled_link_count = 0
    state_budget_reached = False
    frontier_rejections = 0

    iterations = 0

    yield {
        "type": "progress",
        "phase": "neighborhood",
        "message": "Starting parallel search...",
        "neighborhood_1hop": 0,
        "neighborhood_2hop": 0,
    }

    def make_progress_event(current, graph_nodes, graph_edges):
        if not progress_callback:
            return None
        total_visited = len(visited_f) + len(visited_b)
        total_queue = len(open_f) + len(open_b)
        total_discovered = len(set(discovered_f).union(discovered_b))
        progress_pct = min(0.9, total_visited / 100)
        return progress_callback(
            iterations,
            total_visited,
            total_queue,
            1 - progress_pct,
            current,
            {
                "nodes": list(graph_nodes.values()),
                "edges": list(graph_edges.values()),
                "total_nodes": total_discovered,
                "total_edges": sampled_link_count,
                "totals_scope": "search",
                "focus_node_id": graph_node_id(current),
            },
        )

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
            g, _, key, data, path = open_f.pop()
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
            g, _, key, data, path = open_b.pop()
            if (
                key not in visited_b
                and key not in batch_seen_b
                and g <= best_g_b.get(key, float("inf"))
            ):
                batch_seen_b.add(key)
                to_expand_b.append((g, key, data, path))

        if not to_expand_f and not to_expand_b:
            break

        graph_nodes: Dict[str, Dict] = {}
        graph_edges: Dict[str, Dict] = {}

        for _, _, data, path in to_expand_f:
            node_id = graph_node_id(data)
            graph_nodes[node_id] = {
                "id": node_id,
                "artist": data["artist"],
                "track": data["name"],
                "direction": "forward",
                "depth": len(path) - 1,
                "state": "expanded",
            }
        for _, _, data, path in to_expand_b:
            node_id = graph_node_id(data)
            graph_nodes[node_id] = {
                "id": node_id,
                "artist": data["artist"],
                "track": data["name"],
                "direction": "backward",
                "depth": len(path) - 1,
                "state": "expanded",
            }

        # Mark visited and check for meeting point BEFORE fetching neighbors
        for g, key, data, path in to_expand_f:
            visited_f[key] = (g, path)
            if key in discovered_b:
                _, path_b = discovered_b[key]
                complete_path = path[:-1] + list(reversed(path_b))
                print(f"[BiA*] Found path in {iterations} batches!")
                graph_nodes[graph_node_id(data)]["state"] = "meeting"
                progress_event = make_progress_event(data, graph_nodes, graph_edges)
                if progress_event:
                    yield progress_event
                yield {"type": "result", "path": complete_path, "iterations": iterations}
                return

        for g, key, data, path in to_expand_b:
            visited_b[key] = (g, path)
            if key in discovered_f:
                _, path_f = discovered_f[key]
                complete_path = path_f[:-1] + list(reversed(path))
                print(f"[BiA*] Found path in {iterations} batches!")
                graph_nodes[graph_node_id(data)]["state"] = "meeting"
                progress_event = make_progress_event(data, graph_nodes, graph_edges)
                if progress_event:
                    yield progress_event
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
                graph_direction = "forward" if direction == "f" else "backward"
                parent_id = graph_node_id(parent_data)
                parent_depth = len(parent_path) - 1
                parent_g = (
                    best_g_f[parent_key]
                    if direction == "f"
                    else best_g_b[parent_key]
                )

                # Respect the declared branching limit even if a test double or
                # upstream client returns more than requested.
                for neighbor_index, neighbor in enumerate(similar[:SIMILAR_LIMIT]):
                    neighbor_key = track_key(neighbor)
                    if not all(neighbor_key):
                        continue
                    neighbor_id = graph_node_id(neighbor)
                    if neighbor_index < 5:
                        graph_nodes[neighbor_id] = {
                            "id": neighbor_id,
                            "artist": neighbor["artist"],
                            "track": neighbor["name"],
                            "direction": graph_direction,
                            "depth": parent_depth + 1,
                            "state": "discovered",
                        }
                        edge_id = f"{graph_direction}:{parent_id}>{neighbor_id}"
                        if edge_id not in graph_edges:
                            # A parent is expanded at most once per direction,
                            # so directional edge IDs are globally unique.
                            sampled_link_count += 1
                        graph_edges[edge_id] = {
                            "id": edge_id,
                            "source": parent_id,
                            "target": neighbor_id,
                            "similarity": round(
                                float(neighbor.get("match", 0.0)),
                                4,
                            ),
                            "direction": graph_direction,
                            "kind": "search",
                        }
                    edge_cost = 1 - neighbor["match"]
                    new_g = parent_g + edge_cost
                    new_path = parent_path + [neighbor]

                    if direction == "f":
                        if neighbor_key in visited_f:
                            continue
                        if new_g >= best_g_f.get(neighbor_key, float("inf")):
                            continue
                        is_new_discovery = neighbor_key not in discovered_f
                        if neighbor_key in discovered_b:
                            _, path_b = discovered_b[neighbor_key]
                            complete_path = new_path[:-1] + list(reversed(path_b))
                            print(f"[BiA*] Frontiers met in {iterations} batches!")
                            graph_nodes.setdefault(
                                neighbor_id,
                                {
                                    "id": neighbor_id,
                                    "artist": neighbor["artist"],
                                    "track": neighbor["name"],
                                    "direction": graph_direction,
                                    "depth": parent_depth + 1,
                                },
                            )["state"] = "meeting"
                            progress_event = make_progress_event(
                                neighbor,
                                graph_nodes,
                                graph_edges,
                            )
                            if progress_event:
                                yield progress_event
                            yield {
                                "type": "result",
                                "path": complete_path,
                                "iterations": iterations,
                            }
                            return
                        if (
                            is_new_discovery
                            and len(discovered_f)
                            >= FROG_SEARCH_MAX_STATES_PER_DIRECTION
                        ):
                            state_budget_reached = True
                            continue
                        best_g_f[neighbor_key] = new_g
                        discovered_f[neighbor_key] = (new_g, new_path)
                        counter_f += 1
                        if not open_f.push(
                            new_g,
                            counter_f,
                            neighbor_key,
                            neighbor,
                            new_path,
                        ):
                            frontier_rejections += 1
                    else:
                        if neighbor_key in visited_b:
                            continue
                        if new_g >= best_g_b.get(neighbor_key, float("inf")):
                            continue
                        is_new_discovery = neighbor_key not in discovered_b
                        if neighbor_key in discovered_f:
                            _, path_f = discovered_f[neighbor_key]
                            complete_path = path_f[:-1] + list(reversed(new_path))
                            print(f"[BiA*] Frontiers met in {iterations} batches!")
                            graph_nodes.setdefault(
                                neighbor_id,
                                {
                                    "id": neighbor_id,
                                    "artist": neighbor["artist"],
                                    "track": neighbor["name"],
                                    "direction": graph_direction,
                                    "depth": parent_depth + 1,
                                },
                            )["state"] = "meeting"
                            progress_event = make_progress_event(
                                neighbor,
                                graph_nodes,
                                graph_edges,
                            )
                            if progress_event:
                                yield progress_event
                            yield {
                                "type": "result",
                                "path": complete_path,
                                "iterations": iterations,
                            }
                            return
                        if (
                            is_new_discovery
                            and len(discovered_b)
                            >= FROG_SEARCH_MAX_STATES_PER_DIRECTION
                        ):
                            state_budget_reached = True
                            continue
                        best_g_b[neighbor_key] = new_g
                        discovered_b[neighbor_key] = (new_g, new_path)
                        counter_b += 1
                        if not open_b.push(
                            new_g,
                            counter_b,
                            neighbor_key,
                            neighbor,
                            new_path,
                        ):
                            frontier_rejections += 1

        # Progress update
        if progress_callback:
            current = (
                to_expand_f[0][2]
                if to_expand_f
                else (to_expand_b[0][2] if to_expand_b else start)
            )
            progress_event = make_progress_event(current, graph_nodes, graph_edges)
            if progress_event:
                yield progress_event

    # No direct path found - try to find closest meeting point
    print(f"[BiA*] No direct path after {iterations} batches, checking for close approaches...")

    # A time/iteration/budget stop can still use any complete connection
    # already retained on both sides, even when one side had not expanded it.
    overlap = set(discovered_f) & set(discovered_b)
    if overlap:
        # Find the overlap with minimum total cost
        best_meeting = None
        best_cost = float('inf')
        for key in overlap:
            g_f, path_f = discovered_f[key]
            g_b, path_b = discovered_b[key]
            cost = g_f + g_b
            if cost < best_cost:
                best_cost = cost
                best_meeting = (path_f, path_b)

        if best_meeting:
            path_f, path_b = best_meeting
            complete_path = path_f[:-1] + list(reversed(path_b))
            print(f"[BiA*] Found late meeting point! Path length: {len(complete_path)}")
            yield {
                "type": "result",
                "path": complete_path,
                "iterations": iterations,
                "limited_by_budget": (
                    state_budget_reached or frontier_rejections > 0
                ),
            }
            return

    # If still no path, find closest approach and try to bridge via a popular intermediate
    # Just return None for now - user can try different tracks
    print(f"[BiA*] NO PATH FOUND - genres may be too different")
    yield {
        "type": "result",
        "path": None,
        "iterations": iterations,
        "limited_by_budget": (
            state_budget_reached or frontier_rejections > 0
        ),
        "retained_states": len(set(discovered_f).union(discovered_b)),
        "frontier_rejections": frontier_rejections,
    }


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


def get_frog_alternatives(
    track_ids: List[str],
    position: int,
    limit: int = 8,
    *,
    current_left_similarity: Optional[float] = None,
    current_right_similarity: Optional[float] = None,
    track_fetcher: Callable[[List[str]], List[Dict]] = get_tracks_bulk,
    spotify_resolver: Callable[[str, str], Optional[Dict]] = resolve_to_spotify,
    similarity_fetcher: Callable = get_similar_tracks_batch,
) -> Dict:
    """
    Find Spotify replacements that fit both neighbors of one route position.

    Endpoints are immutable. Candidates are ranked by the weaker directional
    observation on their weaker adjacent edge, so a strong one-way Last.fm
    link cannot hide either a weak reverse observation or a weak second hop.
    """
    if len(track_ids) < 3:
        raise ValueError("A Frog route needs at least three tracks.")
    if position <= 0 or position >= len(track_ids) - 1:
        raise ValueError("Only bridge tracks can be replaced.")
    if limit < 1 or limit > 16:
        raise ValueError("Alternative limit must be between 1 and 16.")

    fetched = track_fetcher(track_ids)
    fetched_by_id = {
        track.get("id"): track
        for track in fetched
        if track and track.get("id")
    }
    if any(track_id not in fetched_by_id for track_id in track_ids):
        raise ValueError("Could not load every track in the current route.")

    route = []
    for track_id in track_ids:
        spotify_track = fetched_by_id[track_id]
        route.append({
            "artist": spotify_track.get("artists", [{}])[0].get("name", ""),
            "name": spotify_track.get("name", ""),
            "_spotify": spotify_track,
        })

    left = route[position - 1]
    current = route[position]
    right = route[position + 1]
    adjacency: Dict[
        Tuple[str, str],
        Dict[Tuple[str, str], Tuple[Dict, float]],
    ] = {}
    _adjacency_for([left, current, right], adjacency, similarity_fetcher, 100)

    used_keys = {track_key(node) for node in route}
    used_spotify_ids = set(track_ids)
    candidate_by_key: Dict[Tuple[str, str], Dict] = {}
    initial_strength: Dict[Tuple[str, str], float] = {}

    for endpoint in (left, current, right):
        for key, (candidate, score) in adjacency.get(track_key(endpoint), {}).items():
            if key in used_keys:
                continue
            candidate_by_key[key] = candidate
            initial_strength[key] = max(initial_strength.get(key, 0.0), score)

    candidate_nodes = sorted(
        candidate_by_key.values(),
        key=lambda node: initial_strength.get(track_key(node), 0.0),
        reverse=True,
    )[:FROG_REPAIR_MAX_CANDIDATES]
    _adjacency_for(candidate_nodes, adjacency, similarity_fetcher, 100)

    scored_candidates = []
    for candidate in candidate_nodes:
        left_evidence = _transition_evidence(left, candidate, adjacency)
        right_evidence = _transition_evidence(candidate, right, adjacency)
        left_score = left_evidence["observed_similarity"]
        right_score = right_evidence["observed_similarity"]
        if left_score <= 0 or right_score <= 0:
            continue

        conservative_left = left_evidence["conservative_similarity"]
        conservative_right = right_evidence["conservative_similarity"]
        conservative_bottleneck = min(conservative_left, conservative_right)
        confidence_score = (
            left_evidence["direction_count"] + right_evidence["direction_count"]
        ) / 4
        scored_candidates.append(
            (
                (
                    conservative_bottleneck,
                    confidence_score,
                    (conservative_left + conservative_right) / 2,
                    min(left_score, right_score),
                    (left_score + right_score) / 2,
                ),
                position,
                candidate,
                left_score,
                right_score,
            )
        )

    scored_candidates.sort(key=lambda item: item[0], reverse=True)
    resolver_cache: Dict[Tuple[str, str], Optional[Dict]] = {}
    _resolve_candidates_batch(
        scored_candidates[: max(limit * 4, 16)],
        resolver_cache,
        spotify_resolver,
    )

    current_left_evidence = _transition_evidence(left, current, adjacency)
    current_right_evidence = _transition_evidence(current, right, adjacency)
    observed_current_left = current_left_evidence["observed_similarity"]
    observed_current_right = current_right_evidence["observed_similarity"]
    current_left = (
        max(0.0, min(1.0, float(current_left_similarity)))
        if current_left_similarity is not None
        else observed_current_left
    )
    current_right = (
        max(0.0, min(1.0, float(current_right_similarity)))
        if current_right_similarity is not None
        else observed_current_right
    )
    current_floor = min(current_left, current_right)
    # A route transition can come from the path search's recorded one-way edge
    # even when re-querying normalized Spotify names does not reproduce that
    # edge. In that case the caller's displayed route score is still observed
    # evidence; use it instead of inventing a 0% conservative baseline.
    conservative_current_left = (
        current_left_evidence["conservative_similarity"]
        if current_left_evidence["direction_count"]
        else current_left
    )
    conservative_current_right = (
        current_right_evidence["conservative_similarity"]
        if current_right_evidence["direction_count"]
        else current_right
    )
    conservative_current_floor = min(
        conservative_current_left,
        conservative_current_right,
    )
    alternatives = []

    for _, _, candidate, left_score, right_score in scored_candidates:
        spotify_track = resolver_cache.get(track_key(candidate))
        spotify_track_id = _spotify_id(spotify_track)
        if not spotify_track_id or spotify_track_id in used_spotify_ids:
            continue

        left_evidence = _transition_evidence(left, candidate, adjacency)
        right_evidence = _transition_evidence(candidate, right, adjacency)
        bottleneck = min(left_score, right_score)
        conservative_left = left_evidence["conservative_similarity"]
        conservative_right = right_evidence["conservative_similarity"]
        conservative_bottleneck = min(conservative_left, conservative_right)
        direction_count = (
            left_evidence["direction_count"] + right_evidence["direction_count"]
        )
        confidence_score = direction_count / 4
        confidence_level = (
            "high"
            if direction_count == 4
            else ("medium" if direction_count == 3 else "limited")
        )
        bidirectional_hops = int(left_evidence["bidirectional"]) + int(
            right_evidence["bidirectional"]
        )
        formatted = format_track(
            spotify_track,
            position,
            "bridge",
            transition_similarity=round(left_score, 4),
        )
        alternatives.append({
            "track": formatted,
            "left_similarity": round(left_score, 4),
            "right_similarity": round(right_score, 4),
            "bottleneck_similarity": round(bottleneck, 4),
            "average_similarity": round((left_score + right_score) / 2, 4),
            "improvement": round(bottleneck - current_floor, 4),
            "ranking_score": round(conservative_bottleneck, 4),
            "conservative_improvement": round(
                conservative_bottleneck - conservative_current_floor,
                4,
            ),
            "confidence": {
                "level": confidence_level,
                "score": round(confidence_score, 2),
                "basis": (
                    "share_of_four_possible_directional_lastfm_links_observed"
                ),
            },
            "reason": _alternative_reason(
                conservative_left,
                conservative_right,
                bidirectional_hops,
            ),
            "evidence": {
                "source": "lastfm_track_similarity",
                "both_neighbors_linked": True,
                "left_edge": _format_alternative_edge_evidence(
                    left_evidence,
                    forward_label="left_neighbor_to_candidate",
                    reverse_label="candidate_to_left_neighbor",
                ),
                "right_edge": _format_alternative_edge_evidence(
                    right_evidence,
                    forward_label="candidate_to_right_neighbor",
                    reverse_label="right_neighbor_to_candidate",
                ),
                "ranking_basis": (
                    "weakest_directional_observation_on_the_weaker_neighbor_hop"
                ),
            },
        })
        used_spotify_ids.add(spotify_track_id)
        if len(alternatives) >= limit:
            break

    return {
        "position": position,
        "left_track": format_track(
            left["_spotify"],
            position - 1,
            "start" if position - 1 == 0 else "bridge",
        ),
        "current_track": format_track(
            current["_spotify"],
            position,
            "bridge",
            transition_similarity=round(current_left, 4),
        ),
        "right_track": format_track(
            right["_spotify"],
            position + 1,
            "end" if position + 1 == len(route) - 1 else "bridge",
            transition_similarity=round(current_right, 4),
        ),
        "current_bottleneck": round(current_floor, 4),
        "current_conservative_bottleneck": round(
            conservative_current_floor,
            4,
        ),
        "alternatives": alternatives,
    }
