#!/usr/bin/env python3
"""Deterministic regressions for multi-anchor vibe playlist generation.

All Spotify, Last.fm, and archive access is mocked.  These tests deliberately
give one anchor a much larger and slightly stronger candidate neighborhood so
that a global top-N selector cannot accidentally look balanced.
"""

from collections import Counter
from contextlib import ExitStack, contextmanager
import re
import unittest
from unittest.mock import patch

from api.services.custom_playlist import generate_vibe_playlist
from api.services.flow_ordering import compute_playlist_flow_stats, order_playlist


ANCHORS = (
    ("anchor-a", "Anchor A Song", "Anchor A", "artist-anchor-a"),
    ("anchor-b", "Anchor B Song", "Anchor B", "artist-anchor-b"),
    ("anchor-c", "Anchor C Song", "Anchor C", "artist-anchor-c"),
)
ANCHOR_IDS = [anchor_id for anchor_id, _, _, _ in ANCHORS]


def make_track(track_id, title, artist, artist_id, popularity=55):
    return {
        "id": track_id,
        "name": title,
        "artists": [{"id": artist_id, "name": artist}],
        "album": {"images": []},
        "external_urls": {"spotify": f"https://open.spotify.test/{track_id}"},
        "popularity": popularity,
        "preview_url": None,
    }


def normalized_song_key(track):
    artist = (track.get("artist") or "").split(",", 1)[0]
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalize(artist), normalize(track.get("track") or "")


def longest_run(values):
    longest = current = 0
    previous = object()
    for value in values:
        if value == previous:
            current += 1
        else:
            previous = value
            current = 1
        longest = max(longest, current)
    return longest


class VibeFixture:
    """Complete deterministic replacement for the generator's data sources."""

    def __init__(self, strictness_case=False):
        self.strictness_case = strictness_case
        self.catalog = {}
        self.history = {}
        self.artist_records = {}
        self.similar_artists = {}
        self.similar_tracks = {}
        self.exact_lookup = {}
        self.spotify_artist_lookup = {}
        self.artist_top_tracks = {}

        for group, (anchor_id, title, artist, artist_id) in enumerate(ANCHORS):
            anchor = make_track(anchor_id, title, artist, artist_id, popularity=70)
            self.catalog[anchor_id] = anchor
            self.artist_records[artist_id] = {
                "id": artist_id,
                "name": artist,
                "genres": [f"genre-{group}"],
            }

        if strictness_case:
            self._build_strictness_case()
        else:
            self._build_balancing_case()

    def _register_track(self, track):
        self.catalog[track["id"]] = track
        artist = track["artists"][0]
        self.artist_records.setdefault(
            artist["id"],
            {"id": artist["id"], "name": artist["name"], "genres": []},
        )

    def _build_balancing_case(self):
        # Exactly two eligible familiar tracks per anchor plus the anchor itself
        # make the requested nine-track history side fully satisfiable.
        for group, (_, _, anchor_artist, _) in enumerate(ANCHORS):
            label = chr(ord("A") + group)
            artist_evidence = []
            for index in range(2):
                track_id = f"history-{label.lower()}-{index}"
                artist = f"{label} Familiar {index}"
                artist_id = f"artist-history-{label.lower()}-{index}"
                title = f"{label} Familiar Song {index}"
                track = make_track(track_id, title, artist, artist_id, popularity=50)
                self._register_track(track)
                self.history[track_id] = {
                    "track_id": track_id,
                    "track": title,
                    "artist": artist,
                    "play_count": 7,
                    "last_played": "2026-01-01T00:00:00Z",
                }
                artist_evidence.append({"name": artist, "match": 0.92 - index * 0.03})
            # Keep both real history artists near the top of a realistically
            # sized Last.fm neighborhood. With a two-item list, rank 1 is the
            # absolute tail and correctly scores as weak evidence.
            artist_evidence.extend(
                {"name": f"{label} Unresolved Neighbor {index}", "match": 0.40}
                for index in range(8)
            )
            self.similar_artists[anchor_artist] = artist_evidence

        # A deliberately oversized A neighborhood catches global resolution or
        # selection. B and C still have ample candidates for an even 3/3/3 mix.
        discovery_sizes = (24, 8, 8)
        raw_matches = (0.96, 0.88, 0.80)
        for group, (_, anchor_title, anchor_artist, _) in enumerate(ANCHORS):
            label = chr(ord("A") + group)
            items = []
            for index in range(discovery_sizes[group]):
                artist = f"{label} Discovery {index}"
                title = f"{label} Discovery Song {index}"
                track_id = f"discovery-{label.lower()}-{index}"
                artist_id = f"artist-discovery-{label.lower()}-{index}"
                track = make_track(track_id, title, artist, artist_id, popularity=60)
                self._register_track(track)
                self.exact_lookup[(artist, title)] = track
                items.append({
                    "artist": artist,
                    "name": title,
                    "match": raw_matches[group] - index * 0.001,
                })
            self.similar_tracks[(anchor_artist, anchor_title)] = items

        # The same semantic recommendation appears in two neighborhoods. It
        # must remain one track while retaining both anchor affinities.
        shared = make_track(
            "discovery-shared",
            "Shared Bridge",
            "Shared Artist",
            "artist-shared",
            popularity=75,
        )
        self._register_track(shared)
        self.exact_lookup[("Shared Artist", "Shared Bridge")] = shared
        shared_item = {"artist": "Shared Artist", "name": "Shared Bridge", "match": 0.99}
        self.similar_tracks[("Anchor A", "Anchor A Song")].insert(0, dict(shared_item))
        self.similar_tracks[("Anchor B", "Anchor B Song")].insert(0, dict(shared_item))

    def _build_strictness_case(self):
        # Anchors alone exactly satisfy the familiar side. Each anchor has one
        # high raw-match direct track and one extremely weak artist fallback.
        # At strictness .80 the direct tracks should survive and the fallbacks
        # should be rejected, producing an explicit short-result warning.
        for group, (_, anchor_title, anchor_artist, _) in enumerate(ANCHORS):
            label = chr(ord("A") + group)
            direct_artist = f"{label} Direct Artist"
            direct_title = f"{label} Direct Song"
            direct = make_track(
                f"direct-{label.lower()}",
                direct_title,
                direct_artist,
                f"artist-direct-{label.lower()}",
                popularity=65,
            )
            self._register_track(direct)
            self.exact_lookup[(direct_artist, direct_title)] = direct
            self.similar_tracks[(anchor_artist, anchor_title)] = [{
                "artist": direct_artist,
                "name": direct_title,
                "match": 0.98,
            }]

            weak_artist = f"{label} Weak Artist"
            weak_artist_id = f"artist-weak-{label.lower()}"
            weak = make_track(
                f"weak-{label.lower()}",
                f"{label} Weak Song",
                weak_artist,
                weak_artist_id,
                popularity=45,
            )
            self._register_track(weak)
            self.similar_artists[anchor_artist] = [{"name": weak_artist, "match": 0.02}]
            self.spotify_artist_lookup[weak_artist] = {
                "id": weak_artist_id,
                "name": weak_artist,
            }
            self.artist_top_tracks[weak_artist_id] = [weak]

    def get_tracks_bulk(self, track_ids):
        return [self.catalog[track_id] for track_id in track_ids if track_id in self.catalog]

    def get_artists_bulk(self, artist_ids):
        return [self.artist_records[artist_id] for artist_id in artist_ids if artist_id in self.artist_records]

    def get_similar_artists(self, artist, limit=40):
        return list(self.similar_artists.get(artist, []))[:limit]

    def get_similar_tracks_batch(self, pairs, limit=60, max_workers=5):
        return {
            pair: list(self.similar_tracks.get(pair, []))[:limit]
            for pair in pairs
        }

    def resolve_spotify_track(self, artist, title):
        return self.exact_lookup.get((artist, title))

    def search_artist(self, name):
        return self.spotify_artist_lookup.get(name)

    def get_artist_top_tracks(self, artist_id, market="CH"):
        return list(self.artist_top_tracks.get(artist_id, []))

    @contextmanager
    def installed(self):
        target = "api.services.custom_playlist"
        with ExitStack() as stack:
            stack.enter_context(patch(f"{target}.get_tracks_bulk", side_effect=self.get_tracks_bulk))
            stack.enter_context(patch(f"{target}.get_artists_bulk", side_effect=self.get_artists_bulk))
            stack.enter_context(patch(f"{target}.get_all_tracks_with_counts", return_value=self.history))
            stack.enter_context(patch(f"{target}.get_similar_artists", side_effect=self.get_similar_artists))
            stack.enter_context(
                patch(f"{target}.get_similar_tracks_batch", side_effect=self.get_similar_tracks_batch)
            )
            stack.enter_context(
                patch(f"{target}._resolve_spotify_track", side_effect=self.resolve_spotify_track)
            )
            stack.enter_context(patch(f"{target}.search_artist", side_effect=self.search_artist))
            stack.enter_context(
                patch(f"{target}.get_artist_top_tracks", side_effect=self.get_artist_top_tracks)
            )
            yield


def generate_with_fixture(fixture, **overrides):
    options = {
        "anchor_track_ids": ANCHOR_IDS,
        "track_count": 18,
        "discovery_ratio": 50,
        "flow_mode": "smooth",
        "coherence_threshold": 0.50,
        "max_per_anchor_artist": 3,
        "max_per_similar_artist": 2,
    }
    options.update(overrides)
    with fixture.installed():
        return generate_vibe_playlist(**options)


class MultiAnchorVibeRegressionTests(unittest.TestCase):
    def test_balances_sources_and_anchor_mix_without_losing_affinities(self):
        result = generate_with_fixture(VibeFixture())
        tracks = result["tracks"]

        self.assertEqual(len(tracks), 18)
        self.assertEqual(result["counts"]["total"], 18)
        self.assertEqual(result["counts"]["history"], 9)
        self.assertEqual(result["counts"]["discovery"], 9)
        self.assertEqual(result["counts"]["requested_history"], 9)
        self.assertEqual(result["counts"]["requested_discovery"], 9)
        self.assertFalse(result.get("warnings"), result.get("warnings"))

        ids = [track["track_id"] for track in tracks]
        self.assertEqual(len(ids), len(set(ids)), "duplicate Spotify track IDs")
        semantic_keys = [normalized_song_key(track) for track in tracks]
        self.assertEqual(
            len(semantic_keys),
            len(set(semantic_keys)),
            "duplicate normalized artist/title pairs",
        )
        for anchor_id in ANCHOR_IDS:
            self.assertEqual(ids.count(anchor_id), 1, f"anchor {anchor_id} missing or duplicated")

        primary_ids = [track.get("primary_anchor_id") for track in tracks]
        self.assertTrue(all(anchor_id in ANCHOR_IDS for anchor_id in primary_ids))
        primary_counts = Counter(primary_ids)
        self.assertLessEqual(max(primary_counts.values()) - min(primary_counts.values()), 1)
        self.assertLessEqual(longest_run(primary_ids), 3)

        for track in tracks:
            affinities = track.get("anchor_affinities")
            self.assertIsInstance(affinities, dict)
            self.assertTrue(affinities)
            self.assertTrue(set(affinities).issubset(set(ANCHOR_IDS)))
            self.assertTrue(all(0 <= score <= 1 for score in affinities.values()))
            self.assertIn(track["primary_anchor_id"], affinities)
            self.assertTrue(track.get("primary_anchor_name"))

        shared = next(track for track in tracks if track["track_id"] == "discovery-shared")
        self.assertIn("anchor-a", shared["anchor_affinities"])
        self.assertIn("anchor-b", shared["anchor_affinities"])

        mix = {item["anchor_track_id"]: item for item in result["anchor_mix"]}
        self.assertEqual(set(mix), set(ANCHOR_IDS))
        self.assertEqual(sum(item["count"] for item in mix.values()), len(tracks))
        self.assertEqual(sum(item["history"] for item in mix.values()), 9)
        self.assertEqual(sum(item["discovery"] for item in mix.values()), 9)
        for anchor_id in ANCHOR_IDS:
            expected_tracks = [track for track in tracks if track["primary_anchor_id"] == anchor_id]
            self.assertEqual(mix[anchor_id]["count"], len(expected_tracks))
            self.assertEqual(
                mix[anchor_id]["history"],
                sum(track["source"] == "history" for track in expected_tracks),
            )
            self.assertEqual(
                mix[anchor_id]["discovery"],
                sum(track["source"] == "discovery" for track in expected_tracks),
            )

        self.assertEqual(result["flow_stats"]["ordering_basis"], "multi_anchor_similarity")

    def test_strictness_rejects_weak_artist_fallback_and_warns_on_short_result(self):
        fixture = VibeFixture(strictness_case=True)
        permissive = generate_with_fixture(
            fixture,
            track_count=10,
            discovery_ratio=70,
            coherence_threshold=0.0,
        )
        strict = generate_with_fixture(
            fixture,
            track_count=10,
            discovery_ratio=70,
            coherence_threshold=0.80,
        )

        permissive_by_id = {track["track_id"]: track for track in permissive["tracks"]}
        strict_ids = {track["track_id"] for track in strict["tracks"]}
        direct_ids = {"direct-a", "direct-b", "direct-c"}
        weak_ids = {"weak-a", "weak-b", "weak-c"}

        self.assertTrue(direct_ids.issubset(strict_ids))
        self.assertTrue(weak_ids.isdisjoint(strict_ids))
        self.assertTrue(weak_ids.issubset(permissive_by_id))
        direct_floor = min(permissive_by_id[track_id]["coherence_score"] for track_id in direct_ids)
        weak_ceiling = max(permissive_by_id[track_id]["coherence_score"] for track_id in weak_ids)
        self.assertGreaterEqual(direct_floor - weak_ceiling, 0.15)

        self.assertLess(strict["counts"]["total"], 10)
        self.assertEqual(strict["counts"]["total"], len(strict["tracks"]))
        self.assertEqual(strict["counts"]["requested_history"], 3)
        self.assertEqual(strict["counts"]["requested_discovery"], 7)
        self.assertTrue(strict.get("warnings"), "a short strict result needs an explicit warning")
        warning_text = " ".join(strict["warnings"]).casefold()
        self.assertTrue(
            any(word in warning_text for word in ("short", "requested", "available", "strict")),
            strict["warnings"],
        )

    def test_flow_ordering_caps_group_runs_and_counts_no_overlap_as_jarring(self):
        tracks = [
            make_track(f"a-{index}", f"A {index}", f"Artist A {index}", f"aa-{index}")
            for index in range(5)
        ] + [
            make_track(f"b-{index}", f"B {index}", f"Artist B {index}", f"bb-{index}")
            for index in range(5)
        ]
        features = {track["id"]: {} for track in tracks}
        genres = {
            track["id"]: ({"group-a"} if track["id"].startswith("a-") else {"group-b"})
            for track in tracks
        }
        groups = {
            track["id"]: ("anchor-a" if track["id"].startswith("a-") else "anchor-b")
            for track in tracks
        }

        ordered = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=2,
        )
        self.assertLessEqual(longest_run([groups[track["id"]] for track in ordered]), 2)

        stats = compute_playlist_flow_stats(
            tracks[:1] + tracks[5:6],
            features,
            genres,
        )
        self.assertIsNone(stats["max_transition_cost"])
        self.assertIsNone(stats["jarring_transitions"])
        self.assertEqual(stats["measured_transitions"], 0)
        self.assertEqual(stats["total_transitions"], 1)
        self.assertEqual(stats["measurement_basis"], "unavailable")

    def test_equal_groups_use_coherent_runs_with_every_window_covered(self):
        tracks = []
        features = {}
        genres = {}
        groups = {}
        for group in ("a", "b", "c"):
            for index in range(6):
                track = make_track(
                    f"{group}-{index}",
                    f"{group.upper()} {index}",
                    f"Artist {group.upper()} {index}",
                    f"artist-{group}-{index}",
                )
                tracks.append(track)
                features[track["id"]] = {}
                genres[track["id"]] = {f"genre-{group}"}
                groups[track["id"]] = group

        ordered = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=3,
        )
        sequence = [groups[track["id"]] for track in ordered]
        self.assertLessEqual(longest_run(sequence), 3)
        self.assertLessEqual(
            sum(left != right for left, right in zip(sequence, sequence[1:])),
            6,
        )
        for start in range(len(sequence) - 8):
            window = sequence[start:start + 9]
            self.assertEqual(set(window), {"a", "b", "c"})
            self.assertLessEqual(max(Counter(window).values()), 5)

        short_stats = compute_playlist_flow_stats(
            tracks[:1],
            features,
            genres,
        )
        self.assertEqual(short_stats["total_transitions"], 0)

    def test_sparse_genres_prefer_short_phrases_without_anchor_ping_pong(self):
        tracks = []
        features = {}
        genres = {}
        groups = {}
        group_sizes = {"a": 24, "b": 23, "c": 23}
        # Interleave the input so neutral-cost tie breaking alone would switch
        # anchor neighborhoods almost every song, as the live 70-track case did.
        for index in range(24):
            for group in ("a", "b", "c"):
                if index >= group_sizes[group]:
                    continue
                track = make_track(
                    f"sparse-{group}-{index}",
                    f"Sparse {group.upper()} {index}",
                    f"Sparse Artist {group.upper()} {index}",
                    f"artist-sparse-{group}-{index}",
                )
                tracks.append(track)
                features[track["id"]] = {}
                # Most tracks have no Spotify genre metadata. A few share a
                # broad tag across anchors, exercising real transition costs.
                genres[track["id"]] = (
                    {"shared-indie"} if index % 8 == 0 else set()
                )
                groups[track["id"]] = group

        ordered = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=3,
        )
        sequence = [groups[track["id"]] for track in ordered]
        changes = sum(
            left != right for left, right in zip(sequence, sequence[1:])
        )
        self.assertLessEqual(changes, 30)
        self.assertLessEqual(longest_run(sequence), 3)
        for start in range(len(sequence) - 8):
            self.assertEqual(set(sequence[start:start + 9]), {"a", "b", "c"})

    def test_zero_anchor_artist_cap_is_valid_and_shuffle_is_truthful(self):
        result = generate_with_fixture(
            VibeFixture(),
            flow_mode="shuffle",
            max_per_anchor_artist=0,
        )
        self.assertEqual(result["counts"]["total"], 18)
        self.assertEqual(result["flow_stats"]["ordering_basis"], "shuffle")

    def test_remaster_suffix_keeps_originating_direct_track_evidence(self):
        fixture = VibeFixture(strictness_case=True)
        fixture.catalog["direct-a"]["name"] = "A Direct Song - 2026 Remaster"
        result = generate_with_fixture(
            fixture,
            track_count=10,
            discovery_ratio=70,
            coherence_threshold=0.80,
        )
        direct = next(track for track in result["tracks"] if track["track_id"] == "direct-a")
        self.assertEqual(direct["primary_anchor_id"], "anchor-a")
        self.assertGreaterEqual(direct["anchor_affinities"]["anchor-a"], 0.80)

    def test_history_shortfall_acquires_enough_unique_cap_aware_discovery(self):
        fixture = VibeFixture()
        fixture.history = {}
        fixture.similar_tracks = {
            (anchor_artist, anchor_title): []
            for _, anchor_title, anchor_artist, _ in ANCHORS
        }
        fixture.similar_artists = {}
        fixture.spotify_artist_lookup = {}
        fixture.artist_top_tracks = {}

        for group, (_, _, anchor_artist, _) in enumerate(ANCHORS):
            label = chr(ord("A") + group)
            related = []
            for artist_index in range(12):
                artist = f"{label} Remote Artist {artist_index}"
                artist_id = f"artist-remote-{label.lower()}-{artist_index}"
                related.append({"name": artist, "match": 0.9})
                fixture.spotify_artist_lookup[artist] = {
                    "id": artist_id,
                    "name": artist,
                }
                top_tracks = []
                for track_index in range(3):
                    track = make_track(
                        f"remote-{label.lower()}-{artist_index}-{track_index}",
                        f"{label} Remote {artist_index}-{track_index}",
                        artist,
                        artist_id,
                    )
                    fixture._register_track(track)
                    top_tracks.append(track)
                fixture.artist_top_tracks[artist_id] = top_tracks
            fixture.similar_artists[anchor_artist] = related

        result = generate_with_fixture(
            fixture,
            track_count=70,
            discovery_ratio=60,
            coherence_threshold=0.0,
        )
        self.assertEqual(result["counts"]["total"], 70)
        self.assertEqual(result["counts"]["history"], 3)
        self.assertEqual(result["counts"]["discovery"], 67)
        mix_counts = [item["count"] for item in result["anchor_mix"]]
        self.assertLessEqual(max(mix_counts) - min(mix_counts), 1)
        for track in result["tracks"]:
            self.assertGreater(
                track["anchor_affinities"][track["primary_anchor_id"]],
                0,
            )

    def test_strict_artist_fallback_short_circuits_without_searches(self):
        fixture = VibeFixture(strictness_case=True)
        target = "api.services.custom_playlist"
        with fixture.installed(), patch(f"{target}.search_artist") as search_mock:
            result = generate_vibe_playlist(
                anchor_track_ids=ANCHOR_IDS,
                track_count=70,
                discovery_ratio=60,
                coherence_threshold=0.80,
            )
        search_mock.assert_not_called()
        self.assertLess(result["counts"]["total"], 70)

    def test_duplicate_anchor_ids_are_rejected_before_network_access(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            generate_vibe_playlist(
                anchor_track_ids=["duplicate", "duplicate"],
                track_count=10,
            )

    def test_direct_only_large_request_resolves_beyond_sixty_leads(self):
        fixture = VibeFixture()
        fixture.history = {}
        fixture.similar_artists = {
            artist: [] for _, _, artist, _ in ANCHORS
        }
        fixture.similar_tracks = {}
        fixture.exact_lookup = {}
        direct_ids = set()
        for group, (_, anchor_title, anchor_artist, _) in enumerate(ANCHORS):
            label = chr(ord("A") + group)
            rows = []
            for index in range(60):
                track = make_track(
                    f"large-direct-{label.lower()}-{index}",
                    f"{label} Large Direct {index}",
                    f"{label} Direct Artist {index}",
                    f"artist-large-direct-{label.lower()}-{index}",
                )
                fixture._register_track(track)
                direct_ids.add(track["id"])
                fixture.exact_lookup[
                    (track["artists"][0]["name"], track["name"])
                ] = track
                rows.append({
                    "artist": track["artists"][0]["name"],
                    "name": track["name"],
                    "match": 0.9,
                })
            fixture.similar_tracks[(anchor_artist, anchor_title)] = rows

        result = generate_with_fixture(
            fixture,
            track_count=100,
            discovery_ratio=100,
            coherence_threshold=0.6,
            max_per_similar_artist=1,
        )
        self.assertEqual(result["counts"]["total"], 100)
        self.assertEqual(result["counts"]["history"], 3)
        self.assertEqual(result["counts"]["discovery"], 97)
        discovery_ids = {
            track["track_id"] for track in result["tracks"]
            if track["source"] == "discovery"
        }
        self.assertTrue(discovery_ids.issubset(direct_ids))
        mix_counts = [item["count"] for item in result["anchor_mix"]]
        self.assertLessEqual(max(mix_counts) - min(mix_counts), 1)
        self.assertFalse(any(
            warning.startswith("Returned ") for warning in result["warnings"]
        ))

    def test_shared_candidates_use_a_feasible_fair_matching(self):
        fixture = VibeFixture()
        fixture.similar_tracks = {}
        fixture.exact_lookup = {}
        rows_by_anchor = {"Anchor A": [], "Anchor B": [], "Anchor C": []}
        definitions = {
            "shared-x": ("Shared X", "Artist X", ("Anchor A", "Anchor C"), 0.99),
            "shared-y": ("Shared Y", "Artist Y", ("Anchor B", "Anchor C"), 0.98),
            "unique-z": ("Unique Z", "Artist Z", ("Anchor B",), 0.75),
        }
        for track_id, (title, artist, anchors, match) in definitions.items():
            track = make_track(track_id, title, artist, f"artist-{track_id}")
            fixture._register_track(track)
            fixture.exact_lookup[(artist, title)] = track
            for anchor_artist in anchors:
                rows_by_anchor[anchor_artist].append({
                    "artist": artist,
                    "name": title,
                    "match": match,
                })
        for _, anchor_title, anchor_artist, _ in ANCHORS:
            fixture.similar_tracks[(anchor_artist, anchor_title)] = (
                rows_by_anchor[anchor_artist]
            )

        result = generate_with_fixture(
            fixture,
            track_count=10,
            discovery_ratio=30,
        )
        discoveries = [
            track for track in result["tracks"] if track["source"] == "discovery"
        ]
        self.assertEqual(
            {track["track_id"] for track in discoveries},
            set(definitions),
        )
        self.assertEqual(
            Counter(track["primary_anchor_id"] for track in discoveries),
            Counter(ANCHOR_IDS),
        )
        self.assertFalse(any(
            warning.startswith("Discovery anchor shortages")
            for warning in result["warnings"]
        ))


if __name__ == "__main__":
    unittest.main()
