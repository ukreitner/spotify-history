#!/usr/bin/env python3
"""Test the vibe playlist generator with different anchors."""

import requests

BASE = "http://localhost:8000"


def search_anchor(query):
    resp = requests.get(f"{BASE}/api/tracks/history/search?q={query}&limit=1")
    results = resp.json()
    if results:
        return results[0]
    return None


def generate_playlist(anchors, discovery_ratio=50, track_count=20):
    resp = requests.post(f"{BASE}/api/recommendations/vibe", json={
        "anchor_track_ids": [a["track_id"] for a in anchors],
        "track_count": track_count,
        "discovery_ratio": discovery_ratio,
        "flow_mode": "smooth"
    })
    return resp.json()


def print_result(name, anchors, result):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Anchors: {', '.join(a['artist'] for a in anchors)}")
    print(f"Genres: {', '.join(result['vibe_profile']['top_genres'])}")
    print(f"Counts: history={result['counts']['history']}, discovery={result['counts']['discovery']}")

    disc_artists = set()
    for t in result["tracks"]:
        if t["source"] == "discovery" and "similar to" in (t.get("discovered_via") or ""):
            disc_artists.add(t["artist"].split(",")[0])

    if disc_artists:
        print(f"New artists found: {', '.join(list(disc_artists)[:6])}")

    print("\nPlaylist:")
    for i, t in enumerate(result["tracks"][:12], 1):
        icon = "H" if t["source"] == "history" else "D"
        via = t.get("discovered_via", "")[:22] if t.get("discovered_via") else ""
        print(f"  {i:2}. [{icon}] {t['track'][:30]:30} - {t['artist'][:20]:20} {via}")


if __name__ == "__main__":
    tests = [
        ("Indie/Lo-fi", ["mac demarco", "tame impala"]),
        ("Folk", ["fleet foxes", "bon iver"]),
        ("Electronic", ["daft punk", "justice"]),
        ("Classic Rock 80% discovery", ["led zeppelin", "deep purple"]),
    ]

    for name, queries in tests:
        anchors = [search_anchor(q) for q in queries]
        anchors = [a for a in anchors if a]

        if len(anchors) < 2:
            print(f"\n{name}: Not enough anchors found")
            continue

        discovery = 80 if "80%" in name else 50
        result = generate_playlist(anchors, discovery_ratio=discovery)
        print_result(name, anchors, result)
