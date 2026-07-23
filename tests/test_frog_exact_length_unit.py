"""Deterministic regression tests for Frog Mode's exact-length route builder."""

import unittest
from unittest.mock import patch

from api.services.frog_playlist import (
    astar_find_path_streaming,
    expand_path_to_exact_length,
    get_frog_alternatives,
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

    def test_search_progress_contains_real_graph_delta(self):
        start = node("A")
        middle = node("M", match=0.8)
        end = node("Z")

        def fetch(tracks, limit=30, max_workers=20):
            del limit, max_workers
            return {
                (start["artist"], start["name"]): [middle],
                (end["artist"], end["name"]): [middle],
            }

        def progress(iteration, visited, queue_size, best_h, current, exploration):
            del iteration, visited, queue_size, best_h, current
            return {
                "type": "progress",
                "phase": "search",
                "exploration": exploration,
            }

        with patch("api.services.frog_playlist.get_similar_tracks_batch", fetch):
            events = list(astar_find_path_streaming(start, end, progress))

        graph_event = next(
            event
            for event in events
            if event.get("type") == "progress" and event.get("exploration")
        )
        node_ids = {item["id"] for item in graph_event["exploration"]["nodes"]}
        self.assertIn("artist a::track a", node_ids)
        self.assertIn("artist m::track m", node_ids)
        self.assertTrue(graph_event["exploration"]["edges"])

    def test_alternatives_improve_both_sides_of_a_bridge(self):
        route_nodes = {name: node(name) for name in ("A", "C", "Z")}
        candidate = node("B")
        graph = {
            track_key(route_nodes["A"]): [
                {**route_nodes["C"], "match": 0.2},
                {**candidate, "match": 0.8},
            ],
            track_key(route_nodes["C"]): [
                {**route_nodes["A"], "match": 0.2},
                {**route_nodes["Z"], "match": 0.3},
            ],
            track_key(route_nodes["Z"]): [
                {**route_nodes["C"], "match": 0.3},
                {**candidate, "match": 0.7},
            ],
            track_key(candidate): [
                {**route_nodes["A"], "match": 0.8},
                {**route_nodes["Z"], "match": 0.7},
            ],
        }
        spotify_tracks = {
            name: self.resolve(f"Artist {name}", f"Track {name}")
            for name in ("A", "C", "Z")
        }

        def fetch_tracks(track_ids):
            by_id = {track["id"]: track for track in spotify_tracks.values()}
            return [by_id[track_id] for track_id in track_ids]

        def fetch_similar(tracks, limit=100, max_workers=20):
            del limit, max_workers
            return {
                pair: graph.get(
                    track_key({"artist": pair[0], "name": pair[1]}),
                    [],
                )
                for pair in tracks
            }

        result = get_frog_alternatives(
            [spotify_tracks[name]["id"] for name in ("A", "C", "Z")],
            1,
            track_fetcher=fetch_tracks,
            spotify_resolver=self.resolve,
            similarity_fetcher=fetch_similar,
        )

        self.assertEqual(0.2, result["current_bottleneck"])
        self.assertEqual(1, len(result["alternatives"]))
        alternative = result["alternatives"][0]
        self.assertEqual("Track B", alternative["track"]["track"])
        self.assertEqual(0.8, alternative["left_similarity"])
        self.assertEqual(0.7, alternative["right_similarity"])
        self.assertEqual(0.5, alternative["improvement"])
        self.assertEqual(0.7, alternative["ranking_score"])
        self.assertEqual(
            {
                "level": "high",
                "score": 1.0,
                "basis": (
                    "share_of_four_possible_directional_lastfm_links_observed"
                ),
            },
            alternative["confidence"],
        )
        self.assertTrue(alternative["evidence"]["both_neighbors_linked"])
        self.assertTrue(alternative["evidence"]["left_edge"]["bidirectional"])
        self.assertTrue(alternative["evidence"]["right_edge"]["bidirectional"])
        self.assertIn(
            "both hops were observed in both directions",
            alternative["reason"],
        )

    def test_alternatives_rank_by_conservative_directional_evidence(self):
        route_nodes = {name: node(name) for name in ("A", "C", "Z")}
        asymmetric = node("B")
        corroborated = node("D")
        graph = {
            track_key(route_nodes["A"]): [
                {**route_nodes["C"], "match": 0.2},
                {**asymmetric, "match": 0.95},
                {**corroborated, "match": 0.65},
            ],
            track_key(route_nodes["C"]): [
                {**route_nodes["A"], "match": 0.2},
                {**route_nodes["Z"], "match": 0.3},
            ],
            track_key(route_nodes["Z"]): [
                {**route_nodes["C"], "match": 0.3},
                {**asymmetric, "match": 0.2},
                {**corroborated, "match": 0.65},
            ],
            track_key(asymmetric): [
                {**route_nodes["A"], "match": 0.2},
                {**route_nodes["Z"], "match": 0.95},
            ],
            track_key(corroborated): [
                {**route_nodes["A"], "match": 0.65},
                {**route_nodes["Z"], "match": 0.65},
            ],
        }
        spotify_tracks = {
            name: self.resolve(f"Artist {name}", f"Track {name}")
            for name in ("A", "C", "Z")
        }

        def fetch_tracks(track_ids):
            by_id = {track["id"]: track for track in spotify_tracks.values()}
            return [by_id[track_id] for track_id in track_ids]

        def fetch_similar(tracks, limit=100, max_workers=20):
            del limit, max_workers
            return {
                pair: graph.get(
                    track_key({"artist": pair[0], "name": pair[1]}),
                    [],
                )
                for pair in tracks
            }

        result = get_frog_alternatives(
            [spotify_tracks[name]["id"] for name in ("A", "C", "Z")],
            1,
            limit=2,
            current_left_similarity=0.9,
            current_right_similarity=0.8,
            track_fetcher=fetch_tracks,
            spotify_resolver=self.resolve,
            similarity_fetcher=fetch_similar,
        )

        alternatives = result["alternatives"]
        self.assertEqual(
            ["Track D", "Track B"],
            [alternative["track"]["track"] for alternative in alternatives],
        )
        self.assertEqual(0.65, alternatives[0]["ranking_score"])
        self.assertEqual(0.2, alternatives[1]["ranking_score"])
        self.assertEqual(0.8, result["current_bottleneck"])
        self.assertEqual(0.2, result["current_conservative_bottleneck"])
        self.assertEqual(-0.15, alternatives[0]["improvement"])
        self.assertEqual(0.45, alternatives[0]["conservative_improvement"])
        # The existing display score stays backward-compatible while ranking
        # no longer treats the asymmetric 95%/20% links as uniformly strong.
        self.assertEqual(0.95, alternatives[1]["bottleneck_similarity"])
        self.assertEqual(
            0.2,
            alternatives[1]["evidence"]["left_edge"][
                "conservative_similarity"
            ],
        )

    def test_alternative_confidence_reports_one_way_link_coverage(self):
        route_nodes = {name: node(name) for name in ("A", "C", "Z")}
        candidate = node("B")
        graph = {
            track_key(route_nodes["A"]): [
                {**candidate, "match": 0.8},
            ],
            track_key(route_nodes["Z"]): [
                {**candidate, "match": 0.7},
            ],
            track_key(candidate): [],
        }
        spotify_tracks = {
            name: self.resolve(f"Artist {name}", f"Track {name}")
            for name in ("A", "C", "Z")
        }

        def fetch_tracks(track_ids):
            by_id = {track["id"]: track for track in spotify_tracks.values()}
            return [by_id[track_id] for track_id in track_ids]

        def fetch_similar(tracks, limit=100, max_workers=20):
            del limit, max_workers
            return {
                pair: graph.get(
                    track_key({"artist": pair[0], "name": pair[1]}),
                    [],
                )
                for pair in tracks
            }

        result = get_frog_alternatives(
            [spotify_tracks[name]["id"] for name in ("A", "C", "Z")],
            1,
            current_left_similarity=0.4,
            current_right_similarity=0.5,
            track_fetcher=fetch_tracks,
            spotify_resolver=self.resolve,
            similarity_fetcher=fetch_similar,
        )

        alternative = result["alternatives"][0]
        self.assertEqual("limited", alternative["confidence"]["level"])
        self.assertEqual(0.5, alternative["confidence"]["score"])
        self.assertEqual(0.4, result["current_conservative_bottleneck"])
        self.assertEqual(0.3, alternative["conservative_improvement"])
        self.assertEqual(
            ["left_neighbor_to_candidate"],
            [
                observation["direction"]
                for observation in alternative["evidence"]["left_edge"][
                    "observations"
                ]
            ],
        )
        self.assertEqual(
            ["right_neighbor_to_candidate"],
            [
                observation["direction"]
                for observation in alternative["evidence"]["right_edge"][
                    "observations"
                ]
            ],
        )
        self.assertIn(
            "each hop has one observed direction",
            alternative["reason"],
        )


if __name__ == "__main__":
    unittest.main()
