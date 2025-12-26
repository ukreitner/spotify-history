'use client';

import { useQuery } from '@tanstack/react-query';
import { getNewArtists } from '@/lib/api';
import ArtistCard from '@/components/ArtistCard';

export default function DiscoverPage() {
  const { data: artists, isLoading, error } = useQuery({
    queryKey: ['newArtists'],
    queryFn: () => getNewArtists(20),
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold mb-2">
            <span className="gradient-text">Discover</span> New Artists
          </h1>
          <p className="text-[var(--text-secondary)]">Finding artists you might love...</p>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {[...Array(10)].map((_, i) => (
            <div key={i} className="glass-card p-5 text-center animate-pulse">
              <div className="w-28 h-28 mx-auto mb-4 rounded-full skeleton" />
              <div className="h-4 w-24 mx-auto skeleton rounded mb-2" />
              <div className="h-3 w-16 mx-auto skeleton rounded" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20">
        <p className="text-[var(--text-muted)]">Failed to load recommendations</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 className="text-4xl font-bold mb-2">
          <span className="gradient-text">Discover</span> New Artists
        </h1>
        <p className="text-[var(--text-secondary)]">
          Artists similar to your favorites that you haven't heard yet
        </p>
      </div>

      {/* Info Banner */}
      <div className="glass-card p-4 flex items-center gap-4 animate-fade-in opacity-0" style={{ animationDelay: '0.1s' }}>
        <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--accent-primary)] to-[var(--accent-secondary)] flex items-center justify-center">
          <svg className="w-6 h-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
          </svg>
        </div>
        <div>
          <p className="font-medium">Personalized for you</p>
          <p className="text-sm text-[var(--text-muted)]">
            Based on genres from your top favorites. Click play to preview!
          </p>
        </div>
      </div>

      {/* Artists Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-4">
        {artists?.map((artist, index) => (
          <ArtistCard
            key={artist.artist_id}
            name={artist.artist_name}
            imageUrl={artist.image_url}
            sampleTrack={artist.sample_track}
            previewUrl={artist.preview_url}
            genres={artist.genres}
            seedArtist={artist.seed_artist}
            foundViaGenre={artist.found_via_genre}
            index={index}
          />
        ))}
      </div>

      {artists?.length === 0 && (
        <div className="text-center py-16 glass-card">
          <h3 className="text-xl font-semibold mb-2">You've heard everyone!</h3>
          <p className="text-[var(--text-muted)]">
            No new artists found. You must have great taste!
          </p>
        </div>
      )}
    </div>
  );
}
