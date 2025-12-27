'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AnchorTrack,
  getRecentTracks,
  searchHistoryTracks,
  searchSpotifyTracks,
} from '@/lib/api';

interface AnchorTrackPickerProps {
  selectedAnchors: AnchorTrack[];
  onSelect: (track: AnchorTrack) => void;
  onRemove: (trackId: string) => void;
  maxAnchors?: number;
}

type TabType = 'recent' | 'history' | 'spotify';

export default function AnchorTrackPicker({
  selectedAnchors,
  onSelect,
  onRemove,
  maxAnchors = 5,
}: AnchorTrackPickerProps) {
  const [activeTab, setActiveTab] = useState<TabType>('recent');
  const [historyQuery, setHistoryQuery] = useState('');
  const [spotifyQuery, setSpotifyQuery] = useState('');

  const selectedIds = new Set(selectedAnchors.map(t => t.track_id));

  // Fetch recent tracks
  const { data: recentTracks, isLoading: loadingRecent } = useQuery({
    queryKey: ['recentTracks'],
    queryFn: () => getRecentTracks(7, 30),
  });

  // Search history
  const { data: historyResults, isLoading: loadingHistory } = useQuery({
    queryKey: ['historySearch', historyQuery],
    queryFn: () => searchHistoryTracks(historyQuery, 20),
    enabled: historyQuery.length >= 2,
  });

  // Search Spotify
  const { data: spotifyResults, isLoading: loadingSpotify } = useQuery({
    queryKey: ['spotifySearch', spotifyQuery],
    queryFn: () => searchSpotifyTracks(spotifyQuery, 20),
    enabled: spotifyQuery.length >= 2,
  });

  const handleSelect = (track: AnchorTrack) => {
    if (selectedIds.has(track.track_id)) return;
    if (selectedAnchors.length >= maxAnchors) return;
    onSelect(track);
  };

  const tabs: { id: TabType; label: string }[] = [
    { id: 'recent', label: 'Recent' },
    { id: 'history', label: 'My History' },
    { id: 'spotify', label: 'Search Spotify' },
  ];

  return (
    <div className="space-y-4">
      {/* Selected Anchors */}
      {selectedAnchors.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--text-secondary)]">
            Selected anchors ({selectedAnchors.length}/{maxAnchors})
          </p>
          <div className="flex flex-wrap gap-2">
            {selectedAnchors.map((track) => (
              <div
                key={track.track_id}
                className="flex items-center gap-2 bg-[var(--accent-primary)]/20 rounded-lg px-3 py-2"
              >
                {track.image_url && (
                  <img
                    src={track.image_url}
                    alt=""
                    className="w-8 h-8 rounded object-cover"
                  />
                )}
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--text-primary)] truncate max-w-[150px]">
                    {track.track}
                  </p>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-[150px]">
                    {track.artist}
                  </p>
                </div>
                <button
                  onClick={() => onRemove(track.track_id)}
                  className="p-1 hover:bg-white/10 rounded transition-colors"
                >
                  <svg
                    className="w-4 h-4 text-[var(--text-muted)]"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-white/10">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? 'text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="min-h-[200px]">
        {/* Recent Tab */}
        {activeTab === 'recent' && (
          <div className="space-y-2">
            {loadingRecent ? (
              <p className="text-[var(--text-muted)] text-sm">Loading recent tracks...</p>
            ) : recentTracks && recentTracks.length > 0 ? (
              <div className="grid gap-2 max-h-[300px] overflow-y-auto">
                {recentTracks.map((track) => (
                  <TrackRow
                    key={track.track_id}
                    track={track}
                    isSelected={selectedIds.has(track.track_id)}
                    disabled={selectedAnchors.length >= maxAnchors && !selectedIds.has(track.track_id)}
                    onSelect={() => handleSelect({ ...track, source: 'recent' })}
                  />
                ))}
              </div>
            ) : (
              <p className="text-[var(--text-muted)] text-sm">No recent tracks found</p>
            )}
          </div>
        )}

        {/* History Tab */}
        {activeTab === 'history' && (
          <div className="space-y-3">
            <input
              type="text"
              value={historyQuery}
              onChange={(e) => setHistoryQuery(e.target.value)}
              placeholder="Search your listening history..."
              className="w-full px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)]"
            />
            {historyQuery.length >= 2 && (
              loadingHistory ? (
                <p className="text-[var(--text-muted)] text-sm">Searching...</p>
              ) : historyResults && historyResults.length > 0 ? (
                <div className="grid gap-2 max-h-[250px] overflow-y-auto">
                  {historyResults.map((track) => (
                    <TrackRow
                      key={track.track_id}
                      track={track}
                      isSelected={selectedIds.has(track.track_id)}
                      disabled={selectedAnchors.length >= maxAnchors && !selectedIds.has(track.track_id)}
                      onSelect={() => handleSelect({ ...track, source: 'history' })}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-[var(--text-muted)] text-sm">No results found</p>
              )
            )}
          </div>
        )}

        {/* Spotify Tab */}
        {activeTab === 'spotify' && (
          <div className="space-y-3">
            <input
              type="text"
              value={spotifyQuery}
              onChange={(e) => setSpotifyQuery(e.target.value)}
              placeholder="Search any song on Spotify..."
              className="w-full px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)]"
            />
            {spotifyQuery.length >= 2 && (
              loadingSpotify ? (
                <p className="text-[var(--text-muted)] text-sm">Searching Spotify...</p>
              ) : spotifyResults && spotifyResults.length > 0 ? (
                <div className="grid gap-2 max-h-[250px] overflow-y-auto">
                  {spotifyResults.map((track) => (
                    <TrackRow
                      key={track.track_id}
                      track={track}
                      isSelected={selectedIds.has(track.track_id)}
                      disabled={selectedAnchors.length >= maxAnchors && !selectedIds.has(track.track_id)}
                      onSelect={() => handleSelect({ ...track, source: 'spotify' })}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-[var(--text-muted)] text-sm">No results found</p>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface TrackRowProps {
  track: AnchorTrack;
  isSelected: boolean;
  disabled: boolean;
  onSelect: () => void;
}

function TrackRow({ track, isSelected, disabled, onSelect }: TrackRowProps) {
  return (
    <button
      onClick={onSelect}
      disabled={disabled || isSelected}
      className={`flex items-center gap-3 p-2 rounded-lg text-left w-full transition-colors ${
        isSelected
          ? 'bg-[var(--accent-primary)]/30 cursor-default'
          : disabled
          ? 'opacity-50 cursor-not-allowed'
          : 'hover:bg-white/5 cursor-pointer'
      }`}
    >
      {track.image_url ? (
        <img
          src={track.image_url}
          alt=""
          className="w-10 h-10 rounded object-cover flex-shrink-0"
        />
      ) : (
        <div className="w-10 h-10 rounded bg-[var(--bg-secondary)] flex items-center justify-center flex-shrink-0">
          <svg className="w-5 h-5 text-[var(--text-muted)]" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
          </svg>
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[var(--text-primary)] truncate">
          {track.track}
        </p>
        <p className="text-xs text-[var(--text-secondary)] truncate">
          {track.artist}
          {track.play_count && track.play_count > 0 && (
            <span className="text-[var(--text-muted)]"> · {track.play_count} plays</span>
          )}
        </p>
      </div>
      {isSelected && (
        <svg
          className="w-5 h-5 text-[var(--accent-primary)] flex-shrink-0"
          fill="currentColor"
          viewBox="0 0 24 24"
        >
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
        </svg>
      )}
    </button>
  );
}
