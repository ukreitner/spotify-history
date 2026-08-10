#!/usr/bin/env python3
"""Focused regressions for evidence-aware flow ordering."""

import unittest

from api.services.flow_ordering import (
    _sequence_proxy_cost,
    compute_playlist_flow_stats,
    order_playlist,
)


def make_track(track_id: str, name: str, artist_id: str) -> dict:
    return {
        "id": track_id,
        "name": name,
        "artists": [{"id": artist_id, "name": artist_id}],
        "album": {"images": []},
    }


class FixedGroupFlowRefinementTests(unittest.TestCase):
    def test_bridge_moves_to_boundary_without_changing_group_schedule(self):
        tracks = [
            make_track("a-open", "A Open", "artist-a-open"),
            make_track("a-deep", "A Deep", "artist-a-deep"),
            make_track("b-deep", "B Deep", "artist-b-deep"),
            make_track("b-bridge", "B Bridge", "artist-b-bridge"),
        ]
        features = {track["id"]: {} for track in tracks}
        genres = {track["id"]: set() for track in tracks}
        groups = {
            "a-open": "anchor-a",
            "a-deep": "anchor-a",
            "b-deep": "anchor-b",
            "b-bridge": "anchor-b",
        }
        affinities = {
            "a-open": {"anchor-a": 1.0},
            "a-deep": {"anchor-a": 0.9},
            "b-deep": {"anchor-b": 0.9},
            "b-bridge": {"anchor-b": 0.9, "anchor-a": 0.85},
        }

        baseline = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=2,
        )
        refined = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=2,
            affinities_map=affinities,
        )

        baseline_groups = [groups[track["id"]] for track in baseline]
        refined_groups = [groups[track["id"]] for track in refined]
        self.assertEqual(refined_groups, baseline_groups)
        self.assertEqual(refined[0]["id"], "a-open", "position zero must stay pinned")
        self.assertEqual(refined[2]["id"], "b-bridge")
        self.assertLess(
            _sequence_proxy_cost(refined, features, genres, groups, affinities),
            _sequence_proxy_cost(baseline, features, genres, groups, affinities),
        )

    def test_missing_audio_separates_same_artist_live_version(self):
        tracks = [
            make_track("opener", "Studio Opener", "same-artist"),
            make_track("live", "Big Song - Live", "same-artist"),
            make_track("studio", "Quiet Studio Song", "other-artist"),
        ]
        features = {track["id"]: {} for track in tracks}
        genres = {track["id"]: set() for track in tracks}
        groups = {track["id"]: "anchor-a" for track in tracks}
        affinities = {track["id"]: {"anchor-a": 0.9} for track in tracks}

        refined = order_playlist(
            tracks,
            features,
            genres,
            "smooth",
            group_map=groups,
            max_group_run=3,
            affinities_map=affinities,
        )

        self.assertEqual(
            [track["id"] for track in refined],
            ["opener", "studio", "live"],
        )
        self.assertEqual(
            [groups[track["id"]] for track in refined],
            ["anchor-a", "anchor-a", "anchor-a"],
        )


class HonestFlowStatsTests(unittest.TestCase):
    def test_unavailable_audio_metrics_are_null_not_neutral_scores(self):
        tracks = [
            make_track("one", "One", "artist-one"),
            make_track("two", "Two", "artist-two"),
            make_track("three", "Three", "artist-three"),
        ]
        stats = compute_playlist_flow_stats(
            tracks,
            {track["id"]: {} for track in tracks},
            {
                "one": {"indie"},
                "two": {"pop"},
                "three": {"rock"},
            },
        )

        self.assertEqual(stats["total_transitions"], 2)
        self.assertEqual(stats["measured_transitions"], 0)
        self.assertEqual(stats["measurement_basis"], "unavailable")
        for field in (
            "avg_transition_cost",
            "max_transition_cost",
            "smooth_transitions",
            "jarring_transitions",
        ):
            self.assertIsNone(stats[field])

    def test_partial_audio_measures_only_pairs_with_two_feature_vectors(self):
        tracks = [
            make_track("one", "One", "artist-one"),
            make_track("two", "Two", "artist-two"),
            make_track("three", "Three", "artist-three"),
        ]
        stats = compute_playlist_flow_stats(
            tracks,
            {
                "one": {"energy": 0.2, "tempo": 100, "valence": 0.4},
                "two": {"energy": 0.3, "tempo": 110, "valence": 0.5},
                "three": {},
            },
            {track["id"]: set() for track in tracks},
        )

        self.assertEqual(stats["total_transitions"], 2)
        self.assertEqual(stats["measured_transitions"], 1)
        self.assertEqual(stats["measurement_basis"], "partial_audio_features")
        self.assertEqual(stats["avg_transition_cost"], 0.155)
        self.assertEqual(stats["max_transition_cost"], 0.155)
        self.assertEqual(stats["smooth_transitions"], 1)
        self.assertEqual(stats["jarring_transitions"], 0)

    def test_complete_audio_labels_every_transition_as_measured(self):
        tracks = [
            make_track("one", "One", "artist-one"),
            make_track("two", "Two", "artist-two"),
            make_track("three", "Three", "artist-three"),
        ]
        features = {
            track["id"]: {"energy": 0.4, "tempo": 110, "valence": 0.5}
            for track in tracks
        }
        stats = compute_playlist_flow_stats(
            tracks,
            features,
            {track["id"]: set() for track in tracks},
        )

        self.assertEqual(stats["total_transitions"], 2)
        self.assertEqual(stats["measured_transitions"], 2)
        self.assertEqual(stats["measurement_basis"], "audio_features")
        self.assertIsNotNone(stats["avg_transition_cost"])


if __name__ == "__main__":
    unittest.main()
