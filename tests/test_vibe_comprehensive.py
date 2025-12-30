#!/usr/bin/env python3
"""
Comprehensive test suite for vibe playlist generation.

Tests different:
- Anchor track combinations (single, multiple, genre-mixed)
- Discovery ratios (0%, 50%, 100%)
- Flow modes (smooth, energy_arc, shuffle)
- Edge cases

Collects metrics on:
- Coherence scores
- Genre consistency  
- Artist diversity
- Transition quality
"""

import requests
import json
from dataclasses import dataclass
from typing import List, Dict, Optional
from collections import Counter
import statistics

BASE = "http://localhost:8000"


@dataclass
class TestResult:
    name: str
    anchors: List[Dict]
    settings: Dict
    success: bool
    error: Optional[str] = None
    
    # Results
    total_tracks: int = 0
    history_count: int = 0
    discovery_count: int = 0
    
    # Quality metrics
    avg_coherence: float = 0.0
    min_coherence: float = 0.0
    max_coherence: float = 0.0
    
    # Flow quality
    avg_transition_cost: float = 0.0
    smooth_transitions: int = 0
    jarring_transitions: int = 0
    
    # Diversity
    unique_artists: int = 0
    artist_concentration: float = 0.0  # How concentrated (0 = very diverse, 1 = all same artist)
    
    # Genre coherence
    top_genres: List[str] = None
    tracks_matching_top_genre: int = 0
    
    # Discovery quality
    discovery_new_artists: int = 0  # Tracks from artists not in history
    discovery_via_breakdown: Dict = None


def search_history(query: str) -> Optional[Dict]:
    """Search user's listening history for a track."""
    try:
        resp = requests.get(f"{BASE}/api/tracks/history/search", params={"q": query, "limit": 1}, timeout=30)
        results = resp.json()
        if results:
            return results[0]
    except Exception as e:
        print(f"  ⚠ Search failed for '{query}': {e}")
    return None


def search_spotify(query: str) -> Optional[Dict]:
    """Search Spotify for a track."""
    try:
        resp = requests.get(f"{BASE}/api/tracks/search", params={"q": query, "limit": 1}, timeout=30)
        results = resp.json()
        if results:
            return results[0]
    except Exception as e:
        print(f"  ⚠ Spotify search failed for '{query}': {e}")
    return None


def generate_playlist(anchor_ids: List[str], **settings) -> Dict:
    """Generate a vibe playlist."""
    payload = {
        "anchor_track_ids": anchor_ids,
        "track_count": settings.get("track_count", 25),
        "discovery_ratio": settings.get("discovery_ratio", 50),
        "flow_mode": settings.get("flow_mode", "smooth"),
        "exclude_artists": settings.get("exclude_artists", []),
    }
    resp = requests.post(f"{BASE}/api/recommendations/vibe", json=payload, timeout=120)
    return resp.json()


def analyze_result(name: str, anchors: List[Dict], settings: Dict, result: Dict) -> TestResult:
    """Analyze playlist generation result."""
    test = TestResult(
        name=name,
        anchors=anchors,
        settings=settings,
        success="tracks" in result,
        error=result.get("detail") if "detail" in result else None,
        top_genres=[],
        discovery_via_breakdown={},
    )
    
    if not test.success:
        return test
    
    tracks = result.get("tracks", [])
    counts = result.get("counts", {})
    flow_stats = result.get("flow_stats", {})
    vibe_profile = result.get("vibe_profile", {})
    
    test.total_tracks = len(tracks)
    test.history_count = counts.get("history", 0)
    test.discovery_count = counts.get("discovery", 0)
    
    # Coherence scores
    coherences = [t.get("coherence_score", 0) for t in tracks if t.get("coherence_score")]
    if coherences:
        test.avg_coherence = statistics.mean(coherences)
        test.min_coherence = min(coherences)
        test.max_coherence = max(coherences)
    
    # Flow quality
    test.avg_transition_cost = flow_stats.get("avg_transition_cost", 0)
    test.smooth_transitions = flow_stats.get("smooth_transitions", 0)
    test.jarring_transitions = flow_stats.get("jarring_transitions", 0)
    
    # Artist diversity
    artists = [t.get("artist", "").split(",")[0].strip() for t in tracks]
    artist_counts = Counter(artists)
    test.unique_artists = len(artist_counts)
    if artists:
        most_common_count = artist_counts.most_common(1)[0][1]
        test.artist_concentration = most_common_count / len(tracks)
    
    # Top genres from vibe profile
    test.top_genres = vibe_profile.get("top_genres", [])
    
    # Discovery breakdown
    discovery_tracks = [t for t in tracks if t.get("source") == "discovery"]
    via_counter = Counter()
    for t in discovery_tracks:
        via = t.get("discovered_via", "unknown") or "unknown"
        # Simplify via labels
        if "deep cut" in via:
            via_counter["deep cut"] += 1
        elif "top track" in via:
            via_counter["top track"] += 1
        elif "similar" in via:
            via_counter["similar artist"] += 1
        elif "genre" in via:
            via_counter["genre search"] += 1
        else:
            via_counter[via[:20]] += 1
    test.discovery_via_breakdown = dict(via_counter)
    
    return test


def print_test_result(test: TestResult):
    """Print formatted test result."""
    status = "✅" if test.success else "❌"
    print(f"\n{'='*70}")
    print(f"{status} {test.name}")
    print(f"{'='*70}")
    
    # Anchors
    anchor_info = ", ".join(f"{a.get('track', 'Unknown')[:20]} by {a.get('artist', 'Unknown')[:15]}" 
                            for a in test.anchors if a)
    print(f"Anchors: {anchor_info}")
    print(f"Settings: discovery={test.settings.get('discovery_ratio', 50)}%, "
          f"flow={test.settings.get('flow_mode', 'smooth')}, "
          f"count={test.settings.get('track_count', 25)}")
    
    if not test.success:
        print(f"\n❌ FAILED: {test.error}")
        return
    
    print(f"\n📊 COUNTS:")
    print(f"   Total: {test.total_tracks} | History: {test.history_count} | Discovery: {test.discovery_count}")
    
    print(f"\n🎯 COHERENCE:")
    print(f"   Avg: {test.avg_coherence:.3f} | Min: {test.min_coherence:.3f} | Max: {test.max_coherence:.3f}")
    coherence_grade = "🟢 Excellent" if test.avg_coherence > 0.6 else "🟡 Good" if test.avg_coherence > 0.45 else "🔴 Needs work"
    print(f"   Grade: {coherence_grade}")
    
    print(f"\n🌊 FLOW QUALITY:")
    print(f"   Avg transition cost: {test.avg_transition_cost:.3f}")
    print(f"   Smooth: {test.smooth_transitions} | Jarring: {test.jarring_transitions}")
    flow_grade = "🟢 Smooth" if test.jarring_transitions == 0 else "🟡 OK" if test.jarring_transitions <= 2 else "🔴 Choppy"
    print(f"   Grade: {flow_grade}")
    
    print(f"\n👥 ARTIST DIVERSITY:")
    print(f"   Unique artists: {test.unique_artists} / {test.total_tracks} tracks")
    print(f"   Concentration: {test.artist_concentration:.1%}")
    diversity_grade = "🟢 Diverse" if test.artist_concentration < 0.15 else "🟡 OK" if test.artist_concentration < 0.25 else "🔴 Repetitive"
    print(f"   Grade: {diversity_grade}")
    
    print(f"\n🎸 GENRE PROFILE:")
    print(f"   Top genres: {', '.join(test.top_genres[:5])}")
    
    if test.discovery_via_breakdown:
        print(f"\n🔍 DISCOVERY SOURCES:")
        for source, count in sorted(test.discovery_via_breakdown.items(), key=lambda x: -x[1]):
            print(f"   {source}: {count}")


def run_test(name: str, anchor_queries: List[str], search_spotify_first: bool = False, **settings) -> TestResult:
    """Run a single test."""
    print(f"\n🧪 Running: {name}")
    
    # Find anchor tracks
    anchors = []
    for query in anchor_queries:
        if search_spotify_first:
            anchor = search_spotify(query)
        else:
            anchor = search_history(query)
            if not anchor:
                anchor = search_spotify(query)
        
        if anchor:
            anchors.append(anchor)
            print(f"   ✓ Found: {anchor.get('track', 'Unknown')[:30]} by {anchor.get('artist', 'Unknown')[:20]}")
        else:
            print(f"   ✗ Not found: {query}")
    
    if not anchors:
        return TestResult(
            name=name,
            anchors=[],
            settings=settings,
            success=False,
            error="No anchors found",
            top_genres=[],
            discovery_via_breakdown={},
        )
    
    # Generate playlist
    try:
        anchor_ids = [a.get("track_id") for a in anchors if a.get("track_id")]
        result = generate_playlist(anchor_ids, **settings)
        return analyze_result(name, anchors, settings, result)
    except Exception as e:
        return TestResult(
            name=name,
            anchors=anchors,
            settings=settings,
            success=False,
            error=str(e),
            top_genres=[],
            discovery_via_breakdown={},
        )


def main():
    print("\n" + "="*70)
    print("🎵 VIBE PLAYLIST ALGORITHM - COMPREHENSIVE TEST SUITE")
    print("="*70)
    
    all_results = []
    
    # ========================================
    # TEST GROUP 1: Single Anchor Tracks
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 1: Single Anchor Tracks")
    print("─"*70)
    
    single_anchor_tests = [
        ("Single: Indie Rock", ["arctic monkeys"], {}),
        ("Single: Electronic", ["daft punk"], {}),
        ("Single: Folk", ["fleet foxes"], {}),
        ("Single: Classic Rock", ["led zeppelin"], {}),
        ("Single: Pop", ["taylor swift"], {}),
    ]
    
    for name, queries, settings in single_anchor_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 2: Multiple Coherent Anchors
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 2: Multiple Coherent Anchors (Same Genre)")
    print("─"*70)
    
    coherent_anchor_tests = [
        ("Coherent: Indie/Lo-fi Duo", ["mac demarco", "tame impala"], {}),
        ("Coherent: Folk Trio", ["fleet foxes", "bon iver", "iron and wine"], {}),
        ("Coherent: Electronic Pair", ["daft punk", "justice"], {}),
        ("Coherent: Classic Rock Trio", ["led zeppelin", "deep purple", "black sabbath"], {}),
        ("Coherent: Alt-Rock Duo", ["radiohead", "the strokes"], {}),
    ]
    
    for name, queries, settings in coherent_anchor_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 3: Mixed Genre Anchors (Stress Test)
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 3: Mixed Genre Anchors (Stress Test)")
    print("─"*70)
    
    mixed_anchor_tests = [
        ("Mixed: Rock + Electronic", ["radiohead", "aphex twin"], {}),
        ("Mixed: Folk + Hip-Hop", ["bon iver", "kendrick lamar"], {}),
        ("Mixed: Classical + Jazz", ["debussy", "miles davis"], {}),
        ("Mixed: Wild Mix", ["metallica", "billie eilish", "bach"], {}),
    ]
    
    for name, queries, settings in mixed_anchor_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 4: Discovery Ratio Variations
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 4: Discovery Ratio Variations")
    print("─"*70)
    
    discovery_tests = [
        ("Discovery 0%: History Only", ["radiohead", "arcade fire"], {"discovery_ratio": 0}),
        ("Discovery 25%: Mostly History", ["radiohead", "arcade fire"], {"discovery_ratio": 25}),
        ("Discovery 50%: Balanced", ["radiohead", "arcade fire"], {"discovery_ratio": 50}),
        ("Discovery 75%: Mostly New", ["radiohead", "arcade fire"], {"discovery_ratio": 75}),
        ("Discovery 100%: All New", ["radiohead", "arcade fire"], {"discovery_ratio": 100}),
    ]
    
    for name, queries, settings in discovery_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 5: Flow Mode Variations
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 5: Flow Mode Variations")
    print("─"*70)
    
    flow_tests = [
        ("Flow: Smooth", ["arctic monkeys", "the strokes"], {"flow_mode": "smooth"}),
        ("Flow: Energy Arc", ["arctic monkeys", "the strokes"], {"flow_mode": "energy_arc"}),
        ("Flow: Shuffle", ["arctic monkeys", "the strokes"], {"flow_mode": "shuffle"}),
    ]
    
    for name, queries, settings in flow_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 6: Playlist Size Variations
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 6: Playlist Size Variations")
    print("─"*70)
    
    size_tests = [
        ("Size: Small (10)", ["tame impala"], {"track_count": 10}),
        ("Size: Medium (25)", ["tame impala"], {"track_count": 25}),
        ("Size: Large (50)", ["tame impala"], {"track_count": 50}),
        ("Size: XL (75)", ["tame impala"], {"track_count": 75}),
    ]
    
    for name, queries, settings in size_tests:
        result = run_test(name, queries, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # TEST GROUP 7: Spotify-Only Anchors (Not in History)
    # ========================================
    print("\n\n" + "─"*70)
    print("📦 TEST GROUP 7: Spotify-Only Anchors (Fresh Discovery)")
    print("─"*70)
    
    spotify_tests = [
        ("Spotify: Current Pop Hit", ["bad bunny"], {"search_spotify_first": True}),
        ("Spotify: Niche Artist", ["khruangbin"], {"search_spotify_first": True}),
        ("Spotify: New Release", ["boygenius"], {"search_spotify_first": True}),
    ]
    
    for name, queries, settings in spotify_tests:
        search_spotify_first = settings.pop("search_spotify_first", False)
        result = run_test(name, queries, search_spotify_first=search_spotify_first, **settings)
        all_results.append(result)
        print_test_result(result)
    
    # ========================================
    # SUMMARY
    # ========================================
    print("\n\n" + "="*70)
    print("📊 SUMMARY")
    print("="*70)
    
    successful = [r for r in all_results if r.success]
    failed = [r for r in all_results if not r.success]
    
    print(f"\n✅ Passed: {len(successful)} / {len(all_results)}")
    print(f"❌ Failed: {len(failed)}")
    
    if failed:
        print("\n❌ Failed tests:")
        for f in failed:
            print(f"   - {f.name}: {f.error}")
    
    if successful:
        # Aggregate metrics
        avg_coherence = statistics.mean(r.avg_coherence for r in successful)
        avg_diversity = statistics.mean(r.unique_artists / r.total_tracks for r in successful if r.total_tracks > 0)
        avg_jarring = statistics.mean(r.jarring_transitions for r in successful)
        
        print(f"\n📈 Aggregate Metrics (successful tests):")
        print(f"   Avg Coherence Score: {avg_coherence:.3f}")
        print(f"   Avg Artist Diversity: {avg_diversity:.1%}")
        print(f"   Avg Jarring Transitions: {avg_jarring:.1f}")
        
        # Best and worst
        best_coherence = max(successful, key=lambda r: r.avg_coherence)
        worst_coherence = min(successful, key=lambda r: r.avg_coherence)
        
        print(f"\n🏆 Best Coherence: {best_coherence.name} ({best_coherence.avg_coherence:.3f})")
        print(f"📉 Worst Coherence: {worst_coherence.name} ({worst_coherence.avg_coherence:.3f})")
        
        # Observations
        print("\n" + "="*70)
        print("💡 KEY OBSERVATIONS")
        print("="*70)
        
        # Group by category
        single_results = [r for r in successful if "Single:" in r.name]
        coherent_results = [r for r in successful if "Coherent:" in r.name]
        mixed_results = [r for r in successful if "Mixed:" in r.name]
        discovery_results = [r for r in successful if "Discovery" in r.name]
        
        if single_results and coherent_results:
            single_avg = statistics.mean(r.avg_coherence for r in single_results)
            coherent_avg = statistics.mean(r.avg_coherence for r in coherent_results)
            print(f"\n1. Single vs Multiple Anchors:")
            print(f"   - Single anchor avg coherence: {single_avg:.3f}")
            print(f"   - Multiple coherent anchors avg: {coherent_avg:.3f}")
            if coherent_avg > single_avg:
                print(f"   → Multiple coherent anchors improve coherence by {(coherent_avg-single_avg):.3f}")
            else:
                print(f"   → Single anchors perform similarly or better")
        
        if mixed_results:
            mixed_avg = statistics.mean(r.avg_coherence for r in mixed_results)
            print(f"\n2. Mixed Genre Anchors:")
            print(f"   - Mixed anchors avg coherence: {mixed_avg:.3f}")
            if mixed_avg < 0.45:
                print(f"   → ⚠️ Mixed genres struggle to create coherent playlists")
            else:
                print(f"   → Algorithm handles mixed genres reasonably well")
        
        if discovery_results:
            discovery_0 = next((r for r in discovery_results if "0%" in r.name), None)
            discovery_100 = next((r for r in discovery_results if "100%" in r.name), None)
            if discovery_0 and discovery_100:
                print(f"\n3. Discovery Ratio Impact:")
                print(f"   - 0% discovery coherence: {discovery_0.avg_coherence:.3f}")
                print(f"   - 100% discovery coherence: {discovery_100.avg_coherence:.3f}")
                if discovery_0.avg_coherence > discovery_100.avg_coherence:
                    print(f"   → History tracks more coherent (expected)")
                else:
                    print(f"   → Discovery tracks maintain good coherence!")


if __name__ == "__main__":
    main()

