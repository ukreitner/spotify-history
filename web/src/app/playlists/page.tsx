'use client';

import { useState, useMemo } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { getTopGenres, getTopArtists, createPlaylist } from '@/lib/api';
import TrackCard from '@/components/TrackCard';
import axios from 'axios';

interface PlaylistFilters {
  genres: string[];
  excludeGenres: string[];
  minPlays: number;
  maxDaysSincePlay: number;
  discoveryRatio: number; // 0 = all from history, 100 = all new
  artistFilter: 'all' | 'top' | 'diverse';
  limit: number;
}

const api = axios.create({ baseURL: 'http://127.0.0.1:8000/api' });

export default function PlaylistsPage() {
  const [filters, setFilters] = useState<PlaylistFilters>({
    genres: [],
    excludeGenres: [],
    minPlays: 1,
    maxDaysSincePlay: 365,
    discoveryRatio: 30, // 30% new music by default
    artistFilter: 'all',
    limit: 30,
  });
  
  const [playlistName, setPlaylistName] = useState('My Custom Mix');

  const { data: allGenres } = useQuery({
    queryKey: ['allGenres'],
    queryFn: () => getTopGenres(50),
  });

  const { data: topArtists } = useQuery({
    queryKey: ['topArtistsForFilter'],
    queryFn: () => getTopArtists(20),
  });

  const { data: tracks, isLoading: loadingTracks, refetch } = useQuery({
    queryKey: ['customPlaylist', filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.genres.length > 0) params.set('genres', filters.genres.join(','));
      if (filters.excludeGenres.length > 0) params.set('exclude_genres', filters.excludeGenres.join(','));
      params.set('min_plays', filters.minPlays.toString());
      params.set('max_days', filters.maxDaysSincePlay.toString());
      params.set('discovery_ratio', filters.discoveryRatio.toString());
      params.set('artist_filter', filters.artistFilter);
      params.set('limit', filters.limit.toString());
      
      const res = await api.get(`/recommendations/custom?${params.toString()}`);
      return res.data;
    },
    enabled: true,
  });

  const createMutation = useMutation({
    mutationFn: ({ name, trackIds }: { name: string; trackIds: string[] }) =>
      createPlaylist(name, trackIds, 'Custom playlist from Spotify History'),
    onSuccess: (data) => {
      if (data.url) window.open(data.url, '_blank');
    },
  });

  const handleCreate = () => {
    if (!tracks?.length) return;
    const trackIds = tracks.map((t: any) => t.track_id).filter(Boolean);
    createMutation.mutate({ name: playlistName, trackIds });
  };

  const toggleGenre = (genre: string, type: 'include' | 'exclude') => {
    if (type === 'include') {
      setFilters(f => ({
        ...f,
        genres: f.genres.includes(genre) 
          ? f.genres.filter(g => g !== genre)
          : [...f.genres, genre],
        excludeGenres: f.excludeGenres.filter(g => g !== genre),
      }));
    } else {
      setFilters(f => ({
        ...f,
        excludeGenres: f.excludeGenres.includes(genre)
          ? f.excludeGenres.filter(g => g !== genre)
          : [...f.excludeGenres, genre],
        genres: f.genres.filter(g => g !== genre),
      }));
    }
  };

  const topGenresList = useMemo(() => 
    allGenres?.slice(0, 30) || [], 
    [allGenres]
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          <span className="gradient-text">Playlist</span> Builder
        </h1>
        <p className="text-[var(--text-secondary)]">
          Fine-tune exactly what you want in your playlist
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Controls Panel */}
        <div className="lg:col-span-1 space-y-4">
          {/* Playlist Name */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Playlist Name
            </label>
            <input
              type="text"
              value={playlistName}
              onChange={(e) => setPlaylistName(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg focus:border-[var(--accent-primary)] focus:outline-none text-[var(--text-primary)]"
              placeholder="My Custom Mix"
            />
          </div>

          {/* Play Count Filter */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Minimum Plays: {filters.minPlays}
            </label>
            <input
              type="range"
              min="1"
              max="20"
              value={filters.minPlays}
              onChange={(e) => setFilters(f => ({ ...f, minPlays: parseInt(e.target.value) }))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>1 (include rare)</span>
              <span>20 (only favorites)</span>
            </div>
          </div>

          {/* Recency Filter */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Last Played Within: {filters.maxDaysSincePlay} days
            </label>
            <input
              type="range"
              min="7"
              max="730"
              step="7"
              value={filters.maxDaysSincePlay}
              onChange={(e) => setFilters(f => ({ ...f, maxDaysSincePlay: parseInt(e.target.value) }))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>1 week</span>
              <span>2 years</span>
            </div>
          </div>

          {/* Track Count */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Number of Tracks: {filters.limit}
            </label>
            <input
              type="range"
              min="10"
              max="100"
              step="5"
              value={filters.limit}
              onChange={(e) => setFilters(f => ({ ...f, limit: parseInt(e.target.value) }))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>10</span>
              <span>100</span>
            </div>
          </div>

          {/* Artist Diversity */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Artist Selection
            </label>
            <div className="space-y-2">
              {[
                { value: 'all', label: 'All artists', desc: 'Include any artist' },
                { value: 'top', label: 'Top artists only', desc: 'Only your most played' },
                { value: 'diverse', label: 'Diverse mix', desc: 'Limit tracks per artist' },
              ].map(opt => (
                <label 
                  key={opt.value}
                  className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    filters.artistFilter === opt.value 
                      ? 'bg-[var(--accent-primary)]/20 border border-[var(--accent-primary)]' 
                      : 'bg-[var(--bg-secondary)] border border-transparent hover:border-white/10'
                  }`}
                >
                  <input
                    type="radio"
                    name="artistFilter"
                    value={opt.value}
                    checked={filters.artistFilter === opt.value}
                    onChange={(e) => setFilters(f => ({ ...f, artistFilter: e.target.value as any }))}
                    className="mt-1 accent-[var(--accent-primary)]"
                  />
                  <div>
                    <div className="font-medium text-sm">{opt.label}</div>
                    <div className="text-xs text-[var(--text-muted)]">{opt.desc}</div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Discovery Ratio */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              New Music: {filters.discoveryRatio}%
            </label>
            <input
              type="range"
              min="0"
              max="100"
              step="10"
              value={filters.discoveryRatio}
              onChange={(e) => setFilters(f => ({ ...f, discoveryRatio: parseInt(e.target.value) }))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>All familiar</span>
              <span>All new</span>
            </div>
            <p className="text-xs text-[var(--text-muted)] mt-2">
              {filters.discoveryRatio === 0 && "Only tracks from your listening history"}
              {filters.discoveryRatio > 0 && filters.discoveryRatio < 50 && "Mostly familiar with some discoveries"}
              {filters.discoveryRatio >= 50 && filters.discoveryRatio < 100 && "Good mix of familiar and new"}
              {filters.discoveryRatio === 100 && "All new music based on your taste"}
            </p>
          </div>
        </div>

        {/* Genre Selection */}
        <div className="lg:col-span-1 glass-card p-4 max-h-[700px] overflow-y-auto">
          <h3 className="font-semibold mb-3 sticky top-0 bg-[var(--bg-card)] py-2">Genre Filters</h3>
          
          {filters.genres.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-[var(--text-muted)] mb-2">Including:</p>
              <div className="flex flex-wrap gap-1">
                {filters.genres.map(g => (
                  <button
                    key={g}
                    onClick={() => toggleGenre(g, 'include')}
                    className="px-2 py-1 text-xs rounded-full bg-[var(--accent-success)]/20 text-[var(--accent-success)] hover:bg-[var(--accent-success)]/30"
                  >
                    {g} ×
                  </button>
                ))}
              </div>
            </div>
          )}

          {filters.excludeGenres.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-[var(--text-muted)] mb-2">Excluding:</p>
              <div className="flex flex-wrap gap-1">
                {filters.excludeGenres.map(g => (
                  <button
                    key={g}
                    onClick={() => toggleGenre(g, 'exclude')}
                    className="px-2 py-1 text-xs rounded-full bg-red-500/20 text-red-400 hover:bg-red-500/30"
                  >
                    {g} ×
                  </button>
                ))}
              </div>
            </div>
          )}

          <p className="text-xs text-[var(--text-muted)] mb-2">Click to include, right-click to exclude:</p>
          <div className="space-y-1">
            {topGenresList.map(genre => {
              const isIncluded = filters.genres.includes(genre.genre);
              const isExcluded = filters.excludeGenres.includes(genre.genre);
              return (
                <button
                  key={genre.genre}
                  onClick={() => toggleGenre(genre.genre, 'include')}
                  onContextMenu={(e) => { e.preventDefault(); toggleGenre(genre.genre, 'exclude'); }}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex justify-between items-center ${
                    isIncluded 
                      ? 'bg-[var(--accent-success)]/20 text-[var(--accent-success)]'
                      : isExcluded
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-[var(--bg-secondary)] hover:bg-[var(--bg-card-hover)] text-[var(--text-secondary)]'
                  }`}
                >
                  <span>{genre.genre}</span>
                  <span className="text-xs opacity-60">{genre.play_count}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Preview Panel */}
        <div className="lg:col-span-1 space-y-4">
          <div className="glass-card p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="font-semibold">{playlistName}</h3>
                <p className="text-sm text-[var(--text-muted)]">
                  {tracks?.length || 0} tracks
                </p>
              </div>
              <button
                onClick={handleCreate}
                disabled={!tracks?.length || createMutation.isPending}
                className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {createMutation.isPending ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Creating...
                  </>
                ) : (
                  'Create on Spotify'
                )}
              </button>
            </div>

            {createMutation.isSuccess && (
              <div className="mb-4 p-3 rounded-lg bg-green-500/10 border border-green-500/20 text-green-400 text-sm">
                Playlist created successfully!
              </div>
            )}

            {createMutation.isError && (
              <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                Failed to create playlist. Try again.
              </div>
            )}
          </div>

          <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
            {loadingTracks ? (
              <div className="space-y-3">
                {[...Array(5)].map((_, i) => (
                  <div key={i} className="flex gap-3 p-3 rounded-lg bg-[var(--bg-secondary)] animate-pulse">
                    <div className="w-12 h-12 rounded skeleton" />
                    <div className="flex-1 space-y-2">
                      <div className="h-4 w-32 skeleton rounded" />
                      <div className="h-3 w-24 skeleton rounded" />
                    </div>
                  </div>
                ))}
              </div>
            ) : tracks?.length > 0 ? (
              <div className="space-y-2">
                {tracks.map((track: any, i: number) => (
                  <TrackCard
                    key={track.track_id || i}
                    track={track.track}
                    artist={track.artist}
                    imageUrl={track.image_url}
                    previewUrl={track.preview_url}
                    spotifyUrl={track.spotify_url}
                    subtitle={track.source === 'discovery' ? `New · via ${track.discovered_via || 'search'}` : `${track.play_count} plays`}
                    index={i}
                  />
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-[var(--text-muted)]">
                <p>No tracks match your filters.</p>
                <p className="text-sm mt-1">Try adjusting the controls.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
