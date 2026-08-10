#!/usr/bin/env python3
"""Live acceptance test for the multi-anchor playlist creator.

The default recipe is the exact 70-track case that exposed first-anchor
domination in the web UI.  It exercises the HTTP endpoint twice (normal and
reversed anchor order), checks the response contract and mix quality, and
prints the complete ordered playlist for a human taste review.
"""

from __future__ import annotations

import argparse
from collections import Counter
import re
import sys
import time
import unicodedata

import requests


DEFAULT_ANCHORS = [
    "0oA9wBGDY4uyILLg4GymWP",  # Tom's Diner — AnnenMayKantereit, Giant Rooks
    "7tICCrK3CcyRFKza7yrR0z",  # Homewrecker — sombr
    "26QLJMK8G0M06sk7h7Fkse",  # love is embarrassing — Olivia Rodrigo
]


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def longest_run(values: list[str]) -> int:
    best = current = 0
    previous = None
    for value in values:
        current = current + 1 if value == previous else 1
        previous = value
        best = max(best, current)
    return best


def request_mix(base_url: str, anchor_ids: list[str]) -> tuple[dict, float]:
    started = time.monotonic()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/recommendations/vibe",
        json={
            "anchor_track_ids": anchor_ids,
            "track_count": 70,
            "discovery_ratio": 60,
            "flow_mode": "smooth",
            "exclude_artists": [],
            "coherence_threshold": 50,
            "max_per_anchor_artist": 3,
            "max_per_similar_artist": 2,
        },
        timeout=120,
    )
    elapsed = time.monotonic() - started
    response.raise_for_status()
    return response.json(), elapsed


def audit_result(result: dict, requested_anchors: list[str], elapsed: float) -> list[str]:
    failures: list[str] = []
    tracks = result.get("tracks", [])
    track_ids = [track.get("track_id") for track in tracks]
    groups = [track.get("primary_anchor_id") for track in tracks]
    counts = result.get("counts", {})
    warnings = result.get("warnings", [])

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(elapsed < 120, f"endpoint took {elapsed:.1f}s (browser budget is 120s)")
    require(len(tracks) == 70, f"returned {len(tracks)} tracks instead of 70")
    require(counts.get("total") == len(tracks), "counts.total disagrees with tracks")
    require(counts.get("requested_history") == 28, "requested history count is not 28")
    require(counts.get("requested_discovery") == 42, "requested discovery count is not 42")
    if counts.get("history") != 28 or counts.get("discovery") != 42:
        require(bool(warnings), "source-ratio shortfall was not disclosed")

    require(len(track_ids) == len(set(track_ids)), "duplicate Spotify track IDs")
    semantic = [
        (normalize((track.get("artist") or "").split(",", 1)[0]), normalize(track.get("track") or ""))
        for track in tracks
    ]
    require(len(semantic) == len(set(semantic)), "duplicate normalized artist/title pairs")
    for anchor_id in requested_anchors:
        require(track_ids.count(anchor_id) == 1, f"anchor {anchor_id} is missing or duplicated")

    require(all(group in requested_anchors for group in groups), "track has an unknown primary anchor")
    mix_counts = Counter(groups)
    if mix_counts:
        require(max(mix_counts.values()) / len(tracks) <= 0.45, "one anchor owns over 45% of the mix")
        require(min(mix_counts.values()) / len(tracks) >= 0.20, "one anchor owns under 20% of the mix")
    require(longest_run(groups) <= 3, f"same-anchor run is {longest_run(groups)} (>3)")

    if len(tracks) >= 9 and len(requested_anchors) == 3:
        thin_windows = [
            index + 1
            for index in range(len(groups) - 8)
            if len(set(groups[index:index + 9])) < 3
        ]
        require(not thin_windows, f"9-song windows missing an anchor: {thin_windows[:8]}")

    group_changes = sum(left != right for left, right in zip(groups, groups[1:]))
    require(group_changes >= 8, f"only {group_changes} anchor transitions; mix is still blocky")
    require(group_changes <= 30, f"{group_changes} anchor transitions; mix may ping-pong too often")

    for track in tracks:
        affinities = track.get("anchor_affinities") or {}
        primary = track.get("primary_anchor_id")
        require(primary in affinities, f"{track.get('track')} lacks its primary affinity")
        require(
            all(score > 0 for score in affinities.values()),
            f"{track.get('track')} serializes zero/false anchor affinities",
        )

    mix = result.get("anchor_mix") or []
    require({item.get("anchor_track_id") for item in mix} == set(requested_anchors), "anchor_mix IDs are incomplete")
    require(sum(item.get("count", 0) for item in mix) == len(tracks), "anchor_mix counts do not sum to total")
    return failures


def print_mix(result: dict, elapsed: float, title: str) -> None:
    print(f"\n{title}: {elapsed:.2f}s")
    print(f"counts={result.get('counts')} warnings={result.get('warnings', [])}")
    print("anchor_mix=")
    for item in result.get("anchor_mix", []):
        print(
            f"  {item.get('anchor_track')} — {item.get('anchor_artist')}: "
            f"{item.get('count')} ({item.get('history')} familiar + {item.get('discovery')} new)"
        )
    print(f"flow={result.get('flow_stats')}")
    print("ordered tracks=")
    for index, track in enumerate(result.get("tracks", []), 1):
        source = "N" if track.get("source") == "discovery" else "F"
        print(
            f"  {index:02d}. [{source}] {track.get('track')} — {track.get('artist')} "
            f"| {track.get('primary_anchor_name')} | {track.get('coherence_score'):.3f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()

    first, first_elapsed = request_mix(args.base_url, DEFAULT_ANCHORS)
    reversed_result, reversed_elapsed = request_mix(args.base_url, list(reversed(DEFAULT_ANCHORS)))
    failures = audit_result(first, DEFAULT_ANCHORS, first_elapsed)
    failures.extend(audit_result(reversed_result, DEFAULT_ANCHORS, reversed_elapsed))

    first_ids = {track.get("track_id") for track in first.get("tracks", [])}
    reversed_ids = {track.get("track_id") for track in reversed_result.get("tracks", [])}
    permutation_overlap = 1.0
    if first_ids != reversed_ids:
        permutation_overlap = len(first_ids & reversed_ids) / max(
            len(first_ids | reversed_ids), 1
        )
        # The deterministic unit fixture requires exact input-order
        # invariance. A live run may legitimately lose or gain a few Spotify
        # catalog rows under transient rate limiting, so retain a strict but
        # realistic overlap gate rather than demanding byte-identical sets.
        if permutation_overlap < 0.80:
            failures.append(
                "reversing anchors changed too much of the selected set "
                f"(Jaccard {permutation_overlap:.3f})"
            )

    print_mix(first, first_elapsed, "Original anchor order")
    print_mix(reversed_result, reversed_elapsed, "Reversed anchor order")
    if first_ids != reversed_ids:
        print(
            "\nNOTE: live catalog overlap under reversed input was "
            f"{permutation_overlap:.3f}; deterministic permutation behavior "
            "is covered separately by mocked regressions."
        )
    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: live multi-anchor selection, ordering, ratio, contract, and permutation gates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
