'use client';

import { useState, useRef } from 'react';

interface ArtistCardProps {
  name: string;
  imageUrl?: string | null;
  sampleTrack?: string;
  previewUrl?: string | null;
  genres?: string[];
  seedArtist?: string;
  foundViaGenre?: string;
  index?: number;
}

export default function ArtistCard({ 
  name, 
  imageUrl, 
  sampleTrack, 
  previewUrl,
  genres,
  seedArtist,
  foundViaGenre,
  index = 0 
}: ArtistCardProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  const togglePlay = (e: React.MouseEvent) => {
    e.stopPropagation();
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
      className={`glass-card p-5 text-center animate-fade-in opacity-0 group`}
      style={{ animationDelay: `${index * 0.05}s` }}
    >
      {/* Artist Image */}
      <div className="relative w-28 h-28 mx-auto mb-4">
        <div className="w-full h-full rounded-full overflow-hidden bg-gradient-to-br from-[var(--accent-primary)]/20 to-[var(--accent-secondary)]/20 ring-2 ring-white/5 group-hover:ring-[var(--accent-primary)]/50 transition-all">
          {imageUrl ? (
            <img src={imageUrl} alt={name} className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <svg className="w-12 h-12 text-[var(--text-muted)]" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
              </svg>
            </div>
          )}
        </div>
        
        {/* Play button */}
        {previewUrl && (
          <button
            onClick={togglePlay}
            className={`
              absolute -bottom-1 -right-1 w-10 h-10 rounded-full 
              flex items-center justify-center shadow-lg
              transition-all duration-200
              ${isPlaying 
                ? 'bg-[var(--accent-primary)] scale-110' 
                : 'bg-[var(--bg-card)] border border-white/10 opacity-0 group-hover:opacity-100'
              }
            `}
          >
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
          </button>
        )}
      </div>

      {/* Artist Info */}
      <h3 className="font-semibold text-[var(--text-primary)] truncate mb-1">{name}</h3>
      
      {(seedArtist || foundViaGenre) && (
        <p className="text-xs text-[var(--text-muted)] mb-2">
          {seedArtist ? (
            <>Similar to <span className="text-[var(--accent-secondary)]">{seedArtist}</span></>
          ) : (
            <>Found via <span className="text-[var(--accent-secondary)]">{foundViaGenre}</span></>
          )}
        </p>
      )}
      
      {genres && genres.length > 0 && (
        <div className="flex flex-wrap justify-center gap-1 mb-2">
          {genres.slice(0, 2).map(genre => (
            <span key={genre} className="badge badge-purple text-[10px]">
              {genre}
            </span>
          ))}
        </div>
      )}
      
      {sampleTrack && (
        <p className="text-xs text-[var(--text-secondary)] truncate">
          <span className="text-[var(--text-muted)]">Try:</span> {sampleTrack}
        </p>
      )}

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
