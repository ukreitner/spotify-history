# spotify-history-codex

## Open the Listening Archive on macOS

Install the one-click launcher once:

```bash
scripts/install_macos_launcher.sh
```

Then open **Listening Archive** from `~/Applications` or Spotlight. The launcher:

- reuses the local API and web app when they are already running;
- otherwise pulls the newest archived history when the checkout is clean;
- prepares missing dependencies and the production build;
- starts both local services; and
- opens `http://127.0.0.1:3000` in Google Chrome.

Startup logs live in the gitignored `.run/` directory. The app continues to use
the gitignored local `.env` for Spotify authorization.

plan:

# Spotify History Archiver – GitHub‑only Implementation Plan

> **Purpose**
> Build a *zero‑infrastructure* pipeline that archives every Spotify play into an SQLite database stored in a GitHub repository. A scheduled GitHub Actions workflow runs every *N* minutes, fetches the latest plays via the Spotify Web API, appends new rows, and commits the DB back to the repo.  ✅ Public‑repo + GitHub‑hosted runners → unlimited free minutes ([docs.github.com](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions?utm_source=chatgpt.com), [docs.github.com](https://docs.github.com/get-started/learning-about-github/githubs-products?utm_source=chatgpt.com)).

---

## 1  Repository scaffold

```
spotify-history/
├── .github/
│   └── workflows/
│       └── spotify.yml       # CI schedule
├── src/
│   └── collector.py          # polling script
├── data/
│   └── history_YYYYMM.db            # SQLite DB (auto‑committed)
├── requirements.txt          # spotipy, python‑dotenv
└── README.md                 # setup notes
```

### Branch strategy

* **`main`** – production; workflow runs only here.
* Optional **`dev`** for code reviews.

---

## 2  Spotify credentials

| Item                          | Note                                                                                         |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| **Client ID / Client Secret** | From [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)      |
| **Refresh Token**             | Generated once with an *authorization‑code* flow; provides silent re‑authentication forever. |

<details><summary>One‑time token generator (Python snippet)</summary>

```python
import os, spotipy.util as util
TOKEN = util.prompt_for_user_token(
    username = os.getenv("SPOTIFY_USER"),
    scope = "user-read-recently-played",
    client_id = os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri = "http://localhost:8888/callback"
)
print(TOKEN)
```

</details>

You can also generate a token via `scripts/get_refresh_token.py`:

```bash
SPOTIFY_USER=<username> SPOTIFY_CLIENT_ID=<id> \
SPOTIFY_CLIENT_SECRET=<secret> scripts/get_refresh_token.py
```

> **Scope safety** – only `user-read-recently-played` is required; no write perms.

### Store secrets in the repo ➜ **Settings → Secrets & variables → Actions**

`SPOTIFY_CLIENT_ID` · `SPOTIFY_CLIENT_SECRET` · `SPOTIFY_REFRESH_TOKEN` · `GH_EMAIL` · `GH_NAME`.
Secrets are AES‑encrypted at rest and only exposed to the running job ([docs.github.com](https://docs.github.com/en/rest/guides/encrypting-secrets-for-the-rest-api?utm_source=chatgpt.com), [docs.github.com](https://docs.github.com/en/rest/actions/secrets?utm_source=chatgpt.com)).

---

## 3  Collector script (`src/collector.py`)

```python
#!/usr/bin/env python3
import os, sqlite3, datetime, spotipy
from spotipy.oauth2 import SpotifyOAuth

now = datetime.datetime.now(datetime.UTC)
db_name = f"history_{now.strftime('%Y%m')}.db"
data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(data_dir, exist_ok=True)
DB_PATH = os.path.join(data_dir, db_name)
conn = sqlite3.connect(DB_PATH)
conn.execute("""CREATE TABLE IF NOT EXISTS plays (
    played_at TEXT PRIMARY KEY,  -- ISO‑8601 UTC
    track_id  TEXT,
    track     TEXT,
    artist    TEXT,
    genre     TEXT,
    ms_played INTEGER

)""")

oauth = SpotifyOAuth(
    client_id     = os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri  = "http://localhost:8888/callback",
    scope         = "user-read-recently-played",
    cache_path    = "/tmp/.spotify-cache"  # transient on runner
)
token = oauth.refresh_access_token(os.getenv("SPOTIFY_REFRESH_TOKEN"))
sp = spotipy.Spotify(auth=token["access_token"])

items = sp.current_user_recently_played(limit=50)["items"]
artist_ids = {
    a["id"]
    for it in items
    for a in it["track"]["artists"]
    if a.get("id")
}
artist_genres = {}
ids = list(artist_ids)
for i in range(0, len(ids), 50):
    batch = ids[i:i+50]
    for artist in sp.artists(batch)["artists"]:
        artist_genres[artist["id"]] = artist.get("genres", [])

for item in items:  # 50‑track API cap
    genres = set()
    for artist in item["track"]["artists"]:
        genres.update(artist_genres.get(artist.get("id"), []))
    row = (
        item["played_at"],
        item["track"]["id"],
        item["track"]["name"],
        ", ".join(a["name"] for a in item["track"]["artists"]),
        item.get("ms_played", item["track"].get("duration_ms")),
        ", ".join(sorted(genres))
    )
    conn.execute("INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?,?)", row)

conn.commit()
```

**Design notes**

* The *primary key* is `played_at` – unique per play, de‑duplicates naturally.
* API request volume: 1 call / run → \~144 calls/day at 10‑min cron – way under Spotify’s 100 k/day quota.
* DB lives in `data/` so it remains small; prune or rotate yearly if repo size grows.

---

## 4  Workflow (`.github/workflows/spotify.yml`)

```yaml
name: Collect Spotify history

on:
  schedule:
    # UTC; 10‑minute cadence. GitHub minimum interval = 5 min ([docs.github.com](https://docs.github.com/actions/learn-github-actions/events-that-trigger-workflows?utm_source=chatgpt.com))
    - cron: "*/10 * * * *"
  workflow_dispatch:  # manual trigger for testing

permissions:
  contents: write

jobs:
  scrape:
    runs-on: ubuntu-latest
    concurrency:
      group: spotify-scrape
      cancel-in-progress: true  # avoid overlap ([docs.github.com](https://docs.github.com/en/enterprise-cloud%40latest/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs?utm_source=chatgpt.com))

    steps:
    - uses: actions/checkout@v4

    - uses: actions/setup-python@v5
      with: { python-version: "3.12" }

    - run: pip install -r requirements.txt

    - run: python src/collector.py
      env:
        SPOTIFY_CLIENT_ID:      ${{ secrets.SPOTIFY_CLIENT_ID }}
        SPOTIFY_CLIENT_SECRET:  ${{ secrets.SPOTIFY_CLIENT_SECRET }}
        SPOTIFY_REFRESH_TOKEN:  ${{ secrets.SPOTIFY_REFRESH_TOKEN }}

    - name: Commit DB
      uses: EndBug/add-and-commit@v9
      with:
        add: "data/history_*.db"
        message: "chore: update Spotify history $(date -u +'%F %T')"
        default_author: github_actions
        author_name:  ${{ secrets.GH_NAME }}
        author_email: ${{ secrets.GH_EMAIL }}
```

**Key points**

1. **`concurrency`** block prevents simultaneous runs on slow runners.
2. **`setup-python`** caches the selected version.
3. **`EndBug/add-and-commit`** action commits changes without needing a PAT.
4. DB commit executes only if `history_*.db` diff exists (handled internally by the action).

---

## 5  Local dev & CI debugging

| Tool                                          | Purpose                                                   | Command         |
| --------------------------------------------- | --------------------------------------------------------- | --------------- |
| `pip install -r requirements.txt`             | Run collector locally.                                    | –               |
| [`nektos/act`](https://github.com/nektos/act) | Executes the workflow in Docker to mirror GitHub runners. | `act -j scrape` |

<details><summary>Running <code>act</code> with secrets locally</summary>

```
SPOTIFY_CLIENT_ID=xxx \
SPOTIFY_CLIENT_SECRET=xxx \
SPOTIFY_REFRESH_TOKEN=xxx \
GH_NAME="spotify‑bot" \
GH_EMAIL="bot@example.com" \
act -j scrape
```

</details>

---

## 6  Operational considerations

### 6.1 Git repository growth

* 3 KB per play × \~30 k plays/year ≈ 90 MB raw. SQLite compresses well; still, consider:

  * Monthly DB rotation (`history_YYYYMM.db`).
  * Git LFS if size > 100 MB.

### 6.2 Error handling

* Exit‑code propagation: workflow fails visibly if API/token error.
* Optionally append `--no-commit` flag to collector and skip commit step when zero rows inserted (save runner time).

### 6.3 Security

* Keep repo **private** if you don’t want listen history publicly visible.
* Secrets are masked in logs; avoid `print`‑ing token values.

### 6.4 Cost

* Public repo → unlimited minutes. Private repo → 2 000 free minutes/mo then \$0.008/min ([docs.github.com](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions?utm_source=chatgpt.com)).
* Workflow above ≈ 0.5 min/run → 216 min/mo at 10‑min cadence.

---

## 7  Roadmap extensions

| Feature                 | Idea                                                             |
| ----------------------- | ---------------------------------------------------------------- |
| **Back‑fill**           | Parse monthly GDPR *Streaming History* JSON to seed DB.          |
| **Visual dashboards**   | Build Streamlit or Grafana panel reading `history_YYYYMM.db`.           |
| **Webhook alerts**      | Push Discord message when a track crosses 50th listen.           |
| **Alternative storage** | Switch to DuckDB or Postgres by swapping `sqlite3` driver calls. |
| **Unit tests**          | Mock `spotipy` responses – run via `pytest` in the workflow.     |


## 8  Back‑fill

Use the back-fill tool to import historical plays from Spotify's GDPR export.

```bash
python scripts/backfill_from_json.py "Streaming_History*.json"
```

Each record's timestamp (`endTime` in older exports or `ts` in newer ones) is
interpreted as UTC and stored as `played_at` in `data/history_YYYYMM.db`.
Existing rows are skipped with `INSERT OR IGNORE`.
If provided, the track's genre is saved in the `genre` column.
