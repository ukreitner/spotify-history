#!/usr/bin/env python3
"""Authorize a local checkout with Spotify using PKCE.

The app secret is intentionally not required. The resulting refresh token is
written only to the gitignored .env file and is never printed.
"""

import os
from pathlib import Path

from dotenv import dotenv_values
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyPKCE


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-read-recently-played playlist-modify-public playlist-modify-private"


def write_env(refresh_token: str) -> None:
    values = {
        key: value
        for key, value in dotenv_values(ENV_PATH).items()
        if value is not None
    }
    values["SPOTIFY_CLIENT_ID"] = CLIENT_ID
    values.pop("SPOTIFY_CLIENT_SECRET", None)
    values["SPOTIFY_REFRESH_TOKEN"] = refresh_token

    ENV_PATH.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    ENV_PATH.chmod(0o600)


def main() -> None:
    if not CLIENT_ID:
        raise SystemExit("Set SPOTIFY_CLIENT_ID before running this script.")

    cache = MemoryCacheHandler()
    auth = SpotifyPKCE(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_handler=cache,
        open_browser=True,
    )

    def show_authorize_url(state=None):
        print(f"AUTHORIZE_URL={auth.get_authorize_url(state)}", flush=True)

    # Keep browser choice under the caller's control while retaining Spotipy's
    # loopback callback server and state validation.
    auth._open_auth_url = show_authorize_url
    auth.get_access_token(check_cache=False)
    token_info = cache.get_cached_token() or {}
    refresh_token = token_info.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Spotify did not return a refresh token.")

    write_env(refresh_token)
    print("Local Spotify authorization saved to .env.", flush=True)


if __name__ == "__main__":
    main()
