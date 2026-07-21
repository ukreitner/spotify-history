#!/usr/bin/env python3
"""Run live, taste-oriented quality gates against the recommendation API."""

from collections import Counter
import os
from pathlib import Path
import statistics
import sys
from typing import Dict, List

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.db import get_all_tracks_with_counts


BASE_URL = os.getenv("QUALITY_AUDIT_BASE_URL", "http://127.0.0.1:8001")
KNOWN_TRACK_IDS = set(get_all_tracks_with_counts("music"))
KNOWN_TRACK_KEYS = {
    (
        "".join(character for character in row.get("artist", "").split(",", 1)[0].lower() if character.isalnum()),
        "".join(character for character in row.get("track", "").lower() if character.isalnum()),
    )
    for row in get_all_tracks_with_counts("music").values()
}


SCENARIOS = [
    {
        "name": "twee indie single anchor",
        "anchors": ["0xBH1GysY7fy6RJjX8hW5R"],
        "count": 30,
        "ratio": 50,
        "flow": "smooth",
        "forbidden": {"katy perry", "sabrina carpenter", "ludwig van beethoven"},
    },
    {
        "name": "current pop coherent duo",
        "anchors": ["6HU7h9RYOaPRFeh0R3UeAr", "3hUxzQpSfdDqwM3ZTFQY0K"],
        "count": 24,
        "ratio": 65,
        "flow": "smooth",
    },
    {
        "name": "indie folk coherent duo",
        "anchors": ["13xAVeXFvwNod6KpmNjtRU", "0mflMxspEfB0VbI1kyLiAv"],
        "count": 24,
        "ratio": 50,
        "flow": "smooth",
    },
    {
        "name": "alternative high discovery",
        "anchors": ["63OQupATfueTdZMWTxW03A"],
        "count": 20,
        "ratio": 80,
        "flow": "smooth",
    },
    {
        "name": "mixed-anchor stress test",
        "anchors": ["3B3eOgLJSqPEA0RfboIQVM", "0N3W5peJUQtI4eyR6GJT5O"],
        "count": 20,
        "ratio": 60,
        "flow": "shuffle",
    },
    {
        "name": "artist exclusion regression",
        "anchors": ["0xBH1GysY7fy6RJjX8hW5R"],
        "count": 18,
        "ratio": 60,
        "flow": "smooth",
        "exclude": ["Camera Obscura"],
    },
]


def first_artist(track: Dict) -> str:
    return (track.get("artist") or "").split(",", 1)[0].strip()


def semantic_key(track: Dict):
    artist = "".join(character for character in first_artist(track).lower() if character.isalnum())
    title = "".join(character for character in (track.get("track") or "").lower() if character.isalnum())
    return artist, title


def run_scenario(scenario: Dict) -> List[str]:
    payload = {
        "anchor_track_ids": scenario["anchors"],
        "track_count": scenario["count"],
        "discovery_ratio": scenario["ratio"],
        "flow_mode": scenario["flow"],
        "exclude_artists": scenario.get("exclude", []),
        "coherence_threshold": 50,
        "max_per_anchor_artist": 3,
        "max_per_similar_artist": 2,
    }
    response = requests.post(
        f"{BASE_URL}/api/recommendations/vibe",
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    result = response.json()
    tracks = result.get("tracks", [])
    failures: List[str] = []

    ids = [track.get("track_id") for track in tracks]
    artists = [first_artist(track) for track in tracks]
    artist_counts = Counter(artist.lower() for artist in artists)
    anchor_ids = set(scenario["anchors"])
    discovery = [track for track in tracks if track.get("source") == "discovery"]
    scores = [float(track.get("coherence_score", 0)) for track in tracks]
    actual_ratio = len(discovery) / len(tracks) if tracks else 0

    if len(tracks) != scenario["count"]:
        failures.append(f"returned {len(tracks)} of {scenario['count']} requested tracks")
    if len(ids) != len(set(ids)):
        failures.append("duplicate track IDs")
    semantic_keys = [semantic_key(track) for track in tracks]
    if len(semantic_keys) != len(set(semantic_keys)):
        failures.append("duplicate artist/title songs under different Spotify IDs")
    if not anchor_ids.issubset(set(ids)):
        failures.append("one or more selected anchors are missing")
    if discovery and any(track.get("track_id") in KNOWN_TRACK_IDS for track in discovery):
        failures.append("discovery contains a previously heard track")
    if discovery and any(semantic_key(track) in KNOWN_TRACK_KEYS for track in discovery):
        failures.append("discovery relabels a previously heard artist/title as new")
    if scores and min(scores) < 0.50:
        failures.append(f"minimum coherence {min(scores):.3f} is below threshold")
    if scores and statistics.mean(scores) < 0.80:
        failures.append(f"average coherence {statistics.mean(scores):.3f} is too low")
    if tracks and len(artist_counts) / len(tracks) < 0.50:
        failures.append("fewer than half as many unique artists as tracks")
    if any(count > 3 for count in artist_counts.values()):
        failures.append("artist repetition exceeds the hard cap of 3")
    if abs(actual_ratio - scenario["ratio"] / 100) > 0.15:
        failures.append(
            f"new-music ratio {actual_ratio:.0%} is too far from requested {scenario['ratio']}%"
        )

    forbidden = {name.lower() for name in scenario.get("forbidden", set())}
    bad_artists = sorted(forbidden & set(artist_counts))
    if bad_artists:
        failures.append(f"known off-vibe regression artists returned: {', '.join(bad_artists)}")

    excluded = {name.lower() for name in scenario.get("exclude", [])}
    excluded_hits = sorted(excluded & set(artist_counts))
    if excluded_hits:
        failures.append(f"excluded artists returned: {', '.join(excluded_hits)}")

    ungrounded = [
        track for track in discovery
        if not any(
            label in (track.get("discovered_via") or "")
            for label in ("track match", "similar artist")
        )
    ]
    if ungrounded:
        failures.append(f"{len(ungrounded)} discovery tracks lack similarity provenance")

    status = "PASS" if not failures else "FAIL"
    mean_score = statistics.mean(scores) if scores else 0
    print(
        f"\n[{status}] {scenario['name']}: {len(tracks)} tracks, "
        f"{len(artist_counts)} artists, {actual_ratio:.0%} new, "
        f"mean coherence {mean_score:.3f}"
    )
    for index, track in enumerate(tracks, 1):
        source = "N" if track.get("source") == "discovery" else "H"
        print(f"  {index:2}. [{source}] {track.get('track')} — {track.get('artist')}")
    for failure in failures:
        print(f"  QUALITY FAILURE: {failure}")
    return failures


def main() -> int:
    all_failures: Dict[str, List[str]] = {}
    for scenario in SCENARIOS:
        failures = run_scenario(scenario)
        if failures:
            all_failures[scenario["name"]] = failures

    print(f"\nQuality audit: {len(SCENARIOS) - len(all_failures)}/{len(SCENARIOS)} scenarios passed")
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
