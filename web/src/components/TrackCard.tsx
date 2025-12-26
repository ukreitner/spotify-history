'use client';

import { useState, useRef } from 'react';

interface TrackCardProps {
  track: string;
  artist: string;
  imageUrl?: string | null;
  subtitle?: string;
  previewUrl?: string | null;
  spotifyUrl?: string | null;
  index?: number;
  // Audio features (0-1 scale from Spotify)
  energy?: number | null;
  valence?: number | null;
  danceability?: number | null;
  tempo?: number | null;
  acousticness?: number | null;
  showFeatures?: boolean;
}

export default function TrackCard({
  track,
  artist,
  imageUrl,
  subtitle,
  previewUrl,
  spotifyUrl,
  index = 0,
  energy,
  valence,
  danceability,
  tempo,
  acousticness,
  showFeatures = false,
}: TrackCardProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  const togglePlay = () => {
    if (!audioRef.current) return;
    
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  const handleEnded = () => setIsPlaying(false);

  return (
    <div 
      className={`glass-card p-4 animate-fade-in opacity-0`}
      style={{ animationDelay: `${index * 0.05}s` }}
    >
      <div className="flex gap-4 items-center">
        {/* Album Art */}
        <div className="relative w-16 h-16 flex-shrink-0 group">
          <div className="media-image w-full h-full rounded-lg overflow-hidden bg-[var(--bg-secondary)]">
            {imageUrl ? (
              <img src={imageUrl} alt={track} className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-[var(--accent-primary)]/20 to-[var(--accent-secondary)]/20">
                <svg className="w-6 h-6 text-[var(--text-muted)]" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                </svg>
              </div>
            )}
          </div>
          
          {/* Play button overlay */}
          {previewUrl && (
            <button
              onClick={togglePlay}
              className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity rounded-lg"
            >
              <div className="w-10 h-10 rounded-full bg-[var(--accent-primary)] flex items-center justify-center shadow-lg">
                {isPlaying ? (
                  <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="4" width="4" height="16" />
                    <rect x="14" y="4" width="4" height="16" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                    <polygon points="5,3 19,12 5,21" />
                  </svg>
                )}
              </div>
            </button>
          )}
        </div>

        {/* Track Info */}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-[var(--text-primary)] truncate">{track}</p>
          <p className="text-sm text-[var(--text-secondary)] truncate">{artist}</p>
          {subtitle && (
            <p className="text-xs text-[var(--text-muted)] mt-1">{subtitle}</p>
          )}
          {/* Audio feature badges */}
          {showFeatures && (energy != null || valence != null || tempo != null) && (
            <div className="flex flex-wrap gap-1 mt-2">
              {energy != null && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full"
                  style={{
                    backgroundColor: `hsl(${energy * 60}, 70%, 40%)`,
                    color: 'white',
                  }}
                  title={`Energy: ${Math.round(energy * 100)}%`}
                >
                  ⚡{Math.round(energy * 100)}
                </span>
              )}
              {valence != null && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full"
                  style={{
                    backgroundColor: `hsl(${valence * 120}, 60%, 45%)`,
                    color: 'white',
                  }}
                  title={`Mood: ${Math.round(valence * 100)}% (higher = happier)`}
                >
                  {valence > 0.5 ? '😊' : '😔'}{Math.round(valence * 100)}
                </span>
              )}
              {tempo != null && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-secondary)]/30 text-[var(--accent-secondary)]"
                  title={`Tempo: ${Math.round(tempo)} BPM`}
                >
                  🎵{Math.round(tempo)}
                </span>
              )}
              {danceability != null && danceability > 0.6 && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-pink-500/30 text-pink-400"
                  title={`Danceability: ${Math.round(danceability * 100)}%`}
                >
                  💃
                </span>
              )}
              {acousticness != null && acousticness > 0.6 && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/30 text-amber-400"
                  title={`Acoustic: ${Math.round(acousticness * 100)}%`}
                >
                  🎸
                </span>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          {spotifyUrl && (
            <a
              href={spotifyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 rounded-lg hover:bg-white/5 transition-colors text-[var(--text-muted)] hover:text-[var(--accent-success)]"
              title="Open in Spotify"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
              </svg>
            </a>
          )}
        </div>
      </div>

      {/* Hidden audio element */}
      {previewUrl && (
        <audio
          ref={audioRef}
          src={previewUrl}
          onEnded={handleEnded}
          preload="none"
        />
      )}
    </div>
  );
}
