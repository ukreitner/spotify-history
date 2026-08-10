"""
Flow ordering for playlist tracks.

Orders tracks for smooth listening experience:
- Smooth flow: minimize jarring transitions
- Energy arc: build up → peak → wind down
- Shuffle: random order
"""

from typing import List, Dict, Optional, Literal
import random
import math
import re

FlowMode = Literal["smooth", "energy_arc", "shuffle"]


_VERSION_PATTERNS = {
    "live": re.compile(r"\blive\b", re.IGNORECASE),
    "acoustic": re.compile(r"\b(?:acoustic|unplugged)\b", re.IGNORECASE),
    "remix": re.compile(r"\bremix\b", re.IGNORECASE),
    "version": re.compile(r"\b(?:version|edit)\b", re.IGNORECASE),
    "sped": re.compile(r"\bsped[ -]?up\b", re.IGNORECASE),
    "slowed": re.compile(r"\bslowed\b", re.IGNORECASE),
}


def compute_transition_cost(
    track_a_features: Optional[Dict],
    track_b_features: Optional[Dict],
    track_a_genres: set,
    track_b_genres: set,
) -> float:
    """
    Compute the transition cost between two tracks.

    Lower cost = smoother transition.

    Components:
    - Energy difference (ideal: ±0.1)
    - Tempo difference (ideal: ±10 BPM)
    - Genre continuity bonus
    """
    cost = 0.0

    # If no features, use neutral cost
    if not track_a_features or not track_b_features:
        # Check genre overlap as fallback
        if track_a_genres and track_b_genres:
            overlap = len(track_a_genres & track_b_genres)
            if overlap > 0:
                return 0.3  # Good genre match
            return 0.6  # No genre match
        return 0.5  # Neutral

    # Energy difference (0-1 scale, ideal is small diff)
    energy_a = track_a_features.get('energy', 0.5)
    energy_b = track_b_features.get('energy', 0.5)
    energy_diff = abs(energy_a - energy_b)
    # Penalize large jumps (>0.3)
    if energy_diff > 0.3:
        cost += (energy_diff - 0.1) * 2
    else:
        cost += energy_diff * 0.5

    # Tempo difference (normalize by typical range)
    tempo_a = track_a_features.get('tempo', 120)
    tempo_b = track_b_features.get('tempo', 120)
    tempo_diff = abs(tempo_a - tempo_b)
    # Penalize >20 BPM jumps
    if tempo_diff > 20:
        cost += (tempo_diff / 20) * 0.5
    else:
        cost += (tempo_diff / 40) * 0.3

    # Valence (mood) difference
    valence_a = track_a_features.get('valence', 0.5)
    valence_b = track_b_features.get('valence', 0.5)
    valence_diff = abs(valence_a - valence_b)
    cost += valence_diff * 0.3

    # Genre continuity bonus (reduce cost if genres overlap)
    if track_a_genres and track_b_genres:
        overlap = len(track_a_genres & track_b_genres)
        if overlap > 0:
            cost -= 0.2 * min(overlap, 2)

    return max(0, cost)


def _version_markers(track: Dict) -> set:
    """Return production/performance labels that can imply a large jump."""
    title = track.get('name', '') or ''
    return {
        marker for marker, pattern in _VERSION_PATTERNS.items()
        if pattern.search(title)
    }


def _primary_artist_identity(track: Dict) -> str:
    artists = track.get('artists') or []
    if not artists:
        return ''
    primary = artists[0] or {}
    return str(primary.get('id') or primary.get('name') or '').strip().casefold()


def _affinity_cosine(
    left: Dict[str, float],
    right: Dict[str, float],
) -> float:
    """Compare anchor-evidence vectors without treating their scale as audio."""
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    left_values = {key: max(0.0, float(left.get(key, 0) or 0)) for key in keys}
    right_values = {key: max(0.0, float(right.get(key, 0) or 0)) for key in keys}
    left_norm = math.sqrt(sum(value * value for value in left_values.values()))
    right_norm = math.sqrt(sum(value * value for value in right_values.values()))
    if not left_norm or not right_norm:
        return 0.0
    dot = sum(left_values[key] * right_values[key] for key in keys)
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _metadata_transition_proxy(
    left: Dict,
    right: Dict,
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
    group_map: Dict[str, str],
    affinities_map: Dict[str, Dict[str, float]],
) -> float:
    """Return a relative ordering cost, not a measured sonic-quality score.

    Real Spotify audio features remain authoritative when both tracks have
    them. Otherwise the proxy uses only evidence that can help break ties:
    artist genres, overlap between anchor-affinity vectors, reciprocal
    cross-anchor evidence, and explicit alternate-production labels. Exact
    artist identity is deliberately a small *penalty*: without audio data,
    two songs by one artist can still have a severe energy jump.
    """
    left_id = left.get('id', '')
    right_id = right.get('id', '')
    left_features = features_map.get(left_id, {})
    right_features = features_map.get(right_id, {})
    left_genres = genres_map.get(left_id, set())
    right_genres = genres_map.get(right_id, set())

    if left_features and right_features:
        return compute_transition_cost(
            left_features,
            right_features,
            left_genres,
            right_genres,
        )

    # Preserve the existing genre-overlap fallback as the base ordering hint.
    cost = compute_transition_cost({}, {}, left_genres, right_genres)
    left_affinities = affinities_map.get(left_id, {})
    right_affinities = affinities_map.get(right_id, {})
    if left_affinities and right_affinities:
        cost -= 0.16 * _affinity_cosine(left_affinities, right_affinities)

    left_group = group_map.get(left_id)
    right_group = group_map.get(right_id)
    if left_group and right_group and left_group != right_group:
        bridge_strength = max(
            float(left_affinities.get(right_group, 0) or 0),
            float(right_affinities.get(left_group, 0) or 0),
        )
        cost -= 0.28 * max(0.0, min(1.0, bridge_strength))

    left_artist = _primary_artist_identity(left)
    right_artist = _primary_artist_identity(right)
    if left_artist and left_artist == right_artist:
        cost += 0.08

    if _version_markers(left) != _version_markers(right):
        cost += 0.18

    return max(0.0, cost)


def _sequence_proxy_cost(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
    group_map: Dict[str, str],
    affinities_map: Dict[str, Dict[str, float]],
) -> float:
    """Sum the private metadata proxy for deterministic regression tests."""
    return sum(
        _metadata_transition_proxy(
            tracks[index],
            tracks[index + 1],
            features_map,
            genres_map,
            group_map,
            affinities_map,
        )
        for index in range(len(tracks) - 1)
    )


def _refine_fixed_group_slots(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
    group_map: Dict[str, str],
    affinities_map: Dict[str, Dict[str, float]],
    max_passes: int = 4,
) -> List[Dict]:
    """Improve boundary choices without changing a single group-label slot.

    Position zero stays pinned. Every attempted swap is between tracks from
    the same primary-anchor group, so run length, rolling group coverage, and
    the number of group changes are invariant by construction.
    """
    if len(tracks) < 3 or not group_map:
        return tracks

    refined = list(tracks)
    original_groups = [group_map.get(track.get('id', '')) for track in refined]
    pass_limit = max(0, min(int(max_passes), 4))

    def edge_cost(index: int) -> float:
        return _metadata_transition_proxy(
            refined[index],
            refined[index + 1],
            features_map,
            genres_map,
            group_map,
            affinities_map,
        )

    for _ in range(pass_limit):
        changed = False
        for left_index in range(1, len(refined)):
            left_group = group_map.get(refined[left_index].get('id', ''))
            if not left_group:
                continue
            for right_index in range(left_index + 1, len(refined)):
                if group_map.get(refined[right_index].get('id', '')) != left_group:
                    continue
                affected_edges = {
                    index
                    for index in (
                        left_index - 1,
                        left_index,
                        right_index - 1,
                        right_index,
                    )
                    if 0 <= index < len(refined) - 1
                }
                before = sum(edge_cost(index) for index in affected_edges)
                refined[left_index], refined[right_index] = (
                    refined[right_index],
                    refined[left_index],
                )
                after = sum(edge_cost(index) for index in affected_edges)
                if after + 1e-9 < before:
                    changed = True
                else:
                    refined[left_index], refined[right_index] = (
                        refined[right_index],
                        refined[left_index],
                    )
        if not changed:
            break

    refined_groups = [group_map.get(track.get('id', '')) for track in refined]
    if refined_groups != original_groups:
        raise AssertionError("fixed-group refinement changed the group schedule")
    return refined


def order_for_smooth_flow(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
    group_map: Optional[Dict[str, str]] = None,
    max_group_run: int = 3,
    affinities_map: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict]:
    """
    Order tracks for smooth flow using greedy nearest-neighbor.

    Starts with the strongest/anchor track supplied by the selector, then picks
    the lowest transition cost. Deterministic ordering makes the same anchors
    reproducible and keeps a selected anchor visibly in the lead position.
    """
    if len(tracks) <= 1:
        return tracks

    remaining = list(tracks)
    ordered = []

    initial_group_counts: Dict[str, int] = {}
    for track in tracks:
        group = (group_map or {}).get(track.get('id', ''))
        if group:
            initial_group_counts[group] = initial_group_counts.get(group, 0) + 1
    selected_group_counts = {group: 0 for group in initial_group_counts}

    ordered.append(remaining.pop(0))

    group_map = group_map or {}
    current_group = group_map.get(ordered[0].get('id', ''))
    group_run = 1 if current_group else 0
    if current_group:
        selected_group_counts[current_group] += 1

    while remaining:
        last_track = ordered[-1]
        last_id = last_track.get('id', '')
        last_features = features_map.get(last_id, {})
        last_genres = genres_map.get(last_id, set())

        # A similarity neighborhood is provenance, not a musical genre. When
        # several anchor neighborhoods remain available, do not let the greedy
        # nearest-neighbour walk consume one entire neighborhood just because
        # its candidates tie on sparse Spotify metadata.
        candidate_indexes = list(range(len(remaining)))
        if current_group and group_run >= max(1, max_group_run):
            alternatives = [
                index for index, candidate in enumerate(remaining)
                if group_map.get(candidate.get('id', '')) != current_group
            ]
            if alternatives:
                candidate_indexes = alternatives

        # Preserve enough tracks from the smaller groups to weave the tail as
        # well as the head. Without this feasibility guard, locally cheap
        # alternation can exhaust two groups early and strand a long final run
        # from the largest group even though a capped ordering exists.
        if group_map and max_group_run > 0:
            remaining_counts: Dict[str, int] = {}
            for candidate in remaining:
                group = group_map.get(candidate.get('id', ''))
                if group:
                    remaining_counts[group] = remaining_counts.get(group, 0) + 1

            feasible_indexes = []
            for index in candidate_indexes:
                group = group_map.get(remaining[index].get('id', ''))
                counts_after = dict(remaining_counts)
                if group:
                    counts_after[group] -= 1
                    if counts_after[group] == 0:
                        counts_after.pop(group)
                if not counts_after:
                    feasible_indexes.append(index)
                    continue
                largest = max(counts_after.values())
                others = sum(counts_after.values()) - largest
                if largest <= max_group_run * (others + 1):
                    feasible_indexes.append(index)
            if feasible_indexes:
                candidate_indexes = feasible_indexes

        if len(initial_group_counts) > 1:
            # Every anchor should remain audible throughout the playlist. A
            # nine-song window for three anchors is large enough to preserve
            # musical mini-runs while preventing one neighborhood from
            # disappearing for an entire screenful of results.
            coverage_window = max(6, len(initial_group_counts) * 3)
            recent_tracks = ordered[-(coverage_window - 1):]
            recent_groups = {
                group_map.get(track.get('id', '')) for track in recent_tracks
            }
            remaining_groups = {
                group_map.get(candidate.get('id', '')) for candidate in remaining
            }
            missing_groups = {
                group for group in initial_group_counts
                if group not in recent_groups and group in remaining_groups
            }
            if len(recent_tracks) >= coverage_window - 1 and missing_groups:
                coverage_indexes = [
                    index for index in candidate_indexes
                    if group_map.get(remaining[index].get('id', '')) in missing_groups
                ]
                if coverage_indexes:
                    candidate_indexes = coverage_indexes

            # Keep cumulative progress close to the requested group mixture.
            # Transition cost still decides which actual song wins inside the
            # currently under-represented group(s).
            next_position = len(ordered) + 1
            total_tracks = len(tracks)
            deficits = {
                group: (
                    next_position * count / total_tracks
                    - selected_group_counts.get(group, 0)
                )
                for group, count in initial_group_counts.items()
            }
            available_deficits = [
                deficits[group_map.get(remaining[index].get('id', ''))]
                for index in candidate_indexes
                if group_map.get(remaining[index].get('id', '')) in deficits
            ]
            if available_deficits:
                largest_deficit = max(available_deficits)
                # Permit coherent mini-runs while preventing a group from
                # falling a full run behind its proportional schedule.
                deficit_tolerance = max(0.5, max_group_run - 1)
                balanced_indexes = [
                    index for index in candidate_indexes
                    if deficits.get(
                        group_map.get(remaining[index].get('id', '')),
                        float('-inf'),
                    ) >= largest_deficit - deficit_tolerance
                ]
                if balanced_indexes:
                    candidate_indexes = balanced_indexes

            # No full coverage window may be more than roughly half one
            # neighborhood when another eligible group is available.
            if len(recent_tracks) >= coverage_window - 1:
                concentration_limit = math.ceil(coverage_window * 0.55)
                concentrated_indexes = []
                for index in candidate_indexes:
                    window_groups = [
                        group_map.get(track.get('id', '')) for track in recent_tracks
                    ] + [group_map.get(remaining[index].get('id', ''))]
                    counts = {
                        group: window_groups.count(group)
                        for group in set(window_groups) if group
                    }
                    if not counts or max(counts.values()) <= concentration_limit:
                        concentrated_indexes.append(index)
                if concentrated_indexes:
                    candidate_indexes = concentrated_indexes

        # Find track with lowest transition cost
        best_idx = candidate_indexes[0]
        best_cost = float('inf')

        for i in candidate_indexes:
            candidate = remaining[i]
            cand_id = candidate.get('id', '')
            cand_features = features_map.get(cand_id, {})
            cand_genres = genres_map.get(cand_id, set())

            cost = compute_transition_cost(
                last_features, cand_features,
                last_genres, cand_genres
            )

            # Sparse Spotify metadata leaves many musically plausible choices
            # tied at the neutral fallback cost. Prefer finishing a short
            # anchor-neighborhood phrase in those ties instead of ping-ponging
            # between anchors every song. Hard run, coverage, concentration,
            # and feasibility filters above still decide when a switch is due.
            candidate_group = group_map.get(cand_id)
            effective_cost = cost
            if (
                current_group
                and candidate_group == current_group
                and group_run < max(1, max_group_run)
            ):
                effective_cost -= 0.08

            if effective_cost < best_cost:
                best_cost = effective_cost
                best_idx = i

        next_track = remaining.pop(best_idx)
        ordered.append(next_track)
        next_group = group_map.get(next_track.get('id', ''))
        if next_group and next_group == current_group:
            group_run += 1
        else:
            current_group = next_group
            group_run = 1 if next_group else 0
        if next_group:
            selected_group_counts[next_group] = (
                selected_group_counts.get(next_group, 0) + 1
            )

    return _refine_fixed_group_slots(
        ordered,
        features_map,
        genres_map,
        group_map,
        affinities_map or {},
    )


def order_for_energy_arc(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
) -> List[Dict]:
    """
    Order tracks for energy arc: build up → peak → wind down.

    Creates a smooth energy curve that peaks around 60-70% through.
    """
    if len(tracks) <= 2:
        return tracks

    # Sort by energy
    def get_energy(track: Dict) -> float:
        tid = track.get('id', '')
        features = features_map.get(tid, {})
        return features.get('energy', 0.5)

    sorted_by_energy = sorted(tracks, key=get_energy)

    # Split into low, mid, high energy groups
    n = len(sorted_by_energy)
    third = n // 3

    low_energy = sorted_by_energy[:third]
    mid_energy = sorted_by_energy[third:2*third]
    high_energy = sorted_by_energy[2*third:]

    # Build arc: start low, build to peak, wind down
    # Structure: low → mid → high (peak) → mid → low
    ordered = []

    # Opening: low energy tracks
    opening_count = max(1, n // 6)
    ordered.extend(low_energy[:opening_count])

    # Build up: remaining low + first half of mid
    ordered.extend(low_energy[opening_count:])
    half_mid = len(mid_energy) // 2
    ordered.extend(mid_energy[:half_mid])

    # Peak: high energy tracks (shuffled for variety)
    random.shuffle(high_energy)
    ordered.extend(high_energy)

    # Wind down: second half of mid
    remaining_mid = mid_energy[half_mid:]
    remaining_mid.reverse()  # Go from higher to lower energy
    ordered.extend(remaining_mid)

    return ordered


def order_playlist(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
    flow_mode: FlowMode = "smooth",
    group_map: Optional[Dict[str, str]] = None,
    max_group_run: int = 3,
    affinities_map: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict]:
    """
    Order playlist tracks according to the specified flow mode.

    Args:
        tracks: List of track dicts
        features_map: Dict mapping track_id -> audio features
        genres_map: Dict mapping track_id -> set of genres
        flow_mode: "smooth", "energy_arc", or "shuffle"
        group_map: Optional mapping from track_id to its fixed anchor group
        max_group_run: Maximum consecutive tracks from one anchor group
        affinities_map: Optional track_id -> anchor affinity evidence mapping

    Returns:
        Ordered list of tracks
    """
    if flow_mode == "shuffle":
        shuffled = list(tracks)
        random.shuffle(shuffled)
        return shuffled

    if flow_mode == "energy_arc" and any(features_map.values()):
        return order_for_energy_arc(tracks, features_map)

    # Smooth flow is also the honest fallback for an energy arc when Spotify
    # does not expose audio features to this application.
    return order_for_smooth_flow(
        tracks,
        features_map,
        genres_map,
        group_map=group_map,
        max_group_run=max_group_run,
        affinities_map=affinities_map,
    )


def compute_playlist_flow_stats(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
) -> Dict:
    """
    Compute measured audio-feature statistics about playlist flow quality.

    Unmeasured genre/affinity proxies are deliberately excluded. Metrics are
    null when no adjacent pair has two audio-feature vectors; partial coverage
    reports only the measured pairs while retaining the structural edge count.
    """
    total_transitions = max(0, len(tracks) - 1)
    transition_costs = []

    for i in range(len(tracks) - 1):
        track_a = tracks[i]
        track_b = tracks[i + 1]

        track_a_features = features_map.get(track_a.get('id', ''), {})
        track_b_features = features_map.get(track_b.get('id', ''), {})
        if not track_a_features or not track_b_features:
            continue

        cost = compute_transition_cost(
            track_a_features,
            track_b_features,
            genres_map.get(track_a.get('id', ''), set()),
            genres_map.get(track_b.get('id', ''), set()),
        )
        transition_costs.append(cost)

    measured_transitions = len(transition_costs)
    if not measured_transitions:
        return {
            'avg_transition_cost': None,
            'max_transition_cost': None,
            'smooth_transitions': None,
            'jarring_transitions': None,
            'measured_transitions': 0,
            'measurement_basis': 'unavailable',
            'total_transitions': total_transitions,
        }

    avg_cost = sum(transition_costs) / measured_transitions
    max_cost = max(transition_costs)
    smooth = sum(1 for cost in transition_costs if cost <= 0.3)
    jarring = sum(1 for cost in transition_costs if cost > 0.6)
    measurement_basis = (
        'audio_features'
        if measured_transitions == total_transitions
        else 'partial_audio_features'
    )

    return {
        'avg_transition_cost': round(avg_cost, 3),
        'max_transition_cost': round(max_cost, 3),
        'smooth_transitions': smooth,
        'jarring_transitions': jarring,
        'measured_transitions': measured_transitions,
        'measurement_basis': measurement_basis,
        'total_transitions': total_transitions,
    }
