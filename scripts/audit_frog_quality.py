#!/usr/bin/env python3
"""Exercise live Frog routes, exactness, graph signals, and repairability.

This is deliberately a Last.fm graph-signal audit, not a claim that metadata
similarity is equivalent to listening or audio-feature analysis.
"""

from collections import Counter
import json
import math
import os
import queue
import statistics
import sys
import threading
import time
from typing import Dict, Iterable, List, Tuple

import requests


BASE_URL = os.getenv("QUALITY_AUDIT_BASE_URL", "http://127.0.0.1:8001")
SMOOTHNESS_TARGET = 0.12
ROUTE_WALL_SECONDS = 105.0

SCENARIOS = [
    {
        "name": "Lua to lacy, 50 tracks (reported hard case)",
        "start": "Lua Bright Eyes",
        "end": "lacy Olivia Rodrigo",
        "expected_start": ("Lua", "Bright Eyes"),
        "expected_end": ("lacy", "Olivia Rodrigo"),
        "count": 50,
    },
    {
        "name": "indie-folk to twee, 30 tracks",
        "start": "Upward Over the Mountain Iron & Wine",
        "end": "If You're Feeling Sinister Belle and Sebastian",
        "expected_start": ("Upward Over the Mountain", "Iron & Wine"),
        "expected_end": ("If You're Feeling Sinister", "Belle and Sebastian"),
        "count": 30,
    },
    {
        "name": "Lua to lacy, 30 tracks",
        "start": "Lua Bright Eyes",
        "end": "lacy Olivia Rodrigo",
        "expected_start": ("Lua", "Bright Eyes"),
        "expected_end": ("lacy", "Olivia Rodrigo"),
        "count": 30,
    },
]


def search_track(query: str) -> Dict:
    response = requests.get(
        f"{BASE_URL}/api/tracks/search",
        params={"q": query, "limit": 5},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise RuntimeError(f"No Spotify result for {query!r}")
    return results[0]


def stream_route(start_id: str, end_id: str, count: int) -> Tuple[Dict, float, int]:
    started = time.monotonic()
    response = requests.post(
        f"{BASE_URL}/api/recommendations/frog/stream",
        json={
            "start_track_id": start_id,
            "end_track_id": end_id,
            "track_count": count,
        },
        stream=True,
        # Read timeout bounds a silent socket. The monotonic check below is the
        # actual product wall clock while heartbeat events keep arriving.
        timeout=(15, 15),
    )
    response.raise_for_status()
    result = None
    progress_events = 0
    stream_items: queue.Queue = queue.Queue()

    def read_stream() -> None:
        try:
            for line in response.iter_lines():
                stream_items.put(("line", line))
        except Exception as error:
            stream_items.put(("error", error))
        finally:
            stream_items.put(("done", None))

    threading.Thread(target=read_stream, daemon=True).start()
    try:
        while True:
            remaining = ROUTE_WALL_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError(
                    f"Frog exceeded the {ROUTE_WALL_SECONDS:.0f}-second wall deadline"
                )
            try:
                kind, payload = stream_items.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if kind == "error":
                raise payload
            if kind == "done":
                break
            raw_line = payload
            if not raw_line or not raw_line.startswith(b"data: "):
                continue
            event = json.loads(raw_line[6:])
            if event.get("type") == "progress":
                progress_events += 1
            elif event.get("type") == "error":
                raise RuntimeError(event.get("error", "Frog stream error"))
            elif event.get("type") == "result":
                result = event
                break
    finally:
        response.close()
    if result is None:
        raise RuntimeError("Frog stream ended without a result")
    return result, time.monotonic() - started, progress_events


def first_artist(track: Dict) -> str:
    return (track.get("artist") or "").split(",", 1)[0].strip()


def checked_score(value, label: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"{label} is outside [0, 1]: {value!r}")
    return score


def transition_scores(tracks: Iterable[Dict]) -> List[float]:
    scores = []
    for index, track in enumerate(tracks):
        if index == 0:
            continue
        if track.get("transition_similarity") is None:
            continue
        scores.append(
            checked_score(
                track["transition_similarity"],
                f"transition into track {index + 1}",
            )
        )
    return scores


def normalized(value: str) -> str:
    return " ".join((value or "").casefold().split())


def assert_expected_search_result(
    result: Dict,
    expected: Tuple[str, str],
    endpoint: str,
) -> None:
    expected_track, expected_artist = expected
    if (
        normalized(result.get("track", "")) != normalized(expected_track)
        or normalized(first_artist(result)) != normalized(expected_artist)
    ):
        raise RuntimeError(
            f"{endpoint} search resolved to "
            f"{result.get('track')} by {result.get('artist')}, expected "
            f"{expected_track} by {expected_artist}"
        )


def route_failures(
    result: Dict,
    expected_count: int,
    start_id: str,
    end_id: str,
    elapsed: float,
) -> List[str]:
    tracks = result.get("tracks", [])
    scores = transition_scores(tracks)
    failures: List[str] = []
    ids = [track.get("track_id") for track in tracks]
    artists = Counter(first_artist(track).lower() for track in tracks)

    if not result.get("success"):
        failures.append(result.get("error") or "result did not report success")
    if len(tracks) != expected_count:
        failures.append(f"returned {len(tracks)} of {expected_count} tracks")
    if any(not track_id for track_id in ids):
        failures.append("one or more tracks lack a Spotify track ID")
    if ids and (ids[0] != start_id or ids[-1] != end_id):
        failures.append("route endpoints changed")
    if len(ids) != len(set(ids)):
        failures.append("duplicate Spotify track IDs")
    if len(scores) != max(0, len(tracks) - 1):
        failures.append("one or more transitions lack an observed score")
    if scores and statistics.median(scores) < 0.18:
        failures.append(f"median transition {statistics.median(scores):.0%} is too low")
    if scores:
        computed_target = min(scores) >= SMOOTHNESS_TARGET
        if bool(result.get("meets_smoothness_target")) != computed_target:
            failures.append(
                "reported smoothness flag disagrees with the returned transitions"
            )
    if artists and len(artists) < min(10, max(2, expected_count // 3)):
        failures.append("route repeats too few distinct artists")
    if artists and max(artists.values()) > 4:
        failures.append("one artist appears more than four times")
    if elapsed > ROUTE_WALL_SECONDS:
        failures.append(
            f"route exceeded the {ROUTE_WALL_SECONDS:.0f}-second product deadline "
            f"({elapsed:.1f}s)"
        )
    return failures


def weakest_editable_position(tracks: List[Dict]) -> int:
    weakest_right = min(
        range(1, len(tracks)),
        key=lambda index: float(tracks[index].get("transition_similarity") or 0),
    )
    return weakest_right - 1 if weakest_right == len(tracks) - 1 else weakest_right


def audit_repair(
    tracks: List[Dict],
    scores: List[float],
) -> Tuple[str, List[str], List[float] | None]:
    position = weakest_editable_position(tracks)
    response = requests.post(
        f"{BASE_URL}/api/recommendations/frog/alternatives",
        json={
            "track_ids": [track["track_id"] for track in tracks],
            "position": position,
            "limit": 8,
            "current_left_similarity": tracks[position].get("transition_similarity"),
            "current_right_similarity": tracks[position + 1].get("transition_similarity"),
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    alternatives = payload.get("alternatives", [])
    failures: List[str] = []
    current_floor = min(scores, default=1.0)

    expected_context = (
        tracks[position - 1]["track_id"],
        tracks[position]["track_id"],
        tracks[position + 1]["track_id"],
    )
    actual_context = (
        payload.get("left_track", {}).get("track_id"),
        payload.get("current_track", {}).get("track_id"),
        payload.get("right_track", {}).get("track_id"),
    )
    if actual_context != expected_context:
        failures.append("repair response belongs to a different route context")

    if not alternatives:
        if current_floor < SMOOTHNESS_TARGET:
            failures.append(
                "route misses the 12% target and no connected repair was found"
            )
        return "no connected repair found", failures, None

    ranking = [
        checked_score(item.get("ranking_score"), f"repair {index + 1} ranking")
        for index, item in enumerate(alternatives)
    ]
    if ranking != sorted(ranking, reverse=True):
        failures.append("repairs are not sorted by conservative bottleneck")

    for index, item in enumerate(alternatives):
        evidence = item.get("evidence") or {}
        edges = (evidence.get("left_edge"), evidence.get("right_edge"))
        if not all(edges):
            failures.append(f"repair {index + 1} lacks directional evidence")
            continue
        for side, edge in zip(("left", "right"), edges):
            direction_count = int(edge.get("direction_count", 0) or 0)
            observations = edge.get("observations", [])
            if (
                direction_count < 1
                or direction_count > 2
                or direction_count != len(observations)
            ):
                failures.append(
                    f"repair {index + 1} has inconsistent {side}-edge evidence"
                )
            observation_scores = []
            for observation_index, observation in enumerate(observations):
                observation_scores.append(
                    checked_score(
                        observation.get("similarity"),
                        f"repair {index + 1} {side} observation "
                        f"{observation_index + 1}",
                    )
                )
            conservative_score = checked_score(
                edge.get("conservative_similarity"),
                f"repair {index + 1} {side} conservative score",
            )
            if observation_scores and not math.isclose(
                conservative_score,
                min(observation_scores),
                abs_tol=0.0002,
            ):
                failures.append(
                    f"repair {index + 1} inflates its {side} conservative score"
                )
            if bool(edge.get("bidirectional")) != (direction_count == 2):
                failures.append(
                    f"repair {index + 1} has inconsistent {side} direction flags"
                )

    top = alternatives[0]
    evidence = top.get("evidence") or {}
    left_evidence = evidence.get("left_edge") or {}
    right_evidence = evidence.get("right_edge") or {}
    left_score = checked_score(
        left_evidence.get("conservative_similarity"),
        "top repair left conservative score",
    )
    right_score = checked_score(
        right_evidence.get("conservative_similarity"),
        "top repair right conservative score",
    )
    score = checked_score(top.get("ranking_score"), "top repair ranking")
    if not math.isclose(score, min(left_score, right_score), abs_tol=0.0002):
        failures.append("top repair rank is not its weaker observed side")
    improvement = float(top.get("conservative_improvement", 0))
    if not math.isfinite(improvement) or not -1 <= improvement <= 1:
        failures.append("top repair improvement is invalid")
    confidence = top.get("confidence", {}).get("level", "unknown")
    evidence_count = int(left_evidence.get("direction_count", 0) or 0) + int(
        right_evidence.get("direction_count", 0) or 0
    )
    if current_floor < SMOOTHNESS_TARGET and improvement <= 0:
        failures.append("top repair does not improve the current bottleneck")

    candidate_id = top.get("track", {}).get("track_id")
    route_ids = [track.get("track_id") for track in tracks]
    repaired_ids = route_ids.copy()
    repaired_ids[position] = candidate_id
    if not candidate_id:
        failures.append("top repair has no Spotify track ID")
    if len(repaired_ids) != len(route_ids):
        failures.append("repair changed the exact route length")
    if repaired_ids[0] != route_ids[0] or repaired_ids[-1] != route_ids[-1]:
        failures.append("repair changed a route endpoint")
    if len(repaired_ids) != len(set(repaired_ids)):
        failures.append("repair introduced a duplicate Spotify track")

    repaired_scores = scores.copy()
    repaired_scores[position - 1] = left_score
    repaired_scores[position] = right_score
    repaired_floor = min(repaired_scores, default=1.0)
    if current_floor < SMOOTHNESS_TARGET and repaired_floor < SMOOTHNESS_TARGET:
        failures.append(
            f"best repair still leaves a {repaired_floor:.0%} hop below the "
            f"{SMOOTHNESS_TARGET:.0%} target"
        )

    return (
        f"{top['track']['track']} — {top['track']['artist']}: "
        f"{score:.0%} weaker observed side, {improvement * 100:+.0f} pts, "
        f"{evidence_count}/4 directions ({confidence})",
        failures,
        repaired_scores,
    )


def representative_route(tracks: List[Dict]) -> str:
    if len(tracks) <= 10:
        sample = tracks
    else:
        positions = sorted({
            0,
            1,
            len(tracks) // 4,
            len(tracks) // 2,
            (3 * len(tracks)) // 4,
            len(tracks) - 2,
            len(tracks) - 1,
        })
        sample = [tracks[position] for position in positions]
    return " → ".join(
        f"{track['track']} ({first_artist(track)})"
        for track in sample
    )


def run_scenario(scenario: Dict) -> List[str]:
    start = search_track(scenario["start"])
    end = search_track(scenario["end"])
    assert_expected_search_result(start, scenario["expected_start"], "start")
    assert_expected_search_result(end, scenario["expected_end"], "end")
    result, elapsed, progress_events = stream_route(
        start["track_id"],
        end["track_id"],
        scenario["count"],
    )
    tracks = result.get("tracks", [])
    scores = transition_scores(tracks)
    failures = route_failures(
        result,
        scenario["count"],
        start["track_id"],
        end["track_id"],
        elapsed,
    )
    repair_summary = "not checked"
    repaired_scores = None
    if len(tracks) >= 3:
        repair_summary, repair_failures, repaired_scores = audit_repair(
            tracks,
            scores,
        )
        failures.extend(repair_failures)

    status = "PASS" if not failures else "FAIL"
    print(
        f"\n[{status}] {scenario['name']}: {len(tracks)}/{scenario['count']} tracks "
        f"in {elapsed:.1f}s ({progress_events} progress events)"
    )
    if scores:
        smooth_share = sum(score >= 0.12 for score in scores) / len(scores)
        print(
            f"  floor {min(scores):.0%} · median {statistics.median(scores):.0%} "
            f"· mean {statistics.mean(scores):.0%} · {smooth_share:.0%} clear 12%"
        )
        weakest = sorted(
            (
                score,
                tracks[index]["track"],
                tracks[index + 1]["track"],
            )
            for index, score in enumerate(scores)
        )[:3]
        print(
            "  weakest named hops: "
            + " · ".join(
                f"{left} → {right} {score:.0%}"
                for score, left, right in weakest
            )
        )
    if repaired_scores is not None and scores and min(scores) < SMOOTHNESS_TARGET:
        print(
            f"  simulated top splice: floor {min(scores):.0%} → "
            f"{min(repaired_scores):.0%}; count/endpoints/uniqueness preserved"
        )
    print(f"  arc: {representative_route(tracks)}")
    print(f"  best repair: {repair_summary}")
    for failure in failures:
        print(f"  SIGNAL FAILURE: {failure}")
    return failures


def main() -> int:
    print(
        "Frog live gate: exactness + Last.fm graph signal + repairability. "
        "This is not an audio or listening verdict."
    )
    all_failures = {}
    for scenario in SCENARIOS:
        try:
            failures = run_scenario(scenario)
        except Exception as error:
            failures = [str(error)]
            print(f"\n[FAIL] {scenario['name']}: {error}")
        if failures:
            all_failures[scenario["name"]] = failures

    passed = len(SCENARIOS) - len(all_failures)
    print(
        f"\nFrog live graph-signal audit: {passed}/{len(SCENARIOS)} scenarios passed"
    )
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
