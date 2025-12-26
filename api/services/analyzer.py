from typing import List, Dict
from collections import defaultdict
from datetime import datetime, timedelta
from ..db import (
    get_total_plays, get_unique_artists, get_unique_tracks,
    get_top_artists, get_top_genres, get_listening_stats_by_type,
    get_all_plays_with_timestamps, ContentType
)


def get_overview(content_type: ContentType = "all") -> Dict:
    """Get listening overview stats."""
    return {
        "total_plays": get_total_plays(content_type),
        "unique_artists": get_unique_artists(content_type),
        "unique_tracks": get_unique_tracks(content_type),
    }


def get_overview_split() -> Dict:
    """Get listening stats split by content type."""
    return get_listening_stats_by_type()


def get_top_artists_stats(limit: int = 20, content_type: ContentType = "all") -> List[Dict]:
    """Get top artists by play count."""
    return get_top_artists(limit, content_type)


def get_top_genres_stats(limit: int = 20, content_type: ContentType = "all") -> List[Dict]:
    """Get top genres by play count."""
    return get_top_genres(limit, content_type)


def get_listening_patterns(content_type: ContentType = "all") -> Dict:
    """
    Analyze listening patterns by hour of day and day of week.
    Returns data for visualization.
    """
    plays = get_all_plays_with_timestamps(content_type)
    
    # Initialize counters
    by_hour = defaultdict(int)
    by_day = defaultdict(int)
    by_month = defaultdict(int)
    
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    
    for play in plays:
        try:
            # Parse timestamp
            ts = play.replace("Z", "").replace("+00:00", "")
            if "." in ts:
                dt = datetime.fromisoformat(ts)
            else:
                dt = datetime.fromisoformat(ts)
            
            by_hour[dt.hour] += 1
            by_day[dt.weekday()] += 1
            by_month[dt.month - 1] += 1
        except (ValueError, AttributeError):
            continue
    
    # Format for charts
    hourly_data = [
        {"hour": h, "label": f"{h:02d}:00", "plays": by_hour.get(h, 0)}
        for h in range(24)
    ]
    
    daily_data = [
        {"day": d, "label": day_names[d], "plays": by_day.get(d, 0)}
        for d in range(7)
    ]
    
    monthly_data = [
        {"month": m, "label": month_names[m], "plays": by_month.get(m, 0)}
        for m in range(12)
    ]
    
    # Find peak times
    peak_hour = max(by_hour.items(), key=lambda x: x[1], default=(12, 0))
    peak_day = max(by_day.items(), key=lambda x: x[1], default=(0, 0))
    
    return {
        "by_hour": hourly_data,
        "by_day": daily_data,
        "by_month": monthly_data,
        "peak_hour": peak_hour[0],
        "peak_hour_label": f"{peak_hour[0]:02d}:00",
        "peak_day": peak_day[0],
        "peak_day_label": day_names[peak_day[0]] if peak_day[1] > 0 else "N/A",
    }


def get_listening_streaks(content_type: ContentType = "all") -> Dict:
    """
    Calculate listening streaks - consecutive days with plays.
    """
    plays = get_all_plays_with_timestamps(content_type)
    
    # Get unique days
    listening_days = set()
    for play in plays:
        try:
            ts = play.replace("Z", "").replace("+00:00", "")
            if "." in ts:
                dt = datetime.fromisoformat(ts)
            else:
                dt = datetime.fromisoformat(ts)
            listening_days.add(dt.date())
        except (ValueError, AttributeError):
            continue
    
    if not listening_days:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "total_listening_days": 0,
            "streak_start": None,
            "longest_streak_start": None,
            "longest_streak_end": None,
        }
    
    sorted_days = sorted(listening_days)
    
    # Calculate streaks
    streaks = []
    current_streak_start = sorted_days[0]
    current_streak_length = 1
    
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i-1]).days == 1:
            current_streak_length += 1
        else:
            streaks.append({
                "start": current_streak_start,
                "end": sorted_days[i-1],
                "length": current_streak_length
            })
            current_streak_start = sorted_days[i]
            current_streak_length = 1
    
    # Don't forget the last streak
    streaks.append({
        "start": current_streak_start,
        "end": sorted_days[-1],
        "length": current_streak_length
    })
    
    # Find longest streak
    longest = max(streaks, key=lambda x: x["length"])
    
    # Calculate current streak (must include today or yesterday)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    
    current_streak = 0
    current_streak_start_date = None
    
    if sorted_days[-1] >= yesterday:
        # Count backwards from most recent day
        current_streak = 1
        current_streak_start_date = sorted_days[-1]
        
        for i in range(len(sorted_days) - 2, -1, -1):
            if (sorted_days[i+1] - sorted_days[i]).days == 1:
                current_streak += 1
                current_streak_start_date = sorted_days[i]
            else:
                break
    
    return {
        "current_streak": current_streak,
        "longest_streak": longest["length"],
        "total_listening_days": len(listening_days),
        "streak_start": current_streak_start_date.isoformat() if current_streak_start_date else None,
        "longest_streak_start": longest["start"].isoformat(),
        "longest_streak_end": longest["end"].isoformat(),
    }
