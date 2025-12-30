#!/usr/bin/env python3
"""Simple test for the frog playlist."""

import requests

BASE = "http://localhost:8000"


def search_track(query):
    resp = requests.get(f"{BASE}/api/tracks/search?q={query}&limit=1")
    results = resp.json()
    if results:
        return results[0]
    return None


def generate_frog_playlist(start_id, end_id, track_count=15):
    resp = requests.post(f"{BASE}/api/recommendations/frog", json={
        "start_track_id": start_id,
        "end_track_id": end_id,
        "track_count": track_count
    })
    return resp.json()


# Test similar artists - should find path quickly
start = search_track("tame impala let it happen")
end = search_track("pond man it feels like space again")

print(f"Start: {start['track']} - {start['artist']}")
print(f"End: {end['track']} - {end['artist']}")

result = generate_frog_playlist(start["track_id"], end["track_id"])

if result.get("success"):
    print(f"\nPath found! {result['path_length']} tracks")
    print(f"Sampled to: {result['sampled_length']} tracks")
    print("\nPlaylist:")
    for i, t in enumerate(result["tracks"], 1):
        role = t.get("role", "")
        print(f"  {i:2}. [{role:6}] {t['track'][:40]:40} - {t['artist'][:25]}")
else:
    print(f"Error: {result.get('error')}")
