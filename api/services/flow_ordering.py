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

FlowMode = Literal["smooth", "energy_arc", "shuffle"]


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


def order_for_smooth_flow(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
) -> List[Dict]:
    """
    Order tracks for smooth flow using greedy nearest-neighbor.

    Starts with random track, then picks lowest transition cost.
    """
    if len(tracks) <= 1:
        return tracks

    remaining = list(tracks)
    ordered = []

    # Start with random track
    start_idx = random.randint(0, len(remaining) - 1)
    ordered.append(remaining.pop(start_idx))

    while remaining:
        last_track = ordered[-1]
        last_id = last_track.get('id', '')
        last_features = features_map.get(last_id, {})
        last_genres = genres_map.get(last_id, set())

        # Find track with lowest transition cost
        best_idx = 0
        best_cost = float('inf')

        for i, candidate in enumerate(remaining):
            cand_id = candidate.get('id', '')
            cand_features = features_map.get(cand_id, {})
            cand_genres = genres_map.get(cand_id, set())

            cost = compute_transition_cost(
                last_features, cand_features,
                last_genres, cand_genres
            )

            if cost < best_cost:
                best_cost = cost
                best_idx = i

        ordered.append(remaining.pop(best_idx))

    return ordered


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
) -> List[Dict]:
    """
    Order playlist tracks according to the specified flow mode.

    Args:
        tracks: List of track dicts
        features_map: Dict mapping track_id -> audio features
        genres_map: Dict mapping track_id -> set of genres
        flow_mode: "smooth", "energy_arc", or "shuffle"

    Returns:
        Ordered list of tracks
    """
    if flow_mode == "shuffle":
        shuffled = list(tracks)
        random.shuffle(shuffled)
        return shuffled

    if flow_mode == "energy_arc":
        return order_for_energy_arc(tracks, features_map)

    # Default: smooth flow
    return order_for_smooth_flow(tracks, features_map, genres_map)


def compute_playlist_flow_stats(
    tracks: List[Dict],
    features_map: Dict[str, Dict],
    genres_map: Dict[str, set],
) -> Dict:
    """
    Compute statistics about playlist flow quality.

    Returns metrics about transitions and overall coherence.
    """
    if len(tracks) < 2:
        return {
            'avg_transition_cost': 0,
            'max_transition_cost': 0,
            'smooth_transitions': 0,
            'jarring_transitions': 0,
        }

    transition_costs = []

    for i in range(len(tracks) - 1):
        track_a = tracks[i]
        track_b = tracks[i + 1]

        cost = compute_transition_cost(
            features_map.get(track_a.get('id', ''), {}),
            features_map.get(track_b.get('id', ''), {}),
            genres_map.get(track_a.get('id', ''), set()),
            genres_map.get(track_b.get('id', ''), set()),
        )
        transition_costs.append(cost)

    avg_cost = sum(transition_costs) / len(transition_costs) if transition_costs else 0
    max_cost = max(transition_costs) if transition_costs else 0

    # Count smooth (<0.3) vs jarring (>0.6) transitions
    smooth = sum(1 for c in transition_costs if c < 0.3)
    jarring = sum(1 for c in transition_costs if c > 0.6)

    return {
        'avg_transition_cost': round(avg_cost, 3),
        'max_transition_cost': round(max_cost, 3),
        'smooth_transitions': smooth,
        'jarring_transitions': jarring,
        'total_transitions': len(transition_costs),
    }
