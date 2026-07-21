'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ContentType,
  getArchiveStatus,
  getListeningPatterns,
  getListeningStreaks,
  getOverview,
  getTopArtists,
  getTopGenres,
  getTopTracks,
} from '@/lib/api';

const CONTENT_OPTIONS: { value: ContentType; label: string }[] = [
  { value: 'music', label: 'Music' },
  { value: 'podcast', label: 'Podcasts' },
  { value: 'all', label: 'Everything' },
];

const QUICK_ACTIONS = [
  {
    href: '/gems',
    label: 'Forgotten gems',
    description: 'Resurface tracks you once had on repeat.',
    tone: 'lime',
  },
  {
    href: '/discover',
    label: 'Find a new artist',
    description: 'Branch out from the artists you already love.',
    tone: 'blue',
  },
  {
    href: '/playlists',
    label: 'Build a playlist',
    description: 'Turn a few anchors into a coherent listening arc.',
    tone: 'orange',
  },
];

function formatNumber(value: number) {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function formatDate(value?: string | null) {
  if (!value) return 'No plays yet';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Recently';
  return new Intl.DateTimeFormat('en', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function StatTile({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone: 'lime' | 'blue' | 'orange' | 'neutral';
}) {
  return (
    <article className={`archive-stat archive-stat-${tone}`}>
      <p className="archive-stat-label">{label}</p>
      <p className="archive-stat-value">{formatNumber(value)}</p>
      <p className="archive-stat-note">{note}</p>
    </article>
  );
}

function DashboardSkeleton() {
  return (
    <div className="dashboard-stack" aria-label="Loading listening archive" aria-busy="true">
      <div className="skeleton h-40 rounded-[28px]" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="skeleton h-36 rounded-2xl" />
        ))}
      </div>
      <div className="grid lg:grid-cols-[1.45fr_0.8fr] gap-5">
        <div className="skeleton h-80 rounded-2xl" />
        <div className="skeleton h-80 rounded-2xl" />
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [contentType, setContentType] = useState<ContentType>('music');

  const overviewQuery = useQuery({
    queryKey: ['overview', contentType],
    queryFn: () => getOverview(contentType),
    retry: 1,
  });
  const statusQuery = useQuery({
    queryKey: ['archiveStatus'],
    queryFn: getArchiveStatus,
    retry: 1,
  });
  const artistsQuery = useQuery({
    queryKey: ['topArtists', contentType],
    queryFn: () => getTopArtists(7, contentType),
  });
  const genresQuery = useQuery({
    queryKey: ['topGenres', contentType],
    queryFn: () => getTopGenres(10, contentType),
  });
  const patternsQuery = useQuery({
    queryKey: ['listeningPatterns', contentType],
    queryFn: () => getListeningPatterns(contentType),
  });
  const streaksQuery = useQuery({
    queryKey: ['listeningStreaks', contentType],
    queryFn: () => getListeningStreaks(contentType),
  });
  const tracksQuery = useQuery({
    queryKey: ['topTracks', contentType],
    queryFn: () => getTopTracks(6, contentType),
  });

  const refetchDashboard = () => {
    void Promise.all([
      overviewQuery.refetch(),
      statusQuery.refetch(),
      artistsQuery.refetch(),
      genresQuery.refetch(),
      patternsQuery.refetch(),
      streaksQuery.refetch(),
      tracksQuery.refetch(),
    ]);
  };

  if (overviewQuery.isPending) return <DashboardSkeleton />;

  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <section className="archive-error" role="alert">
        <div className="archive-error-mark">!</div>
        <p className="eyebrow">Archive unavailable</p>
        <h1>We couldn&apos;t reach your listening data.</h1>
        <p>
          The dashboard now reports connection problems instead of quietly pretending your archive is empty.
          Check that the API is running, then try again.
        </p>
        <button type="button" className="btn-primary" onClick={refetchDashboard}>
          Retry connection
        </button>
      </section>
    );
  }

  const overview = overviewQuery.data;
  const status = statusQuery.data;
  const artists = artistsQuery.data ?? [];
  const genres = genresQuery.data ?? [];
  const patterns = patternsQuery.data;
  const streaks = streaksQuery.data;
  const tracks = tracksQuery.data ?? [];
  const maxArtistPlays = Math.max(...artists.map((artist) => artist.play_count), 1);
  const isRefreshing = [
    overviewQuery,
    statusQuery,
    artistsQuery,
    genresQuery,
    patternsQuery,
    streaksQuery,
    tracksQuery,
  ].some((query) => query.isFetching);

  return (
    <div className="dashboard-stack">
      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <div className="archive-live-row">
            <span className="archive-live-dot" aria-hidden="true" />
            <span>Archive live</span>
            <span className="archive-live-separator" aria-hidden="true">/</span>
            <span>Latest play {formatDate(status?.latest_played_at)}</span>
          </div>
          <p className="eyebrow">Personal listening archive</p>
          <h1 className="dashboard-title">
            Your listening,
            <span> finally legible.</span>
          </h1>
          <p className="dashboard-intro">
            {formatNumber(overview.total_plays)} plays, collected quietly and turned into patterns you can actually use.
          </p>
        </div>

        <div className="dashboard-hero-controls">
          <div className="content-switcher" role="group" aria-label="Choose content type">
            {CONTENT_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={contentType === option.value}
                onClick={() => setContentType(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="refresh-button"
            onClick={refetchDashboard}
            disabled={isRefreshing}
          >
            <span className={isRefreshing ? 'refresh-icon refresh-icon-spinning' : 'refresh-icon'} aria-hidden="true">↻</span>
            {isRefreshing ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
      </section>

      <section className="archive-stat-grid" aria-label="Listening summary">
        <StatTile label="Archived plays" value={overview.total_plays} note="Across your full archive" tone="lime" />
        <StatTile label="Distinct tracks" value={overview.unique_tracks} note="Songs and episodes" tone="blue" />
        <StatTile label="Artists heard" value={overview.unique_artists} note="Unique credited artists" tone="orange" />
        <StatTile
          label="Listening days"
          value={streaks?.total_listening_days ?? 0}
          note={`${status?.database_count ?? 0} monthly databases`}
          tone="neutral"
        />
      </section>

      <section className="dashboard-primary-grid">
        <article className="archive-panel rhythm-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Daily rhythm</p>
              <h2>When you press play</h2>
            </div>
            {patterns && <span className="panel-callout">Peak at {patterns.peak_hour_label}</span>}
          </div>
          <div className="rhythm-chart" aria-label="Plays by hour of day">
            <ResponsiveContainer
              width="100%"
              height="100%"
              minWidth={0}
              minHeight={260}
              initialDimension={{ width: 720, height: 260 }}
            >
              <AreaChart data={patterns?.by_hour ?? []} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}>
                <defs>
                  <linearGradient id="rhythmFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity={0.45} />
                    <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={false}
                  interval={3}
                  tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                <Tooltip
                  cursor={{ stroke: 'rgba(183, 255, 94, 0.35)' }}
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="plays"
                  stroke="var(--accent-primary)"
                  strokeWidth={3}
                  fill="url(#rhythmFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </article>

        <article className="archive-panel artist-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Heavy rotation</p>
              <h2>Top artists</h2>
            </div>
          </div>
          <ol className="artist-ranking">
            {artists.map((artist, index) => (
              <li key={artist.artist}>
                <span className="artist-rank">{String(index + 1).padStart(2, '0')}</span>
                <div className="artist-ranking-copy">
                  <div className="artist-ranking-labels">
                    <span title={artist.artist}>{artist.artist}</span>
                    <strong>{artist.play_count.toLocaleString()}</strong>
                  </div>
                  <div className="artist-meter" aria-hidden="true">
                    <span style={{ width: `${(artist.play_count / maxArtistPlays) * 100}%` }} />
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </article>
      </section>

      <section className="insight-strip" aria-label="Listening highlights">
        <div>
          <span>Peak day</span>
          <strong>{patterns?.peak_day_label ?? '—'}</strong>
        </div>
        <div>
          <span>Current streak</span>
          <strong>{streaks?.current_streak ?? 0} days</strong>
        </div>
        <div>
          <span>Longest streak</span>
          <strong>{streaks?.longest_streak ?? 0} days</strong>
        </div>
        <div className="weekday-chart">
          <ResponsiveContainer
            width="100%"
            height="100%"
            minWidth={0}
            minHeight={72}
            initialDimension={{ width: 560, height: 72 }}
          >
            <BarChart data={patterns?.by_day ?? []} margin={{ top: 6, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
              <Bar dataKey="plays" fill="var(--accent-secondary)" radius={[5, 5, 2, 2]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="dashboard-secondary-grid">
        <article className="archive-panel genres-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Taste map</p>
              <h2>Your defining genres</h2>
            </div>
          </div>
          <div className="genre-cloud">
            {genres.map((genre, index) => (
              <div key={genre.genre} className={`genre-pill genre-pill-${(index % 3) + 1}`}>
                <span>{genre.genre}</span>
                <strong>{formatNumber(genre.play_count)}</strong>
              </div>
            ))}
          </div>
        </article>

        <article className="archive-panel tracks-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Most replayed</p>
              <h2>Tracks that stayed</h2>
            </div>
          </div>
          <ol className="track-ranking">
            {tracks.map((track, index) => (
              <li key={track.track_id || `${track.artist}-${track.track}`}>
                <span className="track-rank">{index + 1}</span>
                <div className="track-art" aria-hidden="true">
                  {track.image_url ? (
                    <img src={track.image_url} alt="" />
                  ) : (
                    <span>♪</span>
                  )}
                </div>
                <div className="track-copy">
                  <strong title={track.track}>{track.track}</strong>
                  <span title={track.artist}>{track.artist}</span>
                </div>
                <span className="track-count">{track.play_count} plays</span>
              </li>
            ))}
          </ol>
        </article>
      </section>

      <section className="action-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Do something with it</p>
            <h2>Turn history into a next listen</h2>
          </div>
        </div>
        <div className="action-grid">
          {QUICK_ACTIONS.map((action) => (
            <Link key={action.href} href={action.href} className={`action-card action-card-${action.tone}`}>
              <div>
                <strong>{action.label}</strong>
                <p>{action.description}</p>
              </div>
              <span aria-hidden="true">↗</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
