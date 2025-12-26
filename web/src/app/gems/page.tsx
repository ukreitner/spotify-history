'use client';

import { useQuery } from '@tanstack/react-query';
import { getForgottenGems } from '@/lib/api';
import TrackCard from '@/components/TrackCard';

export default function GemsPage() {
  const { data: gems, isLoading, error } = useQuery({
    queryKey: ['forgottenGems'],
    queryFn: () => getForgottenGems(),
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold mb-2">
            <span className="gradient-text">Forgotten</span> Gems
          </h1>
          <p className="text-[var(--text-secondary)]">Finding tracks you loved but forgot about...</p>
        </div>
        <div className="space-y-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="glass-card p-4 animate-pulse">
              <div className="flex gap-4">
                <div className="w-16 h-16 rounded-lg skeleton" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-48 skeleton rounded" />
                  <div className="h-3 w-32 skeleton rounded" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20">
        <p className="text-[var(--text-muted)]">Failed to load forgotten gems</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          <span className="gradient-text">Forgotten</span> Gems
        </h1>
        <p className="text-[var(--text-secondary)]">
          Tracks you loved but haven't played in a while. Time to rediscover!
        </p>
      </div>

      {/* Stats Bar */}
      {gems && gems.length > 0 && (
        <div className="glass-card p-4 flex items-center gap-6 animate-fade-in opacity-0" style={{ animationDelay: '0.1s' }}>
          <div>
            <p className="text-sm text-[var(--text-muted)]">Gems Found</p>
            <p className="font-bold text-[var(--accent-primary)]">{gems.length}</p>
          </div>
          <div className="h-8 w-px bg-white/10" />
          <div>
            <p className="text-sm text-[var(--text-muted)]">Longest Forgotten</p>
            <p className="font-bold text-[var(--accent-secondary)]">
              {Math.max(...gems.map(g => g.days_since_played))} days
            </p>
          </div>
          <div className="h-8 w-px bg-white/10" />
          <div>
            <p className="text-sm text-[var(--text-muted)]">Most Played Gem</p>
            <p className="font-bold text-[var(--accent-tertiary)]">
              {Math.max(...gems.map(g => g.play_count))} plays
            </p>
          </div>
        </div>
      )}

      {/* Gems List */}
      <div className="space-y-3">
        {gems?.map((gem, index) => (
          <TrackCard
            key={gem.track_id}
            track={gem.track}
            artist={gem.artist}
            imageUrl={gem.image_url}
            spotifyUrl={gem.spotify_url}
            previewUrl={gem.preview_url}
            subtitle={`${gem.play_count} plays · Last played ${gem.days_since_played} days ago`}
            index={index}
          />
        ))}
      </div>

      {gems?.length === 0 && (
        <div className="text-center py-16 glass-card">
          <h3 className="text-xl font-semibold mb-2">No forgotten gems!</h3>
          <p className="text-[var(--text-muted)]">
            You've been keeping up with all your favorites. Keep listening!
          </p>
        </div>
      )}
    </div>
  );
}
