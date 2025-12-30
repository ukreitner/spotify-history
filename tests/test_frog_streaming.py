#!/usr/bin/env python3
"""Test the frog playlist streaming endpoint."""

import requests
import json
import time
import sys

BASE = "http://localhost:8000"


def search_track(query):
    """Search for a track by name."""
    resp = requests.get(f"{BASE}/api/tracks/search?q={query}&limit=1", timeout=10)
    results = resp.json()
    return results[0] if results else None


def test_frog_streaming(start_query, end_query, track_count=15, verbose=True):
    """
    Test the streaming frog playlist endpoint.

    Returns (success, elapsed_time, track_count, path)
    """
    start = search_track(start_query)
    end = search_track(end_query)

    if not start or not end:
        print(f"Could not find tracks for: {start_query} or {end_query}")
        return False, 0, 0, []

    if verbose:
        print(f"\n{start['artist']} → {end['artist']}")

    t0 = time.time()
    resp = requests.post(
        f"{BASE}/api/recommendations/frog/stream",
        json={
            "start_track_id": start["track_id"],
            "end_track_id": end["track_id"],
            "track_count": track_count,
        },
        stream=True,
        timeout=120,
    )

    batch_count = 0
    result = None

    for line in resp.iter_lines():
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: "):
                event = json.loads(line[6:])

                if event.get("type") == "progress" and event.get("phase") == "search":
                    batch_count = event.get("iteration", 0)
                    if verbose and batch_count % 10 == 0:
                        print(f"  batch={batch_count} visited={event.get('visited', 0)}")

                elif event.get("type") == "result":
                    result = event
                    break

    elapsed = time.time() - t0

    if result and result.get("success"):
        tracks = result.get("tracks", [])
        if verbose:
            print(f"\n✓ Found path in {elapsed:.1f}s! {len(tracks)} tracks")
            for i, t in enumerate(tracks):
                print(f"  {i+1}. {t['track'][:35]:35} - {t['artist'][:20]}")
        return True, elapsed, len(tracks), tracks
    else:
        if verbose:
            print(f"\n✗ No path found after {elapsed:.1f}s")
        return False, elapsed, 0, []


def main():
    test_cases = [
        # Easy - same genre/era
        ("tame impala let it happen", "pond holding out for you", "Easy: Tame Impala → Pond"),
        ("arctic monkeys do i wanna know", "the strokes last nite", "Easy: Arctic Monkeys → The Strokes"),

        # Medium - related genres
        ("radiohead creep", "nirvana smells like teen spirit", "Medium: Radiohead → Nirvana"),

        # Hard - different genres
        ("the raconteurs steady as she goes", "suzanne vega toms diner", "Hard: The Raconteurs → Suzanne Vega"),
    ]

    if len(sys.argv) > 1:
        # Run specific test by index
        idx = int(sys.argv[1])
        test_cases = [test_cases[idx]]

    for start_q, end_q, desc in test_cases:
        print(f"\n{'='*60}")
        print(f"TEST: {desc}")
        print('='*60)

        success, elapsed, count, _ = test_frog_streaming(start_q, end_q)

        print(f"\nResult: {'PASS' if success else 'FAIL'} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
