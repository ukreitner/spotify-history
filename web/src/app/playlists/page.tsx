'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  AnchorTrack,
  FlowMode,
  VibePlaylistResult,
  createPlaylist,
  generateVibePlaylist,
} from '@/lib/api';
import TrackCard from '@/components/TrackCard';
import AnchorTrackPicker from '@/components/AnchorTrackPicker';

export default function PlaylistsPage() {
  const [anchors, setAnchors] = useState<AnchorTrack[]>([]);
  const [playlistName, setPlaylistName] = useState('My Vibe Mix');
  const [trackCount, setTrackCount] = useState(30);
  const [discoveryRatio, setDiscoveryRatio] = useState(50);
  const [flowMode, setFlowMode] = useState<FlowMode>('smooth');
  const [excludeArtists, setExcludeArtists] = useState<string[]>([]);
  const [excludeInput, setExcludeInput] = useState('');

  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [coherenceThreshold, setCoherenceThreshold] = useState(50);
  const [maxPerAnchorArtist, setMaxPerAnchorArtist] = useState(3);
  const [maxPerSimilarArtist, setMaxPerSimilarArtist] = useState(2);

  const [result, setResult] = useState<VibePlaylistResult | null>(null);

  const generateMutation = useMutation({
    mutationFn: () =>
      generateVibePlaylist({
        anchor_track_ids: anchors.map((a) => a.track_id),
        track_count: trackCount,
        discovery_ratio: discoveryRatio,
        flow_mode: flowMode,
        exclude_artists: excludeArtists,
        coherence_threshold: coherenceThreshold,
        max_per_anchor_artist: maxPerAnchorArtist,
        max_per_similar_artist: maxPerSimilarArtist,
      }),
    onSuccess: (data) => {
      setResult(data);
    },
  });

  const createMutation = useMutation({
    mutationFn: ({ name, trackIds }: { name: string; trackIds: string[] }) =>
      createPlaylist(name, trackIds, 'Vibe playlist from Spotify History'),
    onSuccess: (data) => {
      if (data.url) window.open(data.url, '_blank');
    },
  });

  const handleAddAnchor = (track: AnchorTrack) => {
    if (anchors.length < 5 && !anchors.find((a) => a.track_id === track.track_id)) {
      setAnchors([...anchors, track]);
    }
  };

  const handleRemoveAnchor = (trackId: string) => {
    setAnchors(anchors.filter((a) => a.track_id !== trackId));
  };

  const handleGenerate = () => {
    if (anchors.length === 0) return;
    generateMutation.mutate();
  };

  const handleCreate = () => {
    if (!result?.tracks.length) return;
    const trackIds = result.tracks.map((t) => t.track_id).filter(Boolean);
    createMutation.mutate({ name: playlistName, trackIds });
  };

  const handleAddExclude = () => {
    const artist = excludeInput.trim();
    if (artist && !excludeArtists.includes(artist)) {
      setExcludeArtists([...excludeArtists, artist]);
      setExcludeInput('');
    }
  };

  const flowModes: { value: FlowMode; label: string; desc: string }[] = [
    { value: 'smooth', label: 'Smooth Flow', desc: 'Minimize jarring transitions' },
    { value: 'energy_arc', label: 'Energy Arc', desc: 'Build up, peak, wind down' },
    { value: 'shuffle', label: 'Shuffle', desc: 'Random order' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          <span className="gradient-text">Vibe</span> Playlist
        </h1>
        <p className="text-[var(--text-secondary)]">
          Pick anchor tracks that define your vibe, and get a coherent playlist
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Anchor Track Picker */}
        <div className="lg:col-span-1 glass-card p-4">
          <h3 className="font-semibold mb-4">1. Pick Your Anchors</h3>
          <p className="text-sm text-[var(--text-muted)] mb-4">
            Select 1-5 tracks that define the vibe you want
          </p>
          <AnchorTrackPicker
            selectedAnchors={anchors}
            onSelect={handleAddAnchor}
            onRemove={handleRemoveAnchor}
            maxAnchors={5}
          />
        </div>

        {/* Settings Panel */}
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
              placeholder="My Vibe Mix"
            />
          </div>

          {/* Track Count */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Number of Tracks: {trackCount}
            </label>
            <input
              type="range"
              min="10"
              max="100"
              step="5"
              value={trackCount}
              onChange={(e) => setTrackCount(parseInt(e.target.value))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>10</span>
              <span>100</span>
            </div>
          </div>

          {/* Discovery Ratio */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              New Music: {discoveryRatio}%
            </label>
            <input
              type="range"
              min="0"
              max="100"
              step="10"
              value={discoveryRatio}
              onChange={(e) => setDiscoveryRatio(parseInt(e.target.value))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>All familiar</span>
              <span>All new</span>
            </div>
          </div>

          {/* Flow Mode */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Flow Mode
            </label>
            <div className="space-y-2">
              {flowModes.map((mode) => (
                <label
                  key={mode.value}
                  className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    flowMode === mode.value
                      ? 'bg-[var(--accent-primary)]/20 border border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-secondary)] border border-transparent hover:border-white/10'
                  }`}
                >
                  <input
                    type="radio"
                    name="flowMode"
                    value={mode.value}
                    checked={flowMode === mode.value}
                    onChange={(e) => setFlowMode(e.target.value as FlowMode)}
                    className="mt-1 accent-[var(--accent-primary)]"
                  />
                  <div>
                    <div className="font-medium text-sm">{mode.label}</div>
                    <div className="text-xs text-[var(--text-muted)]">{mode.desc}</div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Exclude Artists */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Exclude Artists
            </label>
            {excludeArtists.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-3">
                {excludeArtists.map((a) => (
                  <button
                    key={a}
                    onClick={() => setExcludeArtists(excludeArtists.filter((x) => x !== a))}
                    className="px-2 py-1 text-xs rounded-full bg-red-500/20 text-red-400 hover:bg-red-500/30"
                  >
                    {a} x
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={excludeInput}
                onChange={(e) => setExcludeInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddExclude()}
                className="flex-1 px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-primary)] text-sm focus:outline-none focus:border-[var(--accent-primary)]"
                placeholder="Artist name..."
              />
              <button
                onClick={handleAddExclude}
                className="px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-secondary)] hover:border-white/20"
              >
                Add
              </button>
            </div>
          </div>

          {/* Advanced Settings Toggle */}
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="w-full flex items-center justify-between px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            <span>Advanced Settings</span>
            <svg
              className={`w-4 h-4 transition-transform ${showAdvanced ? 'rotate-180' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {/* Advanced Settings Panel */}
          {showAdvanced && (
            <div className="glass-card p-4 space-y-4">
              {/* Coherence Threshold */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  Strictness: {coherenceThreshold}%
                </label>
                <input
                  type="range"
                  min="20"
                  max="80"
                  step="5"
                  value={coherenceThreshold}
                  onChange={(e) => setCoherenceThreshold(parseInt(e.target.value))}
                  className="w-full accent-[var(--accent-primary)]"
                />
                <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                  <span>Loose</span>
                  <span>Strict</span>
                </div>
              </div>

              {/* Max per anchor artist */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  Tracks per anchor artist: {maxPerAnchorArtist}
                </label>
                <input
                  type="range"
                  min="0"
                  max="10"
                  step="1"
                  value={maxPerAnchorArtist}
                  onChange={(e) => setMaxPerAnchorArtist(parseInt(e.target.value))}
                  className="w-full accent-[var(--accent-primary)]"
                />
                <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                  <span>None</span>
                  <span>Many</span>
                </div>
              </div>

              {/* Max per similar artist */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  Tracks per similar artist: {maxPerSimilarArtist}
                </label>
                <input
                  type="range"
                  min="1"
                  max="5"
                  step="1"
                  value={maxPerSimilarArtist}
                  onChange={(e) => setMaxPerSimilarArtist(parseInt(e.target.value))}
                  className="w-full accent-[var(--accent-primary)]"
                />
                <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                  <span>1</span>
                  <span>5</span>
                </div>
              </div>
            </div>
          )}

          {/* Generate Button */}
          <button
            onClick={handleGenerate}
            disabled={anchors.length === 0 || generateMutation.isPending}
            className="w-full btn-primary py-3 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {generateMutation.isPending ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Generating...
              </>
            ) : (
              'Generate Playlist'
            )}
          </button>

          {generateMutation.isError && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              Failed to generate playlist. Try again.
            </div>
          )}
        </div>

        {/* Preview Panel */}
        <div className="lg:col-span-1 space-y-4">
          {result ? (
            <>
              {/* Playlist Header */}
              <div className="glass-card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold">{playlistName}</h3>
                    <p className="text-sm text-[var(--text-muted)]">
                      {result.tracks.length} tracks
                    </p>
                  </div>
                  <button
                    onClick={handleCreate}
                    disabled={createMutation.isPending}
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

                {/* Vibe Profile */}
                {result.vibe_profile.top_genres.length > 0 && (
                  <div className="mb-3">
                    <p className="text-xs text-[var(--text-muted)] mb-1">Vibe genres:</p>
                    <div className="flex flex-wrap gap-1">
                      {result.vibe_profile.top_genres.map((g) => (
                        <span
                          key={g}
                          className="px-2 py-0.5 text-xs rounded-full bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]"
                        >
                          {g}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Stats */}
                <div className="flex gap-4 text-xs text-[var(--text-muted)]">
                  <span>History: {result.counts.history}</span>
                  <span>Discovery: {result.counts.discovery}</span>
                  {result.flow_stats.smooth_transitions > 0 && (
                    <span>Smooth transitions: {result.flow_stats.smooth_transitions}</span>
                  )}
                </div>
              </div>

              {/* Track List */}
              <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
                <div className="space-y-2">
                  {result.tracks.map((track, i) => (
                    <TrackCard
                      key={track.track_id || i}
                      track={track.track}
                      artist={track.artist}
                      imageUrl={track.image_url}
                      previewUrl={track.preview_url}
                      spotifyUrl={track.spotify_url}
                      subtitle={
                        track.source === 'discovery'
                          ? `New · ${track.discovered_via || 'discovery'}`
                          : `${track.play_count} plays`
                      }
                      index={i}
                      energy={track.energy}
                      valence={track.valence}
                      tempo={track.tempo}
                      showFeatures={true}
                    />
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="glass-card p-8 text-center">
              <div className="text-4xl mb-4">🎵</div>
              <h3 className="font-semibold mb-2">Pick Your Anchors</h3>
              <p className="text-sm text-[var(--text-muted)]">
                Select 1-5 tracks that define the vibe you want, then click Generate
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
