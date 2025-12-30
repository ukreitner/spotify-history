'use client';

import { useState, useCallback, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  AnchorTrack,
  FlowMode,
  VibePlaylistResult,
  FrogPlaylistResult,
  FrogProgressEvent,
  createPlaylist,
  generateVibePlaylist,
  generateFrogPlaylistStreaming,
} from '@/lib/api';
import TrackCard from '@/components/TrackCard';
import AnchorTrackPicker from '@/components/AnchorTrackPicker';

type PlaylistMode = 'vibe' | 'frog';

export default function PlaylistsPage() {
  const [mode, setMode] = useState<PlaylistMode>('vibe');

  // Vibe mode state
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

  // Frog mode state
  const [startTrack, setStartTrack] = useState<AnchorTrack | null>(null);
  const [endTrack, setEndTrack] = useState<AnchorTrack | null>(null);
  const [frogTrackCount, setFrogTrackCount] = useState(20);
  const [frogPlaylistName, setFrogPlaylistName] = useState('Frog Transition');

  const [vibeResult, setVibeResult] = useState<VibePlaylistResult | null>(null);
  const [frogResult, setFrogResult] = useState<FrogPlaylistResult | null>(null);

  // Frog streaming state
  const [frogProgress, setFrogProgress] = useState<FrogProgressEvent | null>(null);
  const [frogLoading, setFrogLoading] = useState(false);
  const [frogError, setFrogError] = useState<string | null>(null);
  const cancelFrogRef = useRef<(() => void) | null>(null);

  const generateVibeMutation = useMutation({
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
      setVibeResult(data);
    },
  });

  const handleGenerateFrog = useCallback(() => {
    if (!startTrack || !endTrack) return;

    // Cancel any existing request
    if (cancelFrogRef.current) {
      cancelFrogRef.current();
    }

    setFrogLoading(true);
    setFrogError(null);
    setFrogProgress(null);
    setFrogResult(null);

    cancelFrogRef.current = generateFrogPlaylistStreaming(
      {
        start_track_id: startTrack.track_id,
        end_track_id: endTrack.track_id,
        track_count: frogTrackCount,
      },
      (progress) => {
        setFrogProgress(progress);
      },
      (result) => {
        setFrogLoading(false);
        setFrogProgress(null);
        setFrogResult({
          tracks: result.tracks,
          path_length: result.path_length,
          sampled_length: result.sampled_length,
          success: result.success,
        });
      },
      (error) => {
        setFrogLoading(false);
        setFrogProgress(null);
        setFrogError(error.error);
      }
    );
  }, [startTrack, endTrack, frogTrackCount]);

  const createMutation = useMutation({
    mutationFn: ({ name, trackIds }: { name: string; trackIds: string[] }) =>
      createPlaylist(name, trackIds, mode === 'vibe' ? 'Vibe playlist from Spotify History' : 'Frog transition playlist'),
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

  const handleSelectStart = (track: AnchorTrack) => {
    if (endTrack?.track_id !== track.track_id) {
      setStartTrack(track);
    }
  };

  const handleSelectEnd = (track: AnchorTrack) => {
    if (startTrack?.track_id !== track.track_id) {
      setEndTrack(track);
    }
  };

  const handleGenerate = () => {
    if (mode === 'vibe') {
      if (anchors.length === 0) return;
      generateVibeMutation.mutate();
    } else {
      handleGenerateFrog();
    }
  };

  const handleCreate = () => {
    if (mode === 'vibe') {
      if (!vibeResult?.tracks.length) return;
      const trackIds = vibeResult.tracks.map((t) => t.track_id).filter(Boolean);
      createMutation.mutate({ name: playlistName, trackIds });
    } else {
      if (!frogResult?.tracks.length) return;
      const trackIds = frogResult.tracks.map((t) => t.track_id).filter(Boolean);
      createMutation.mutate({ name: frogPlaylistName, trackIds });
    }
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

  const isGenerating = mode === 'vibe' ? generateVibeMutation.isPending : frogLoading;
  const hasError = mode === 'vibe' ? generateVibeMutation.isError : !!frogError;
  const canGenerate = mode === 'vibe' ? anchors.length > 0 : (startTrack && endTrack);

  return (
    <div className="space-y-6">
      {/* Header with Mode Toggle */}
      <div className="animate-fade-in">
        <div className="flex items-center gap-4 mb-2">
          <h1 className="text-4xl font-bold">
            <span className="gradient-text">{mode === 'vibe' ? 'Vibe' : 'Frog'}</span> Playlist
          </h1>
        </div>
        <p className="text-[var(--text-secondary)] mb-4">
          {mode === 'vibe'
            ? 'Pick anchor tracks that define your vibe, and get a coherent playlist'
            : 'Pick start and end tracks, and create a smooth transition between them'}
        </p>

        {/* Mode Toggle */}
        <div className="flex gap-2">
          <button
            onClick={() => setMode('vibe')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'vibe'
                ? 'bg-[var(--accent-primary)] text-white'
                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            Vibe Mode
          </button>
          <button
            onClick={() => setMode('frog')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'frog'
                ? 'bg-[var(--accent-primary)] text-white'
                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            Frog Mode
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Track Selection */}
        <div className="lg:col-span-1 glass-card p-4">
          {mode === 'vibe' ? (
            <>
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
            </>
          ) : (
            <>
              <h3 className="font-semibold mb-4">1. Pick Start & End</h3>
              <p className="text-sm text-[var(--text-muted)] mb-4">
                Select the tracks to transition between
              </p>

              {/* Start Track */}
              <div className="mb-6">
                <h4 className="text-sm font-medium text-[var(--text-secondary)] mb-2">Start Track</h4>
                {startTrack ? (
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-green-500/10 border border-green-500/30">
                    {startTrack.image_url && (
                      <img src={startTrack.image_url} alt="" className="w-10 h-10 rounded" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{startTrack.track}</div>
                      <div className="text-xs text-[var(--text-muted)] truncate">{startTrack.artist}</div>
                    </div>
                    <button
                      onClick={() => setStartTrack(null)}
                      className="p-1 hover:bg-white/10 rounded"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ) : (
                  <div className="border-2 border-dashed border-green-500/30 rounded-lg p-4">
                    <AnchorTrackPicker
                      selectedAnchors={startTrack ? [startTrack] : []}
                      onSelect={handleSelectStart}
                      onRemove={() => setStartTrack(null)}
                      maxAnchors={1}
                      compact
                    />
                  </div>
                )}
              </div>

              {/* Arrow */}
              <div className="flex justify-center mb-6">
                <svg className="w-6 h-6 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
              </div>

              {/* End Track */}
              <div>
                <h4 className="text-sm font-medium text-[var(--text-secondary)] mb-2">End Track</h4>
                {endTrack ? (
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-purple-500/10 border border-purple-500/30">
                    {endTrack.image_url && (
                      <img src={endTrack.image_url} alt="" className="w-10 h-10 rounded" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{endTrack.track}</div>
                      <div className="text-xs text-[var(--text-muted)] truncate">{endTrack.artist}</div>
                    </div>
                    <button
                      onClick={() => setEndTrack(null)}
                      className="p-1 hover:bg-white/10 rounded"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ) : (
                  <div className="border-2 border-dashed border-purple-500/30 rounded-lg p-4">
                    <AnchorTrackPicker
                      selectedAnchors={endTrack ? [endTrack] : []}
                      onSelect={handleSelectEnd}
                      onRemove={() => setEndTrack(null)}
                      maxAnchors={1}
                      compact
                    />
                  </div>
                )}
              </div>
            </>
          )}
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
              value={mode === 'vibe' ? playlistName : frogPlaylistName}
              onChange={(e) => mode === 'vibe' ? setPlaylistName(e.target.value) : setFrogPlaylistName(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg focus:border-[var(--accent-primary)] focus:outline-none text-[var(--text-primary)]"
              placeholder={mode === 'vibe' ? 'My Vibe Mix' : 'Frog Transition'}
            />
          </div>

          {/* Track Count */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Number of Tracks: {mode === 'vibe' ? trackCount : frogTrackCount}
            </label>
            <input
              type="range"
              min={mode === 'vibe' ? 10 : 5}
              max={mode === 'vibe' ? 100 : 50}
              step="5"
              value={mode === 'vibe' ? trackCount : frogTrackCount}
              onChange={(e) => mode === 'vibe' ? setTrackCount(parseInt(e.target.value)) : setFrogTrackCount(parseInt(e.target.value))}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>{mode === 'vibe' ? 10 : 5}</span>
              <span>{mode === 'vibe' ? 100 : 50}</span>
            </div>
          </div>

          {/* Vibe-only settings */}
          {mode === 'vibe' && (
            <>
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
                  {flowModes.map((m) => (
                    <label
                      key={m.value}
                      className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                        flowMode === m.value
                          ? 'bg-[var(--accent-primary)]/20 border border-[var(--accent-primary)]'
                          : 'bg-[var(--bg-secondary)] border border-transparent hover:border-white/10'
                      }`}
                    >
                      <input
                        type="radio"
                        name="flowMode"
                        value={m.value}
                        checked={flowMode === m.value}
                        onChange={(e) => setFlowMode(e.target.value as FlowMode)}
                        className="mt-1 accent-[var(--accent-primary)]"
                      />
                      <div>
                        <div className="font-medium text-sm">{m.label}</div>
                        <div className="text-xs text-[var(--text-muted)]">{m.desc}</div>
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
            </>
          )}

          {/* Frog mode explanation / progress */}
          {mode === 'frog' && (
            <div className="glass-card p-4">
              {frogProgress ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-[var(--accent-primary)]/30 border-t-[var(--accent-primary)] rounded-full animate-spin" />
                    <h4 className="font-medium text-sm">Finding path...</h4>
                  </div>

                  {/* Phase indicator */}
                  <div className="text-xs text-[var(--text-muted)]">
                    {frogProgress.message || (
                      frogProgress.phase === 'neighborhood' ? 'Building neighborhood...' :
                      frogProgress.phase === 'search' ? 'Searching...' :
                      frogProgress.phase === 'resolving' ? 'Resolving to Spotify...' :
                      'Initializing...'
                    )}
                  </div>

                  {/* Search stats */}
                  {frogProgress.phase === 'search' && (
                    <div className="space-y-2">
                      <div className="grid grid-cols-3 gap-2 text-center">
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-[var(--accent-primary)]">
                            {frogProgress.iteration || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">Iteration</div>
                        </div>
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-green-400">
                            {frogProgress.visited || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">Visited</div>
                        </div>
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-blue-400">
                            {frogProgress.queue_size || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">In Queue</div>
                        </div>
                      </div>

                      {/* Current track */}
                      {frogProgress.current_track && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)] text-xs">
                          <span className="text-[var(--text-muted)]">Exploring: </span>
                          <span className="text-[var(--text-primary)]">{frogProgress.current_track}</span>
                        </div>
                      )}

                      {/* Heuristic progress bar */}
                      {frogProgress.best_h !== undefined && (
                        <div>
                          <div className="flex justify-between text-xs text-[var(--text-muted)] mb-1">
                            <span>Distance to goal</span>
                            <span>{((1 - frogProgress.best_h) * 100).toFixed(0)}%</span>
                          </div>
                          <div className="h-2 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
                            <div
                              className="h-full bg-gradient-to-r from-[var(--accent-primary)] to-green-400 transition-all duration-300"
                              style={{ width: `${(1 - frogProgress.best_h) * 100}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Neighborhood building */}
                  {frogProgress.phase === 'neighborhood' && (
                    <div className="grid grid-cols-2 gap-2 text-center">
                      {frogProgress.neighborhood_1hop !== undefined && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-[var(--accent-primary)]">
                            {frogProgress.neighborhood_1hop}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">1-hop tracks</div>
                        </div>
                      )}
                      {frogProgress.neighborhood_2hop !== undefined && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-purple-400">
                            {frogProgress.neighborhood_2hop}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">2-hop tracks</div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <>
                  <h4 className="font-medium text-sm mb-2">How it works</h4>
                  <p className="text-xs text-[var(--text-muted)]">
                    The algorithm uses A* pathfinding over Last.fm&apos;s track similarity graph to find the smoothest musical journey from your start track to your end track.
                  </p>
                </>
              )}
            </div>
          )}

          {/* Generate Button */}
          <button
            onClick={handleGenerate}
            disabled={!canGenerate || isGenerating}
            className="w-full btn-primary py-3 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {isGenerating ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                {mode === 'frog' ? 'Finding path...' : 'Generating...'}
              </>
            ) : (
              mode === 'frog' ? 'Find Path' : 'Generate Playlist'
            )}
          </button>

          {hasError && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {mode === 'frog' ? (frogError || 'No path found. Try tracks that are more similar.') : 'Failed to generate playlist. Try again.'}
            </div>
          )}
        </div>

        {/* Preview Panel */}
        <div className="lg:col-span-1 space-y-4">
          {mode === 'vibe' && vibeResult ? (
            <>
              {/* Playlist Header */}
              <div className="glass-card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold">{playlistName}</h3>
                    <p className="text-sm text-[var(--text-muted)]">
                      {vibeResult.tracks.length} tracks
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
                {vibeResult.vibe_profile.top_genres.length > 0 && (
                  <div className="mb-3">
                    <p className="text-xs text-[var(--text-muted)] mb-1">Vibe genres:</p>
                    <div className="flex flex-wrap gap-1">
                      {vibeResult.vibe_profile.top_genres.map((g) => (
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
                  <span>History: {vibeResult.counts.history}</span>
                  <span>Discovery: {vibeResult.counts.discovery}</span>
                  {vibeResult.flow_stats.smooth_transitions > 0 && (
                    <span>Smooth transitions: {vibeResult.flow_stats.smooth_transitions}</span>
                  )}
                </div>
              </div>

              {/* Track List */}
              <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
                <div className="space-y-2">
                  {vibeResult.tracks.map((track, i) => (
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
          ) : mode === 'frog' && frogResult ? (
            <>
              {/* Frog Playlist Header */}
              <div className="glass-card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold">{frogPlaylistName}</h3>
                    <p className="text-sm text-[var(--text-muted)]">
                      {frogResult.tracks.length} tracks
                      {frogResult.path_length !== frogResult.sampled_length && (
                        <span> (sampled from {frogResult.path_length})</span>
                      )}
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
              </div>

              {/* Frog Track List */}
              <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
                <div className="space-y-2">
                  {frogResult.tracks.map((track, i) => (
                    <div key={track.track_id || i} className="relative">
                      {/* Role badge */}
                      {(track.role === 'start' || track.role === 'end') && (
                        <div className={`absolute -left-2 top-1/2 -translate-y-1/2 px-1.5 py-0.5 text-xs rounded ${
                          track.role === 'start' ? 'bg-green-500/20 text-green-400' : 'bg-purple-500/20 text-purple-400'
                        }`}>
                          {track.role === 'start' ? 'START' : 'END'}
                        </div>
                      )}
                      <TrackCard
                        track={track.track}
                        artist={track.artist}
                        imageUrl={track.image_url}
                        previewUrl={track.preview_url}
                        spotifyUrl={track.spotify_url}
                        subtitle={track.role}
                        index={i}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="glass-card p-8 text-center">
              <div className="text-4xl mb-4">{mode === 'vibe' ? '🎵' : '🐸'}</div>
              <h3 className="font-semibold mb-2">
                {mode === 'vibe' ? 'Pick Your Anchors' : 'Pick Start & End'}
              </h3>
              <p className="text-sm text-[var(--text-muted)]">
                {mode === 'vibe'
                  ? 'Select 1-5 tracks that define the vibe you want, then click Generate'
                  : 'Select your start and end tracks, then click Find Path'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
