"""Deterministic regression tests for Frog Mode's exact-length route builder."""

import unittest

from api.services.frog_playlist import expand_path_to_exact_length, track_key


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

    def test_rejects_a_route_below_the_smoothness_floor(self):
        start = {**self.nodes["A"], "_spotify": self.resolve("Artist A", "Track A")}
        end = {
            **self.nodes["Z"],
            "match": 0.4,
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

        self.assertIsNone(route)
        self.assertEqual(0.4, details["weakest_transition"])
        self.assertIn("smoothness floor", details["error"])


if __name__ == "__main__":
    unittest.main()
