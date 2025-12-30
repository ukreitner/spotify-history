"""
Boiling the Frog Playlist Generator.

Creates a playlist that smoothly transitions from one track to another
using A* pathfinding over Last.fm's track similarity graph.
"""

import heapq
from typing import List, Dict, Optional, Set, Tuple
from ..lastfm_client import get_similar_tracks, get_similar_tracks_batch
from ..spotify_client import search_tracks_advanced, get_tracks_bulk


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

    # Find best match (artist name should match)
    artist_lower = artist.lower()
    for r in results:
        track_artists = [a.get("name", "").lower() for a in r.get("artists", [])]
        if any(artist_lower in a or a in artist_lower for a in track_artists):
            return r

    # Fallback to first result if no exact artist match
    return results[0] if results else None


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

    # Find path using A*
    path = astar_find_path(start, end)

    if not path:
        return {
            "tracks": [],
            "path_length": 0,
            "iterations": 0,
            "success": False,
            "error": "No path found between tracks. They may be too different.",
        }

    original_length = len(path)

    # Sample if path is longer than target
    if len(path) > track_count:
        path = sample_evenly(path, track_count)

    # Resolve to Spotify tracks
    # First and last should use the original Spotify data we already have
    spotify_tracks = []

    # Add start track
    spotify_tracks.append(format_track(start_spotify, 0, "start"))

    # Resolve middle tracks
    if len(path) > 2:
        middle_path = path[1:-1]
        for i, track in enumerate(middle_path):
            spotify_track = resolve_to_spotify(track["artist"], track["name"])
            if spotify_track:
                spotify_tracks.append(format_track(
                    spotify_track,
                    i + 1,
                    f"bridge ({track.get('match', 0):.0%} similar)"
                ))

    # Add end track
    spotify_tracks.append(format_track(end_spotify, len(spotify_tracks), "end"))

    return {
        "tracks": spotify_tracks,
        "path_length": original_length,
        "sampled_length": len(spotify_tracks),
        "success": True,
    }


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

    original_length = len(path)

    yield {
        "type": "progress",
        "phase": "resolving",
        "message": f"Found path with {original_length} tracks, resolving to Spotify...",
    }

    # Sample if path is longer than target
    if len(path) > track_count:
        path = sample_evenly(path, track_count)

    # Resolve to Spotify tracks
    spotify_tracks = []

    # Add start track
    spotify_tracks.append(format_track(start_spotify, 0, "start"))

    # Resolve middle tracks
    if len(path) > 2:
        middle_path = path[1:-1]
        for i, track in enumerate(middle_path):
            spotify_track = resolve_to_spotify(track["artist"], track["name"])
            if spotify_track:
                spotify_tracks.append(format_track(
                    spotify_track,
                    i + 1,
                    f"bridge ({track.get('match', 0):.0%} similar)"
                ))

    # Add end track
    spotify_tracks.append(format_track(end_spotify, len(spotify_tracks), "end"))

    # Final result
    yield {
        "type": "result",
        "tracks": spotify_tracks,
        "path_length": original_length,
        "sampled_length": len(spotify_tracks),
        "success": True,
    }


def astar_find_path_streaming(
    start: Dict,
    end: Dict,
    progress_callback=None,
    max_iterations: int = 500,
    max_seconds: float = 90.0,
):
    """
    Bidirectional search with PARALLEL API calls for speed.

    Uses batch expansion to fetch similar tracks for multiple nodes at once.
    Returns best path found within time/iteration limits.
    """
    import time
    start_time = time.time()

    print(f"[BiA*] Starting parallel bidirectional search: {start['artist']} - {start['name']} → {end['artist']} - {end['name']}")

    yield {
        "type": "progress",
        "phase": "neighborhood",
        "message": "Initializing bidirectional search...",
    }

    start_key = (start["artist"].lower(), start["name"].lower())
    end_key = (end["artist"].lower(), end["name"].lower())

    if start_key == end_key:
        yield {"type": "result", "path": [start], "iterations": 0}
        return

    # Settings for speed vs coverage tradeoff
    SIMILAR_LIMIT = 30  # Tracks per node
    BATCH_SIZE = 10  # Expand 10 nodes in parallel per side (up to 20 API calls per batch)

    # Forward search state
    open_f = [(0, 0, start_key, start, [start])]
    visited_f: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    counter_f = 0

    # Backward search state
    open_b = [(0, 0, end_key, end, [end])]
    visited_b: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}  # key -> (g_score, path)
    counter_b = 0

    iterations = 0

    yield {
        "type": "progress",
        "phase": "neighborhood",
        "message": "Starting parallel search...",
        "neighborhood_1hop": 0,
        "neighborhood_2hop": 0,
    }

    # Track closest approach from each side for best-effort path
    best_forward_to_end: Optional[Tuple[float, List[Dict]]] = None  # (distance, path)
    best_backward_to_start: Optional[Tuple[float, List[Dict]]] = None

    while (open_f or open_b) and iterations < max_iterations:
        # Check time limit
        elapsed = time.time() - start_time
        if elapsed > max_seconds:
            print(f"[BiA*] Time limit reached ({elapsed:.1f}s)")
            break

        iterations += 1

        # Collect nodes to expand in batch
        to_expand_f = []
        while open_f and len(to_expand_f) < BATCH_SIZE:
            _, _, key, data, path = heapq.heappop(open_f)
            if key not in visited_f:
                to_expand_f.append((key, data, path))

        to_expand_b = []
        while open_b and len(to_expand_b) < BATCH_SIZE:
            _, _, key, data, path = heapq.heappop(open_b)
            if key not in visited_b:
                to_expand_b.append((key, data, path))

        if not to_expand_f and not to_expand_b:
            break

        # Mark visited and check for meeting point BEFORE fetching neighbors
        for key, data, path in to_expand_f:
            g = sum(1 - t.get("match", 0) for t in path[1:]) if len(path) > 1 else 0
            visited_f[key] = (g, path)
            if key in visited_b:
                g_b, path_b = visited_b[key]
                complete_path = path[:-1] + list(reversed(path_b))
                print(f"[BiA*] Found path in {iterations} batches!")
                yield {"type": "result", "path": complete_path, "iterations": iterations}
                return

        for key, data, path in to_expand_b:
            g = sum(1 - t.get("match", 0) for t in path[1:]) if len(path) > 1 else 0
            visited_b[key] = (g, path)
            if key in visited_f:
                g_f, path_f = visited_f[key]
                complete_path = path_f[:-1] + list(reversed(path))
                print(f"[BiA*] Found path in {iterations} batches!")
                yield {"type": "result", "path": complete_path, "iterations": iterations}
                return

        # Fetch neighbors in PARALLEL
        tracks_to_fetch = []
        track_info = {}  # Map (artist, track) -> (direction, key, data, path)

        for key, data, path in to_expand_f:
            t = (data["artist"], data["name"])
            tracks_to_fetch.append(t)
            track_info[t] = ("f", key, data, path)

        for key, data, path in to_expand_b:
            t = (data["artist"], data["name"])
            tracks_to_fetch.append(t)
            track_info[t] = ("b", key, data, path)

        if tracks_to_fetch:
            # PARALLEL API CALLS (up to 20 concurrent)
            batch_results = get_similar_tracks_batch(tracks_to_fetch, limit=SIMILAR_LIMIT, max_workers=20)

            # Process results
            for track_tuple, similar in batch_results.items():
                direction, parent_key, parent_data, parent_path = track_info[track_tuple]
                parent_g = sum(1 - t.get("match", 0) for t in parent_path[1:]) if len(parent_path) > 1 else 0

                for neighbor in similar:
                    neighbor_key = (neighbor["artist"].lower(), neighbor["name"].lower())
                    edge_cost = 1 - neighbor["match"]
                    new_g = parent_g + edge_cost

                    if direction == "f":
                        if neighbor_key not in visited_f:
                            counter_f += 1
                            heapq.heappush(open_f, (new_g, counter_f, neighbor_key, neighbor, parent_path + [neighbor]))
                    else:
                        if neighbor_key not in visited_b:
                            counter_b += 1
                            heapq.heappush(open_b, (new_g, counter_b, neighbor_key, neighbor, parent_path + [neighbor]))

        # Progress update
        if progress_callback:
            total_visited = len(visited_f) + len(visited_b)
            total_queue = len(open_f) + len(open_b)
            progress_pct = min(0.9, total_visited / 100)
            current = to_expand_f[0][1] if to_expand_f else (to_expand_b[0][1] if to_expand_b else start)
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


def format_track(spotify_track: Dict, position: int, role: str) -> Dict:
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
    }
