'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPodcastStats, getTopShows, getRecentEpisodes, getPodcastBacklog, getShowEpisodes } from '@/lib/api';

export default function PodcastsPage() {
  const [selectedShow, setSelectedShow] = useState<string | null>(null);

  const { data: stats } = useQuery({
    queryKey: ['podcastStats'],
    queryFn: getPodcastStats,
  });

  const { data: shows } = useQuery({
    queryKey: ['topShows'],
    queryFn: () => getTopShows(10),
  });

  const { data: recent } = useQuery({
    queryKey: ['recentEpisodes'],
    queryFn: () => getRecentEpisodes(10),
  });

  const { data: backlog } = useQuery({
    queryKey: ['podcastBacklog'],
    queryFn: () => getPodcastBacklog(10),
  });

  const { data: episodes } = useQuery({
    queryKey: ['showEpisodes', selectedShow],
    queryFn: () => getShowEpisodes(selectedShow!, 20),
    enabled: !!selectedShow,
  });

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          <span className="gradient-text">Podcast</span> Stats
        </h1>
        <p className="text-[var(--text-secondary)]">
          Your podcast listening habits and backlog
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="stat-card animate-fade-in opacity-0" style={{ animationDelay: '0.1s' }}>
          <p className="text-[var(--text-muted)] text-sm font-medium uppercase tracking-wider">Total Plays</p>
          <p className="text-4xl font-bold mt-2 text-[var(--accent-pink)]">
            {stats?.total_plays.toLocaleString() || '—'}
          </p>
        </div>
        
        <div className="stat-card animate-fade-in opacity-0" style={{ animationDelay: '0.15s' }}>
          <p className="text-[var(--text-muted)] text-sm font-medium uppercase tracking-wider">Shows</p>
          <p className="text-4xl font-bold mt-2 text-[var(--accent-secondary)]">
            {stats?.unique_shows.toLocaleString() || '—'}
          </p>
        </div>
        
        <div className="stat-card animate-fade-in opacity-0" style={{ animationDelay: '0.2s' }}>
          <p className="text-[var(--text-muted)] text-sm font-medium uppercase tracking-wider">Episodes</p>
          <p className="text-4xl font-bold mt-2 text-[var(--accent-tertiary)]">
            {stats?.unique_episodes.toLocaleString() || '—'}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Shows */}
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.25s' }}>
          <h2 className="text-xl font-semibold mb-4">Top Shows</h2>
          <div className="space-y-2">
            {shows?.map((show, i) => (
              <button
                key={show.show}
                onClick={() => setSelectedShow(selectedShow === show.show ? null : show.show)}
                className={`
                  w-full text-left p-4 rounded-xl transition-all duration-200
                  ${selectedShow === show.show
                    ? 'bg-[var(--accent-primary)]/20 ring-1 ring-[var(--accent-primary)]'
                    : 'bg-[var(--bg-secondary)] hover:bg-[var(--bg-card-hover)]'
                  }
                `}
              >
                <div className="flex justify-between items-center">
                  <div className="flex items-center gap-3">
                    <span className="text-[var(--text-muted)] text-sm font-mono w-6">#{i + 1}</span>
                    <span className="font-medium truncate">{show.show}</span>
                  </div>
                  <span className="badge badge-cyan">{show.episode_count} eps</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Episodes / Recent */}
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.3s' }}>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">
              {selectedShow ? `Episodes: ${selectedShow}` : 'Recently Played'}
            </h2>
            {selectedShow && (
              <button
                onClick={() => setSelectedShow(null)}
                className="text-sm text-[var(--text-muted)] hover:text-white transition-colors"
              >
                Clear
              </button>
            )}
          </div>
          <div className="space-y-2 max-h-[400px] overflow-y-auto pr-2">
            {selectedShow ? (
              episodes?.map((ep, i) => (
                <div 
                  key={ep.episode} 
                  className="p-4 bg-[var(--bg-secondary)] rounded-xl animate-fade-in opacity-0"
                  style={{ animationDelay: `${i * 0.03}s` }}
                >
                  <p className="font-medium truncate text-[var(--text-primary)]">{ep.episode}</p>
                  <p className="text-sm text-[var(--text-muted)]">
                    {ep.play_count || 1} play{(ep.play_count || 1) > 1 ? 's' : ''}
                  </p>
                </div>
              ))
            ) : (
              recent?.map((ep, i) => (
                <div 
                  key={ep.episode + ep.show} 
                  className="p-4 bg-[var(--bg-secondary)] rounded-xl animate-fade-in opacity-0"
                  style={{ animationDelay: `${i * 0.03}s` }}
                >
                  <p className="font-medium truncate text-[var(--text-primary)]">{ep.episode}</p>
                  <p className="text-sm text-[var(--accent-secondary)]">{ep.show}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Backlog */}
      {backlog && backlog.length > 0 && (
        <div className="glass-card p-6 animate-fade-in opacity-0" style={{ animationDelay: '0.35s' }}>
          <h2 className="text-xl font-semibold mb-2">Backlog</h2>
          <p className="text-sm text-[var(--text-muted)] mb-4">
            Episodes you started but haven't returned to
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {backlog.map((ep, i) => (
              <div 
                key={ep.episode + ep.show} 
                className="p-4 bg-[var(--bg-secondary)] rounded-xl animate-fade-in opacity-0"
                style={{ animationDelay: `${i * 0.03}s` }}
              >
                <p className="font-medium truncate text-[var(--text-primary)]">{ep.episode}</p>
                <div className="flex justify-between items-center mt-1">
                  <p className="text-sm text-[var(--accent-secondary)]">{ep.show}</p>
                  <span className="text-xs text-[var(--text-muted)]">{ep.days_since_played}d ago</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
