#!/usr/bin/env python3
"""Regressions for evidence-path diversity and credited-artist coverage."""

from collections import Counter
from contextlib import ExitStack, contextmanager
import threading
import unittest
from unittest.mock import patch

from api.services.custom_playlist import generate_vibe_playlist


def make_track(track_id, title, credits, popularity=50):
    return {
        "id": track_id,
        "name": title,
        "artists": [
            {"id": artist_id, "name": artist_name}
            for artist_id, artist_name in credits
        ],
        "album": {"images": []},
        "external_urls": {"spotify": f"https://open.spotify.test/{track_id}"},
        "popularity": popularity,
        "preview_url": None,
    }


class LaneFixture:
    def __init__(self):
        self.catalog = {}
        self.artist_records = {}
        self.similar_artists = {}
        self.similar_tracks = {}
        self.exact_lookup = {}
        self.spotify_artist_lookup = {}
        self.artist_top_tracks = {}
        self.history = {}
        self.anchors = []
        self.resolve_calls = []

    def register(self, track, genres=None):
        self.catalog[track["id"]] = track
        for artist in track["artists"]:
            self.artist_records.setdefault(
                artist["id"],
                {
                    "id": artist["id"],
                    "name": artist["name"],
                    "genres": list(genres or []),
                },
            )
        return track

    def add_anchor(self, track_id, title, credits, genre):
        track = self.register(make_track(track_id, title, credits, 70), [genre])
        self.anchors.append(track_id)
        return track

    def add_direct(self, anchor_artist, anchor_title, prefix, count, raw_match):
        rows = self.similar_tracks.setdefault((anchor_artist, anchor_title), [])
        ids = set()
        for index in range(count):
            artist = f"{prefix} Direct Artist {index}"
            title = f"{prefix} Direct Song {index}"
            track = self.register(make_track(
                f"{prefix.lower()}-direct-{index}",
                title,
                [(f"artist-{prefix.lower()}-direct-{index}", artist)],
                55,
            ))
            self.exact_lookup[(artist, title)] = track
            rows.append({"artist": artist, "name": title, "match": raw_match})
            ids.add(track["id"])
        return ids

    def add_supported_artist(self, anchor_artist, name, prefix, track_count):
        artist_id = f"artist-{prefix.lower()}-supported"
        self.similar_artists.setdefault(anchor_artist, []).append({
            "name": name,
            "match": 1.0,
        })
        self.spotify_artist_lookup[name] = {"id": artist_id, "name": name}
        tracks = []
        for index in range(track_count):
            track = self.register(make_track(
                f"{prefix.lower()}-supported-{index}",
                f"{prefix} Supported Song {index}",
                [(artist_id, name)],
                50,
            ))
            tracks.append(track)
        self.artist_top_tracks[artist_id] = tracks
        return {track["id"] for track in tracks}

    def get_tracks_bulk(self, track_ids):
        return [self.catalog[track_id] for track_id in track_ids if track_id in self.catalog]

    def get_artists_bulk(self, artist_ids):
        return [
            self.artist_records[artist_id]
            for artist_id in artist_ids
            if artist_id in self.artist_records
        ]

    def get_similar_artists(self, artist, limit=40):
        return list(self.similar_artists.get(artist, []))[:limit]

    def get_similar_tracks_batch(self, pairs, limit=60, max_workers=5):
        return {
            pair: list(self.similar_tracks.get(pair, []))[:limit]
            for pair in pairs
        }

    def resolve_track(self, artist, title):
        self.resolve_calls.append((artist, title))
        return self.exact_lookup.get((artist, title))

    def search_artist(self, name):
        return self.spotify_artist_lookup.get(name)

    def get_artist_top_tracks(self, artist_id, market="CH"):
        return list(self.artist_top_tracks.get(artist_id, []))

    @contextmanager
    def installed(self):
        target = "api.services.custom_playlist"
        with ExitStack() as stack:
            stack.enter_context(patch(
                f"{target}.get_tracks_bulk", side_effect=self.get_tracks_bulk
            ))
            stack.enter_context(patch(
                f"{target}.get_artists_bulk", side_effect=self.get_artists_bulk
            ))
            stack.enter_context(patch(
                f"{target}.get_all_tracks_with_counts", return_value=self.history
            ))
            stack.enter_context(patch(
                f"{target}.get_similar_artists", side_effect=self.get_similar_artists
            ))
            stack.enter_context(patch(
                f"{target}.get_similar_tracks_batch",
                side_effect=self.get_similar_tracks_batch,
            ))
            stack.enter_context(patch(
                f"{target}._resolve_spotify_track", side_effect=self.resolve_track
            ))
            stack.enter_context(patch(
                f"{target}.search_artist", side_effect=self.search_artist
            ))
            stack.enter_context(patch(
                f"{target}.get_artist_top_tracks",
                side_effect=self.get_artist_top_tracks,
            ))
            yield


def generate(fixture, **overrides):
    options = {
        "anchor_track_ids": fixture.anchors,
        "track_count": 10,
        "discovery_ratio": 80,
        "flow_mode": "smooth",
        "coherence_threshold": 0.0,
        "max_per_anchor_artist": 3,
        "max_per_similar_artist": 3,
    }
    options.update(overrides)
    with fixture.installed():
        return generate_vibe_playlist(**options)


def relaxation_warnings(result):
    return [
        warning for warning in result["warnings"]
        if warning.startswith("Direct-evidence soft cap relaxed")
    ]


class SupportedLaneRegressionTests(unittest.TestCase):
    def make_rich_fixture(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "A Anchor Song", [("artist-a", "A Seed")], "genre-a"
        )
        fixture.add_anchor(
            "anchor-b",
            "B Anchor Song",
            [("artist-b-main", "B Main"), ("artist-b-guest", "B Guest")],
            "genre-b",
        )
        fixture.a_direct_ids = fixture.add_direct(
            "A Seed", "A Anchor Song", "A", 20, 0.98
        )
        fixture.a_supported_ids = fixture.add_supported_artist(
            "A Seed", "A Supported Artist", "A", 3
        )
        fixture.b_supported_ids = fixture.add_supported_artist(
            "B Main", "B Supported Artist", "B", 2
        )

        b_main_catalog = fixture.register(make_track(
            "b-main-catalog",
            "B Main Catalog Song",
            [("artist-b-main", "B Main")],
            60,
        ))
        b_guest_catalog = fixture.register(make_track(
            "b-guest-catalog",
            "B Guest Catalog Song",
            [("artist-b-guest", "B Guest")],
            60,
        ))
        fixture.artist_top_tracks["artist-b-main"] = [b_main_catalog]
        fixture.artist_top_tracks["artist-b-guest"] = [b_guest_catalog]
        return fixture

    def test_supported_neighbors_and_each_credited_artist_survive_direct_swarm(self):
        fixture = self.make_rich_fixture()
        result = generate(fixture)
        ids = {track["track_id"] for track in result["tracks"]}

        self.assertEqual(result["counts"]["total"], 10)
        self.assertEqual(result["counts"]["history"], 2)
        self.assertEqual(result["counts"]["discovery"], 8)
        self.assertEqual(
            Counter(track["primary_anchor_id"] for track in result["tracks"]),
            Counter({"anchor-a": 5, "anchor-b": 5}),
        )
        self.assertTrue(fixture.a_supported_ids.issubset(ids))
        self.assertIn("b-main-catalog", ids)
        self.assertIn("b-guest-catalog", ids)
        self.assertEqual(len(ids & fixture.a_direct_ids), 1)
        self.assertFalse(relaxation_warnings(result), result["warnings"])

    def test_direct_soft_cap_relaxes_once_only_when_supported_pool_is_empty(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "A Anchor Song", [("artist-a", "A Seed")], "genre-a"
        )
        fixture.add_anchor(
            "anchor-b", "B Anchor Song", [("artist-b", "B Seed")], "genre-b"
        )
        a_ids = fixture.add_direct("A Seed", "A Anchor Song", "A", 20, 0.98)
        b_ids = fixture.add_direct("B Seed", "B Anchor Song", "B", 20, 0.98)

        result = generate(
            fixture,
            track_count=30,
            discovery_ratio=94,
            max_per_similar_artist=1,
        )
        ids = {track["track_id"] for track in result["tracks"]}
        self.assertEqual(result["counts"]["total"], 30)
        self.assertEqual(result["counts"]["history"], 2)
        self.assertEqual(result["counts"]["discovery"], 28)
        self.assertEqual(
            Counter(track["primary_anchor_id"] for track in result["tracks"]),
            Counter({"anchor-a": 15, "anchor-b": 15}),
        )
        self.assertEqual(len(ids & (a_ids | b_ids)), 28)
        self.assertEqual(len(relaxation_warnings(result)), 1, result["warnings"])

    def test_low_raw_match_plateau_does_not_clear_default_strictness(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "Plateau Anchor", [("artist-a", "A Seed")], "genre-a"
        )
        strong_id = next(iter(fixture.add_direct(
            "A Seed", "Plateau Anchor", "Strong", 1, 1.0
        )))
        plateau_ids = fixture.add_direct(
            "A Seed", "Plateau Anchor", "Plateau", 59, 0.0622924
        )
        variant = fixture.register(make_track(
            "explicit-speed-variant",
            "Catalog Song - Sped Up Version",
            [("artist-speed-variant", "Speed Variant Artist")],
            55,
        ))
        fixture.exact_lookup[("Speed Variant Artist", "Catalog Song")] = variant
        fixture.similar_tracks[("A Seed", "Plateau Anchor")].insert(1, {
            "artist": "Speed Variant Artist",
            "name": "Catalog Song",
            "match": 0.99,
        })

        # A realistically sized artist list keeps the first eight neighbors
        # above the .50 threshold without relying on the one-item rank edge.
        supported_ids = set()
        neighbors = []
        for index in range(40):
            name = f"Supported Neighbor {index}"
            neighbors.append({"name": name, "match": 1.0})
            if index >= 8:
                continue
            artist_id = f"artist-supported-{index}"
            fixture.spotify_artist_lookup[name] = {"id": artist_id, "name": name}
            track = fixture.register(make_track(
                f"supported-{index}",
                f"Supported Song {index}",
                [(artist_id, name)],
                50,
            ))
            fixture.artist_top_tracks[artist_id] = [track]
            supported_ids.add(track["id"])
        fixture.similar_artists["A Seed"] = neighbors

        result = generate(
            fixture,
            track_count=10,
            discovery_ratio=90,
            coherence_threshold=0.50,
            max_per_similar_artist=1,
        )
        tracks_by_id = {track["track_id"]: track for track in result["tracks"]}
        self.assertEqual(result["counts"]["total"], 10)
        self.assertIn(strong_id, tracks_by_id)
        self.assertEqual(tracks_by_id[strong_id]["evidence_raw_match"], 1.0)
        self.assertTrue(plateau_ids.isdisjoint(tracks_by_id))
        self.assertTrue(supported_ids.issubset(tracks_by_id))
        self.assertNotIn("explicit-speed-variant", tracks_by_id)
        self.assertEqual(
            set(fixture.resolve_calls),
            {
                ("Strong Direct Artist 0", "Strong Direct Song 0"),
                ("Speed Variant Artist", "Catalog Song"),
            },
            "below-threshold direct leads must be pruned before Spotify calls",
        )

    def test_weak_direct_edge_is_kept_when_its_artist_support_is_strong(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "Artist Gate Anchor", [("artist-a", "A Seed")], "genre-a"
        )
        artist_name = "Strong Supported Artist"
        artist_id = "artist-strong-supported"
        fixture.similar_artists["A Seed"] = [
            {"name": artist_name, "match": 1.0},
            *(
                {"name": f"Unused Neighbor {index}", "match": 0.1}
                for index in range(39)
            ),
        ]
        weak = fixture.register(make_track(
            "weak-track-strong-artist",
            "Weak Track Edge",
            [(artist_id, artist_name)],
            50,
        ))
        fixture.exact_lookup[(artist_name, "Weak Track Edge")] = weak
        fixture.similar_tracks[("A Seed", "Artist Gate Anchor")] = [{
            "artist": artist_name,
            "name": "Weak Track Edge",
            "match": 0.01,
        }]
        fixture.spotify_artist_lookup[artist_name] = {
            "id": artist_id,
            "name": artist_name,
        }
        supported_tracks = []
        for index in range(3):
            supported_tracks.append(fixture.register(make_track(
                f"strong-supported-{index}",
                f"Strong Supported {index}",
                [(artist_id, artist_name)],
                50,
            )))
        fixture.artist_top_tracks[artist_id] = supported_tracks

        result = generate(
            fixture,
            track_count=10,
            discovery_ratio=90,
            coherence_threshold=0.50,
            max_per_similar_artist=10,
        )
        self.assertIn((artist_name, "Weak Track Edge"), fixture.resolve_calls)
        self.assertIn(
            "weak-track-strong-artist",
            {track["track_id"] for track in result["tracks"]},
        )

    def test_slow_direct_pool_cannot_consume_artist_acquisition_budget(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "Slow Direct Anchor", [("artist-a", "A Seed")], "genre-a"
        )
        fixture.add_direct(
            "A Seed", "Slow Direct Anchor", "Slow", 30, 0.98
        )
        # Every direct lookup is unresolved and advances the deterministic
        # acquisition clock.  The six-item direct batch consumes ~20 seconds;
        # the old 30-item batch consumed the entire 45-second shared budget.
        clock = [0.0]
        lock = threading.Lock()

        def monotonic():
            with lock:
                return clock[0]

        def slow_unresolved(artist, title):
            fixture.resolve_calls.append((artist, title))
            with lock:
                clock[0] += 3.4
            return None

        fixture.resolve_track = slow_unresolved
        supported_ids = set()
        neighbors = []
        for index in range(40):
            name = f"Budget Neighbor {index}"
            neighbors.append({"name": name, "match": 1.0})
            if index >= 3:
                continue
            artist_id = f"artist-budget-{index}"
            fixture.spotify_artist_lookup[name] = {"id": artist_id, "name": name}
            top_tracks = []
            for track_index in range(3):
                track = fixture.register(make_track(
                    f"budget-supported-{index}-{track_index}",
                    f"Budget Supported {index}-{track_index}",
                    [(artist_id, name)],
                    50,
                ))
                top_tracks.append(track)
                supported_ids.add(track["id"])
            fixture.artist_top_tracks[artist_id] = top_tracks
        fixture.similar_artists["A Seed"] = neighbors

        with patch(
            "api.services.custom_playlist.time.monotonic",
            side_effect=monotonic,
        ):
            result = generate(
                fixture,
                track_count=10,
                discovery_ratio=90,
                coherence_threshold=0.50,
            )
        ids = {track["track_id"] for track in result["tracks"]}
        self.assertEqual(result["counts"]["total"], 10)
        self.assertEqual(result["counts"]["discovery"], 9)
        self.assertTrue(supported_ids.issubset(ids))
        self.assertEqual(len(fixture.resolve_calls), 6)

    def test_direct_only_pool_resumes_after_reserved_artist_window(self):
        fixture = LaneFixture()
        fixture.add_anchor(
            "anchor-a", "Direct Only Anchor", [("artist-a", "A Seed")], "genre-a"
        )
        fixture.add_direct(
            "A Seed", "Direct Only Anchor", "DirectOnly", 30, 0.98
        )
        clock = [0.0]
        lock = threading.Lock()

        def monotonic():
            with lock:
                return clock[0]

        original_resolve = fixture.resolve_track

        def slow_success(artist, title):
            with lock:
                clock[0] += 3.4
            return original_resolve(artist, title)

        fixture.resolve_track = slow_success
        with patch(
            "api.services.custom_playlist.time.monotonic",
            side_effect=monotonic,
        ):
            result = generate(
                fixture,
                track_count=10,
                discovery_ratio=90,
                coherence_threshold=0.80,
                max_per_similar_artist=1,
            )
        self.assertEqual(result["counts"]["total"], 10)
        self.assertEqual(result["counts"]["history"], 1)
        self.assertEqual(result["counts"]["discovery"], 9)
        self.assertGreater(len(fixture.resolve_calls), 6)


if __name__ == "__main__":
    unittest.main()
