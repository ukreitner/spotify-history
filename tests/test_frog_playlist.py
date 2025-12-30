#!/usr/bin/env python3
"""Test the frog playlist generator with different start/end pairs."""

import requests

BASE = "http://localhost:8000"


def search_track(query):
    """Search for a track on Spotify."""
    resp = requests.get(f"{BASE}/api/tracks/search?q={query}&limit=1")
    results = resp.json()
    if results:
        return results[0]
    return None


def generate_frog_playlist(start_id, end_id, track_count=15):
    """Generate a frog playlist."""
    resp = requests.post(f"{BASE}/api/recommendations/frog", json={
        "start_track_id": start_id,
        "end_track_id": end_id,
        "track_count": track_count
    })
    return resp.json()


def print_result(name, start, end, result):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Start: {start['track']} - {start['artist']}")
    print(f"End: {end['track']} - {end['artist']}")

    if not result.get("success"):
        print(f"ERROR: {result.get('error', 'Unknown error')}")
        return

    print(f"Path length: {result['path_length']}")
    print(f"Sampled to: {result['sampled_length']} tracks")

    print("\nPlaylist:")
    for i, t in enumerate(result["tracks"], 1):
        role = t.get("role", "")
        role_str = f"[{role.upper()}]" if role in ("start", "end") else ""
        print(f"  {i:2}. {role_str:7} {t['track'][:35]:35} - {t['artist'][:25]}")


if __name__ == "__main__":
    tests = [
        # Similar genres
        ("Indie Rock", "tame impala let it happen", "mac demarco my kind of woman"),

        # Different moods same genre
        ("Folk range", "fleet foxes white winter hymnal", "bon iver skinny love"),

        # Cross-genre challenge
        ("Rock to Electronic", "the strokes last nite", "daft punk around the world"),

        # Decade jump
        ("70s to 2020s", "led zeppelin stairway to heaven", "glass animals heat waves"),
    ]

    for name, start_query, end_query in tests:
        start = search_track(start_query)
        end = search_track(end_query)

        if not start or not end:
            print(f"\n{name}: Could not find tracks")
            continue

        result = generate_frog_playlist(start["track_id"], end["track_id"])
        print_result(name, start, end, result)
