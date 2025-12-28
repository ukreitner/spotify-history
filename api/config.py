import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Spotify credentials
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

# Last.fm credentials (for similar artists - Spotify API is restricted)
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "dc93712b39a39ecd05946e10afb25d05")

# Data directory
DATA_DIR = Path(__file__).parent.parent / "data"
