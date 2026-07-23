"""Deterministic tests for Frog Atlas exploration sampling and counters."""

import unittest
from unittest.mock import patch

from api.services.frog_playlist import (
    FROG_EXPLORATION_MAX_NODES,
    FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION,
    FROG_SEARCH_MAX_STATES_PER_DIRECTION,
    _budget_exploration_graph,
    astar_find_path_streaming,
    generate_frog_playlist_streaming,
)


def graph_node(
    node_id,
    *,
    state="discovered",
    direction="forward",
    depth=1,
    route_position=None,
):
    node = {
        "id": node_id,
        "artist": f"Artist {node_id}",
        "track": f"Track {node_id}",
        "state": state,
        "direction": direction,
        "depth": depth,
    }
    if route_position is not None:
        node["route_position"] = route_position
    return node


def graph_edge(edge_id, source, target, *, kind="search", similarity=0.5):
    direction = "route" if kind == "route" else "forward"
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
        "direction": direction,
        "similarity": similarity,
    }


def search_track(name, match=1.0):
    return {
        "artist": f"Artist {name}",
        "name": f"Track {name}",
        "match": match,
    }


class FrogExplorationBudgetTests(unittest.TestCase):
    def test_budget_preserves_route_meeting_and_active_frontier(self):
        nodes = [
            graph_node(
                "route-start",
                state="start",
                direction="route",
                depth=0,
                route_position=0,
            ),
            graph_node(
                "route-end",
                state="end",
                direction="route",
                depth=1,
                route_position=1,
            ),
            graph_node("meeting", state="meeting", direction="backward"),
            graph_node("active", state="expanded", depth=8),
            graph_node("old-expanded", state="expanded", depth=2),
            graph_node("leaf", depth=3),
        ]
        edges = [
            graph_edge(
                "route",
                "route-start",
                "route-end",
                kind="route",
                similarity=0.1,
            ),
            graph_edge("active-link", "meeting", "active", similarity=0.4),
            graph_edge("old-link", "old-expanded", "leaf", similarity=0.9),
        ]

        first = _budget_exploration_graph(
            nodes,
            edges,
            max_nodes=4,
            max_edges=2,
            preferred_node_ids={"active"},
        )
        second = _budget_exploration_graph(
            list(reversed(nodes)),
            list(reversed(edges)),
            max_nodes=4,
            max_edges=2,
            preferred_node_ids={"active"},
        )

        self.assertEqual(
            {"route-start", "route-end", "meeting", "active"},
            {node["id"] for node in first["nodes"]},
        )
        self.assertEqual(
            ["route", "active-link"],
            [edge["id"] for edge in first["edges"]],
        )
        self.assertEqual(first, second)
        self.assertTrue(first["truncated"])
        self.assertEqual(2, first["omitted_nodes"])

    def test_budget_never_returns_edges_with_omitted_endpoints(self):
        nodes = [
            graph_node("start", state="start", depth=0),
            graph_node("expanded", state="expanded"),
            graph_node("leaf"),
        ]
        edges = [
            graph_edge("kept", "start", "expanded"),
            graph_edge("dangling-after-cap", "expanded", "leaf"),
            graph_edge("already-dangling", "missing", "start"),
        ]

        result = _budget_exploration_graph(
            nodes,
            edges,
            max_nodes=2,
            max_edges=10,
        )

        retained_ids = {node["id"] for node in result["nodes"]}
        self.assertEqual(["kept"], [edge["id"] for edge in result["edges"]])
        self.assertTrue(
            all(
                edge["source"] in retained_ids and edge["target"] in retained_ids
                for edge in result["edges"]
            )
        )
        self.assertEqual(2, result["omitted_edges"])

    def test_stream_reports_exact_unique_search_totals_and_meeting_node(self):
        start = search_track("A")
        middle = search_track("M", match=0.8)
        end = search_track("Z")

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
            if event.get("type") == "progress"
            and event.get("exploration", {}).get("edges")
        )
        exploration = graph_event["exploration"]
        self.assertEqual(3, exploration["total_nodes"])
        self.assertEqual(2, exploration["total_edges"])
        self.assertEqual("search", exploration["totals_scope"])
        self.assertEqual(
            "meeting",
            next(
                node["state"]
                for node in exploration["nodes"]
                if node["id"] == "artist m::track m"
            ),
        )
        self.assertEqual(
            len({node["id"] for node in exploration["nodes"]}),
            exploration["total_nodes"],
        )

    def test_search_hard_caps_retained_states_and_live_frontier(self):
        start = search_track("A")
        end = search_track("Z")
        progress_events = []

        def fetch(tracks, limit=30, max_workers=20):
            del limit, max_workers
            results = {}
            for artist, name in tracks:
                direction = (
                    "forward"
                    if (artist, name) == (start["artist"], start["name"])
                    or "Forward" in name
                    else "backward"
                )
                prefix = "Forward" if direction == "forward" else "Backward"
                results[(artist, name)] = [
                    {
                        "artist": f"{prefix} Artist {index}",
                        "name": f"{name} {prefix} {index}",
                        "match": 0.5,
                    }
                    for index in range(40)
                ]
            return results

        def progress(iteration, visited, queue_size, best_h, current, exploration):
            del iteration, visited, best_h, current
            progress_events.append((queue_size, exploration["total_nodes"]))
            return {
                "type": "progress",
                "phase": "search",
                "exploration": exploration,
            }

        state_limit = 5
        frontier_limit = 2
        with (
            patch(
                "api.services.frog_playlist."
                "FROG_SEARCH_MAX_STATES_PER_DIRECTION",
                state_limit,
            ),
            patch(
                "api.services.frog_playlist."
                "FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION",
                frontier_limit,
            ),
            patch(
                "api.services.frog_playlist.get_similar_tracks_batch",
                fetch,
            ),
        ):
            events = list(
                astar_find_path_streaming(
                    start,
                    end,
                    progress,
                    max_iterations=10,
                )
            )

        result = next(event for event in events if event["type"] == "result")
        self.assertIsNone(result["path"])
        self.assertTrue(result["limited_by_budget"])
        self.assertEqual(state_limit * 2, result["retained_states"])
        self.assertGreater(result["frontier_rejections"], 0)
        self.assertTrue(progress_events)
        self.assertLessEqual(
            max(queue_size for queue_size, _ in progress_events),
            frontier_limit * 2,
        )
        self.assertLessEqual(
            max(node_count for _, node_count in progress_events),
            state_limit * 2,
        )
        # Defaults remain intentionally larger than the browser trace budget;
        # this focused test patches them down only to exercise saturation.
        self.assertGreater(
            FROG_SEARCH_MAX_STATES_PER_DIRECTION,
            FROG_EXPLORATION_MAX_NODES,
        )
        self.assertGreater(
            FROG_SEARCH_MAX_FRONTIER_PER_DIRECTION,
            0,
        )

    def test_streaming_generator_surfaces_safe_search_limit(self):
        spotify_tracks = [
            {
                "id": "spotify-start",
                "name": "Start",
                "artists": [{"name": "Start Artist"}],
            },
            {
                "id": "spotify-end",
                "name": "End",
                "artists": [{"name": "End Artist"}],
            },
        ]

        def fake_search(_start, _end, _callback):
            yield {
                "type": "result",
                "path": None,
                "iterations": 2,
                "limited_by_budget": True,
            }

        with (
            patch(
                "api.services.frog_playlist.get_tracks_bulk",
                return_value=spotify_tracks,
            ),
            patch(
                "api.services.frog_playlist.astar_find_path_streaming",
                side_effect=fake_search,
            ),
        ):
            events = list(
                generate_frog_playlist_streaming(
                    "spotify-start",
                    "spotify-end",
                    track_count=2,
                )
            )

        error = next(event for event in events if event["type"] == "error")
        self.assertTrue(error["search_limited"])
        self.assertIn("safe exploration limit", error["error"])

    def test_streaming_generator_caps_final_state_and_keeps_route(self):
        start_spotify = {
            "id": "spotify-start",
            "name": "Start",
            "artists": [{"name": "Start Artist"}],
        }
        end_spotify = {
            "id": "spotify-end",
            "name": "End",
            "artists": [{"name": "End Artist"}],
        }
        search_start = {"artist": "Start Artist", "name": "Start"}
        search_end = {"artist": "End Artist", "name": "End"}

        def fake_search(_start, _end, _callback):
            total = 0
            for batch in range(7):
                nodes = [
                    graph_node(f"search-{batch}-{index}", depth=batch)
                    for index in range(100)
                ]
                total += len(nodes)
                yield {
                    "type": "progress",
                    "phase": "search",
                    "exploration": {
                        "nodes": nodes,
                        "edges": [],
                        "total_nodes": total,
                        "total_edges": 0,
                        "totals_scope": "search",
                    },
                }
            yield {
                "type": "result",
                "path": [search_start, search_end],
                "iterations": 7,
            }

        route_nodes = [
            graph_node(
                "route-start",
                state="start",
                direction="route",
                depth=0,
                route_position=0,
            ),
            graph_node(
                "route-end",
                state="end",
                direction="route",
                depth=1,
                route_position=1,
            ),
        ]
        route_edge = graph_edge(
            "route-edge",
            "route-start",
            "route-end",
            kind="route",
        )
        exact_result = {
            "tracks": [{"track_id": "spotify-start"}, {"track_id": "spotify-end"}],
            "path_length": 2,
            "sampled_length": 2,
            "requested_length": 2,
            "success": True,
            "exploration": {
                "nodes": route_nodes,
                "edges": [route_edge],
            },
        }

        with (
            patch(
                "api.services.frog_playlist.get_tracks_bulk",
                return_value=[start_spotify, end_spotify],
            ),
            patch(
                "api.services.frog_playlist.astar_find_path_streaming",
                side_effect=fake_search,
            ),
            patch(
                "api.services.frog_playlist._build_exact_result",
                return_value=exact_result,
            ),
        ):
            events = list(
                generate_frog_playlist_streaming(
                    "spotify-start",
                    "spotify-end",
                    track_count=2,
                )
            )

        result = next(event for event in events if event["type"] == "result")
        exploration = result["exploration"]
        self.assertEqual(FROG_EXPLORATION_MAX_NODES, len(exploration["nodes"]))
        self.assertEqual(700, exploration["total_nodes"])
        self.assertEqual("search", exploration["totals_scope"])
        self.assertEqual(598, exploration["retained_search_nodes"])
        self.assertEqual(102, exploration["omitted_search_nodes"])
        self.assertEqual(0, exploration["retained_search_edges"])
        self.assertTrue(exploration["sampled"])
        self.assertTrue(exploration["truncated"])
        self.assertTrue(
            {"route-start", "route-end"}.issubset(
                {node["id"] for node in exploration["nodes"]}
            )
        )
        self.assertEqual(["route-edge"], [edge["id"] for edge in exploration["edges"]])


if __name__ == "__main__":
    unittest.main()
