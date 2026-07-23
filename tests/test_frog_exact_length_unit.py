"""Deterministic regression tests for Frog Mode's exact-length route builder."""

import unittest
from unittest.mock import patch

from api.services.frog_playlist import (
    astar_find_path_streaming,
    expand_path_to_exact_length,
    track_key,
)


def node(name, match=1.0):
    return {"artist": f"Artist {name}", "name": f"Track {name}", "match": match}


class FrogExactLengthTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {name: node(name) for name in ("A", "B", "C", "D", "Z")}
        # A dense high-similarity neighborhood models the triangles used to
        # subdivide a route without introducing a large jump.
        self.graph = {}
        order = ["A", "B", "C", "D", "Z"]
        for left_index, left in enumerate(order):
            self.graph[track_key(self.nodes[left])] = [
                {
                    **self.nodes[right],
                    "match": 0.98 - (abs(left_index - right_index) * 0.01),
                }
                for right_index, right in enumerate(order)
                if right != left
            ]

    def fetch(self, tracks, limit=100, max_workers=20):
        del limit, max_workers
        return {
            pair: self.graph.get(
                track_key({"artist": pair[0], "name": pair[1]}),
                [],
            )
            for pair in tracks
        }

    @staticmethod
    def resolve(artist, track):
        return {
            "id": f"{artist}:{track}",
            "name": track,
            "artists": [{"name": artist}],
            "album": {"name": "Test", "images": []},
            "external_urls": {},
        }

    def test_expands_to_exact_requested_length_with_distinct_endpoints(self):
        start = {**self.nodes["A"], "_spotify": self.resolve("Artist A", "Track A")}
        end = {**self.nodes["Z"], "_spotify": self.resolve("Artist Z", "Track Z")}

        route, quality = expand_path_to_exact_length(
            [start, end],
            5,
            spotify_resolver=self.resolve,
            similarity_fetcher=self.fetch,
        )

        self.assertIsNotNone(route)
        self.assertEqual(5, len(route))
        self.assertEqual(track_key(start), track_key(route[0]))
        self.assertEqual(track_key(end), track_key(route[-1]))
        self.assertEqual(5, len({item["_spotify"]["id"] for item in route}))
        self.assertGreaterEqual(quality["weakest_transition"], 0.94)
        self.assertEqual(4, len(quality["transition_scores"]))

    def test_refuses_to_fake_length_with_duplicate_spotify_tracks(self):
        start = {**self.nodes["A"], "_spotify": {"id": "only-one"}}
        end = {**self.nodes["Z"], "_spotify": {"id": "end"}}

        route, details = expand_path_to_exact_length(
            [start, end],
            5,
            spotify_resolver=lambda _artist, _track: {"id": "only-one"},
            similarity_fetcher=self.fetch,
        )

        self.assertIsNone(route)
        self.assertEqual(2, details["built_length"])
        self.assertIn("distinct Spotify tracks", details["error"])

    def test_thirty_song_request_returns_thirty_scored_songs(self):
        names = [f"N{index:02d}" for index in range(30)]
        nodes = {name: node(name) for name in names}
        graph = {
            track_key(left): [
                {**right, "match": 0.95}
                for right in nodes.values()
                if track_key(right) != track_key(left)
            ]
            for left in nodes.values()
        }

        def fetch(tracks, limit=100, max_workers=20):
            del limit, max_workers
            return {
                pair: graph[track_key({"artist": pair[0], "name": pair[1]})]
                for pair in tracks
            }

        start = {**nodes[names[0]], "_spotify": self.resolve("Artist N00", "Track N00")}
        end = {**nodes[names[-1]], "_spotify": self.resolve("Artist N29", "Track N29")}
        route, quality = expand_path_to_exact_length(
            [start, end],
            30,
            spotify_resolver=self.resolve,
            similarity_fetcher=fetch,
        )

        self.assertIsNotNone(route)
        self.assertEqual(30, len(route))
        self.assertEqual(30, len({item["_spotify"]["id"] for item in route}))
        self.assertEqual(29, len(quality["transition_scores"]))
        self.assertEqual(0.95, quality["weakest_transition"])

    def test_returns_best_exact_route_below_the_smoothness_target(self):
        start = {**self.nodes["A"], "_spotify": self.resolve("Artist A", "Track A")}
        end = {
            **self.nodes["Z"],
            "match": 0.04,
            "_spotify": self.resolve("Artist Z", "Track Z"),
        }

        def sparse_fetch(tracks, limit=100, max_workers=20):
            del limit, max_workers
            return {pair: [] for pair in tracks}

        route, details = expand_path_to_exact_length(
            [start, end],
            2,
            spotify_resolver=self.resolve,
            similarity_fetcher=sparse_fetch,
        )

        self.assertIsNotNone(route)
        self.assertEqual(2, len(route))
        self.assertEqual(0.04, details["weakest_transition"])
        self.assertFalse(details["meets_smoothness_target"])
        self.assertIn("below the 12% smoothness target", details["quality_warning"])

    def test_fifty_song_request_grows_in_batches_with_progress(self):
        names = [f"N{index:02d}" for index in range(50)]
        nodes = {name: node(name) for name in names}
        graph = {
            track_key(left): [
                {**right, "match": 0.93}
                for right in nodes.values()
                if track_key(right) != track_key(left)
            ]
            for left in nodes.values()
        }
        fetch_calls = []
        progress = []

        def fetch(tracks, limit=100, max_workers=20):
            del limit, max_workers
            fetch_calls.append(list(tracks))
            return {
                pair: graph[track_key({"artist": pair[0], "name": pair[1]})]
                for pair in tracks
            }

        start = {**nodes[names[0]], "_spotify": self.resolve("Artist N00", "Track N00")}
        end = {**nodes[names[-1]], "_spotify": self.resolve("Artist N49", "Track N49")}
        route, quality = expand_path_to_exact_length(
            [start, end],
            50,
            spotify_resolver=self.resolve,
            similarity_fetcher=fetch,
            progress_callback=progress.append,
        )

        self.assertIsNotNone(route)
        self.assertEqual(50, len(route))
        self.assertEqual(50, len({item["_spotify"]["id"] for item in route}))
        self.assertEqual(49, len(quality["transition_scores"]))
        # Growth is batched: substantially fewer Last.fm rounds than the 48
        # one-track-at-a-time requests this route previously required.
        self.assertLessEqual(len(fetch_calls), 14)
        self.assertGreaterEqual(len(progress), 6)
        self.assertEqual(50, progress[-1]["built_length"])
        self.assertEqual(50, progress[-1]["target_length"])

    def test_expansion_deadline_returns_partial_length(self):
        start = {**self.nodes["A"], "_spotify": self.resolve("Artist A", "Track A")}
        end = {**self.nodes["Z"], "_spotify": self.resolve("Artist Z", "Track Z")}

        route, details = expand_path_to_exact_length(
            [start, end],
            5,
            spotify_resolver=self.resolve,
            similarity_fetcher=self.fetch,
            max_seconds=0,
        )

        self.assertIsNone(route)
        self.assertTrue(details["timed_out"])
        self.assertEqual(2, details["built_length"])

    def test_bidirectional_search_meets_on_discovered_frontier(self):
        start = node("A")
        middle = node("M", match=0.8)
        end = node("Z")
        calls = []

        def fetch(tracks, limit=30, max_workers=20):
            del limit, max_workers
            calls.append(list(tracks))
            return {
                (start["artist"], start["name"]): [middle],
                (end["artist"], end["name"]): [middle],
            }

        with patch("api.services.frog_playlist.get_similar_tracks_batch", fetch):
            events = list(astar_find_path_streaming(start, end))

        result = next(event for event in events if event["type"] == "result")
        self.assertEqual(
            [track_key(start), track_key(middle), track_key(end)],
            [track_key(item) for item in result["path"]],
        )
        self.assertEqual(1, result["iterations"])
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
