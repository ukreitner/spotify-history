'use client';

import { useQuery } from '@tanstack/react-query';
import { getOverview, getTopArtists, getTopGenres, getListeningPatterns, getListeningStreaks, getTopTracks } from '@/lib/api';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, AreaChart, Area } from 'recharts';
import Link from 'next/link';

const COLORS = ['#7c3aed', '#8b5cf6', '#a78bfa', '#c4b5fd', '#ddd6fe'];

function StatCard({ label, value, color = 'var(--accent-primary)', delay = 0 }: { 
  label: string; 
  value: string | number; 
  color?: string;
  delay?: number;
}) {
  return (
    <div 
      className="stat-card animate-fade-in opacity-0"
      style={{ animationDelay: `${delay}s` }}
    >
      <p className="text-[var(--text-muted)] text-sm font-medium uppercase tracking-wider">{label}</p>
      <p className="text-4xl font-bold mt-2" style={{ color }}>{typeof value === 'number' ? value.toLocaleString() : value}</p>
    </div>
  );
}

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="custom-tooltip">
      <p className="font-medium text-[var(--text-primary)]">{label}</p>
      <p className="text-[var(--accent-primary)]">{payload[0].value.toLocaleString()} plays</p>
    </div>
  );
}

export default function Dashboard() {
  const { data: overview, isLoading: loadingOverview } = useQuery({
    queryKey: ['overview'],
    queryFn: getOverview,
  });

  const { data: artists } = useQuery({
    queryKey: ['topArtists'],
    queryFn: () => getTopArtists(8),
  });

  const { data: genres } = useQuery({
    queryKey: ['topGenres'],
    queryFn: () => getTopGenres(8),
  });

  const { data: patterns } = useQuery({
    queryKey: ['listeningPatterns'],
    queryFn: getListeningPatterns,
  });

  const { data: streaks } = useQuery({
    queryKey: ['listeningStreaks'],
    queryFn: getListeningStreaks,
  });

  const { data: topTracks } = useQuery({
    queryKey: ['topTracks'],
    queryFn: () => getTopTracks(5),
  });

  if (loadingOverview) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-[var(--accent-primary)] to-[var(--accent-secondary)] animate-pulse" />
          <p className="text-[var(--text-muted)]">Loading your music story...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          Your <span className="gradient-text">Listening</span> Story
        </h1>
        <p className="text-[var(--text-secondary)]">
          A deep dive into your musical journey
        </p>
      </div>

      {/* Main Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <StatCard 
          label="Total Plays" 
          value={overview?.total_plays || 0} 
          delay={0.1}
        />
        <StatCard 
          label="Unique Artists" 
          value={overview?.unique_artists || 0} 
          color="var(--accent-secondary)"
          delay={0.15}
        />
        <StatCard 
          label="Unique Tracks" 
          value={overview?.unique_tracks || 0} 
          color="var(--accent-tertiary)"
          delay={0.2}
        />
      </div>

      {/* Streaks */}
      {streaks && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.25s' }}>
            <p className="text-sm text-[var(--text-muted)] mb-1">Current Streak</p>
            <p className="text-3xl font-bold text-[var(--accent-tertiary)]">
              {streaks.current_streak} <span className="text-lg font-normal text-[var(--text-muted)]">days</span>
            </p>
          </div>
          
          <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.3s' }}>
            <p className="text-sm text-[var(--text-muted)] mb-1">Longest Streak</p>
            <p className="text-3xl font-bold text-[var(--accent-primary)]">
              {streaks.longest_streak} <span className="text-lg font-normal text-[var(--text-muted)]">days</span>
            </p>
          </div>
          
          <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.35s' }}>
            <p className="text-sm text-[var(--text-muted)] mb-1">Total Listening Days</p>
            <p className="text-3xl font-bold text-[var(--accent-secondary)]">
              {streaks.total_listening_days}
            </p>
          </div>
        </div>
      )}

      {/* Listening Patterns */}
      {patterns && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* By Hour */}
          <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.4s' }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold">Listening by Hour</h2>
              <span className="badge badge-purple">Peak: {patterns.peak_hour_label}</span>
            </div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={patterns.by_hour} margin={{ left: 0, right: 0 }}>
                  <defs>
                    <linearGradient id="hourGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity={0.4} />
                      <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis 
                    dataKey="label" 
                    stroke="var(--text-muted)" 
                    fontSize={10} 
                    tickLine={false} 
                    axisLine={false}
                    interval={3}
                  />
                  <YAxis hide />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone" 
                    dataKey="plays" 
                    stroke="var(--accent-primary)" 
                    strokeWidth={2}
                    fill="url(#hourGradient)" 
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* By Day */}
          <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.45s' }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold">Listening by Day</h2>
              <span className="badge badge-cyan">Peak: {patterns.peak_day_label}</span>
            </div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={patterns.by_day} margin={{ left: 0, right: 0 }}>
                  <XAxis 
                    dataKey="label" 
                    stroke="var(--text-muted)" 
                    fontSize={12} 
                    tickLine={false} 
                    axisLine={false} 
                  />
                  <YAxis hide />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(6, 182, 212, 0.1)' }} />
                  <Bar dataKey="plays" radius={[4, 4, 0, 0]}>
                    {patterns.by_day.map((entry, index) => (
                      <Cell 
                        key={index} 
                        fill={index === patterns.peak_day ? 'var(--accent-secondary)' : 'var(--accent-secondary)'} 
                        fillOpacity={index === patterns.peak_day ? 1 : 0.5}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Artists */}
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.5s' }}>
          <h2 className="text-xl font-semibold mb-6">Top Artists</h2>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={artists} layout="vertical" margin={{ left: 10, right: 20 }}>
                <XAxis type="number" stroke="var(--text-muted)" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis 
                  type="category" 
                  dataKey="artist" 
                  stroke="var(--text-muted)" 
                  width={100} 
                  tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(124, 58, 237, 0.1)' }} />
                <Bar dataKey="play_count" radius={[0, 8, 8, 0]}>
                  {artists?.map((_, index) => (
                    <Cell key={index} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Top Genres */}
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.55s' }}>
          <h2 className="text-xl font-semibold mb-6">Top Genres</h2>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={genres} layout="vertical" margin={{ left: 10, right: 20 }}>
                <XAxis type="number" stroke="var(--text-muted)" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis 
                  type="category" 
                  dataKey="genre" 
                  stroke="var(--text-muted)" 
                  width={100} 
                  tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(6, 182, 212, 0.1)' }} />
                <Bar dataKey="play_count" radius={[0, 8, 8, 0]}>
                  {genres?.map((_, index) => (
                    <Cell key={index} fill={index % 2 === 0 ? '#06b6d4' : '#22d3ee'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Top Tracks */}
      {topTracks && topTracks.length > 0 && (
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.6s' }}>
          <h2 className="text-xl font-semibold mb-4">Most Played Tracks</h2>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {topTracks.map((track, index) => (
              <div 
                key={track.track_id} 
                className="flex flex-col items-center text-center p-4 rounded-xl bg-[var(--bg-secondary)] hover:bg-[var(--bg-card-hover)] transition-colors"
              >
                <div className="relative mb-3">
                  <div className="w-20 h-20 rounded-lg overflow-hidden bg-[var(--bg-card)]">
                    {track.image_url ? (
                      <img src={track.image_url} alt={track.track} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-[var(--text-muted)]">
                        <svg className="w-8 h-8" viewBox="0 0 24 24" fill="currentColor">
                          <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                        </svg>
                      </div>
                    )}
                  </div>
                  <div className="absolute -top-2 -left-2 w-6 h-6 rounded-full bg-[var(--accent-primary)] flex items-center justify-center text-xs font-bold">
                    {index + 1}
                  </div>
                </div>
                <p className="font-medium text-sm truncate w-full">{track.track}</p>
                <p className="text-xs text-[var(--text-muted)] truncate w-full">{track.artist}</p>
                <p className="text-xs text-[var(--accent-primary)] mt-1">{track.play_count} plays</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Quick Actions */}
      <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.65s' }}>
        <h2 className="text-xl font-semibold mb-4">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Link href="/gems" className="btn-secondary text-center py-4 rounded-xl hover:scale-105 transition-transform">
            Find Forgotten Gems
          </Link>
          <Link href="/discover" className="btn-secondary text-center py-4 rounded-xl hover:scale-105 transition-transform">
            Discover New Artists
          </Link>
          <Link href="/playlists" className="btn-secondary text-center py-4 rounded-xl hover:scale-105 transition-transform">
            Build Custom Playlist
          </Link>
          <Link href="/podcasts" className="btn-secondary text-center py-4 rounded-xl hover:scale-105 transition-transform">
            Podcast Stats
          </Link>
        </div>
      </div>
    </div>
  );
}
