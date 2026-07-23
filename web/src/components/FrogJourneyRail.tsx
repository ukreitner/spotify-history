'use client';

import Image from 'next/image';
import { useId } from 'react';
import type { FrogTrack } from '@/lib/api';

export interface FrogJourneyRailProps {
  tracks: FrogTrack[];
  activePosition: number;
  playing: boolean;
  onPositionChange: (position: number) => void;
  onPlayingChange: (playing: boolean) => void;
  className?: string;
}

function clampPosition(position: number, trackCount: number) {
  if (!trackCount) return 0;
  if (!Number.isFinite(position)) return 0;
  return Math.min(Math.max(Math.trunc(position), 0), trackCount - 1);
}

function qualityLabel(similarity?: number | null) {
  if (similarity == null) return 'Not scored';
  if (similarity >= 0.25) return 'Tiny hop';
  if (similarity >= 0.12) return 'Noticeable hop';
  return 'Big leap';
}

function qualityColor(similarity?: number | null) {
  if (similarity == null) return 'var(--border-strong)';
  if (similarity >= 0.25) return 'var(--accent-primary)';
  if (similarity >= 0.12) return 'var(--accent-tertiary)';
  return 'var(--accent-pink)';
}

function qualityText(similarity?: number | null) {
  if (similarity == null) return qualityLabel(similarity);
  return `${Math.round(similarity * 100)}% · ${qualityLabel(similarity)}`;
}

export default function FrogJourneyRail({
  tracks,
  activePosition,
  playing,
  onPositionChange,
  onPlayingChange,
  className = '',
}: FrogJourneyRailProps) {
  const positionControlId = useId();
  const position = clampPosition(activePosition, tracks.length);
  const activeTrack = tracks[position];
  const incomingSimilarity = activeTrack?.transition_similarity;
  const outgoingSimilarity = tracks[position + 1]?.transition_similarity;
  const canGoPrevious = position > 0;
  const canGoNext = position < tracks.length - 1;

  if (!tracks.length) {
    return (
      <section
        aria-label="Frog journey"
        className={`glass-card p-5 ${className}`}
      >
        <div className="flex items-center gap-3 text-[var(--text-muted)]">
          <span
            aria-hidden="true"
            className="grid h-10 w-10 place-items-center rounded-full border border-dashed border-[var(--border-strong)]"
          >
            🐸
          </span>
          <div>
            <h2 className="font-medium text-[var(--text-secondary)]">Frog journey</h2>
            <p className="text-sm">Generate a route to walk through it here.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-label="Frog journey"
      className={`glass-card overflow-hidden ${className}`}
    >
      <div className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="text-lg">🐸</span>
            <h2 className="font-medium">Journey rail</h2>
            <span className="font-mono text-xs text-[var(--text-muted)]">
              {position + 1}/{tracks.length}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
            <span
              aria-hidden="true"
              className={`h-2 w-2 rounded-full ${
                playing
                  ? 'bg-[var(--accent-primary)] animate-pulse'
                  : 'bg-[var(--border-strong)]'
              }`}
            />
            <span>{playing ? 'Walking the route' : 'Paused'}</span>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
          <div className="h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-[var(--border-soft)] bg-[var(--bg-elevated)]">
            {activeTrack.image_url ? (
              <Image
                src={activeTrack.image_url}
                alt=""
                width={64}
                height={64}
                unoptimized
                className="h-full w-full object-cover"
              />
            ) : (
              <div
                aria-hidden="true"
                className="grid h-full w-full place-items-center text-2xl text-[var(--text-muted)]"
              >
                ♪
              </div>
            )}
          </div>

          <div aria-live="polite" className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate font-medium">{activeTrack.track}</p>
              <span className="rounded-full bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                {activeTrack.role}
              </span>
            </div>
            <p className="truncate text-sm text-[var(--text-secondary)]">
              {activeTrack.artist}
              {activeTrack.album ? ` · ${activeTrack.album}` : ''}
            </p>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {position > 0 && (
                <span
                  className="rounded-full border px-2 py-1 text-[var(--text-secondary)]"
                  style={{ borderColor: qualityColor(incomingSimilarity) }}
                >
                  Jump in: {qualityText(incomingSimilarity)}
                </span>
              )}
              {position < tracks.length - 1 && (
                <span
                  className="rounded-full border px-2 py-1 text-[var(--text-secondary)]"
                  style={{ borderColor: qualityColor(outgoingSimilarity) }}
                >
                  Next: {qualityText(outgoingSimilarity)}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 sm:justify-end">
            <button
              type="button"
              onClick={() => onPositionChange(position - 1)}
              disabled={!canGoPrevious}
              className="btn-secondary !px-3 !py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Previous track"
            >
              <span aria-hidden="true">←</span>
              <span className="sr-only">Previous</span>
            </button>
            <button
              type="button"
              onClick={() => {
                if (playing) {
                  onPlayingChange(false);
                  return;
                }
                if (!canGoNext) onPositionChange(0);
                onPlayingChange(true);
              }}
              disabled={tracks.length < 2}
              aria-pressed={playing}
              className={`!px-4 !py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${
                playing
                  ? 'btn-secondary'
                  : 'rounded-xl bg-[var(--accent-primary)] font-semibold text-[var(--bg-primary)] transition hover:brightness-110'
              }`}
            >
              <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
              <span className="ml-2">{playing ? 'Pause' : 'Walk'}</span>
            </button>
            <button
              type="button"
              onClick={() => onPositionChange(position + 1)}
              disabled={!canGoNext}
              className="btn-secondary !px-3 !py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Next track"
            >
              <span aria-hidden="true">→</span>
              <span className="sr-only">Next</span>
            </button>
          </div>
        </div>

        <div>
          <label
            htmlFor={positionControlId}
            className="mb-2 flex items-center justify-between text-xs text-[var(--text-muted)]"
          >
            <span>Scrub the route</span>
            <span className="font-mono">
              {position + 1} of {tracks.length}
            </span>
          </label>
          <input
            id={positionControlId}
            type="range"
            min={0}
            max={tracks.length - 1}
            step={1}
            value={position}
            onChange={(event) => onPositionChange(Number(event.target.value))}
            aria-valuetext={`${position + 1} of ${tracks.length}: ${activeTrack.track} by ${activeTrack.artist}`}
            className="w-full cursor-pointer accent-[var(--accent-primary)]"
          />
        </div>
      </div>

      <div className="border-t border-[var(--border-soft)] bg-[var(--bg-secondary)]/55 px-4 py-4 sm:px-5">
        <div className="overflow-x-auto pb-2">
          <ol
            className="flex items-center"
            style={{ minWidth: `${Math.max(440, tracks.length * 34)}px` }}
            aria-label="Tracks in route"
          >
            {tracks.map((track, index) => {
              const isActive = index === position;
              const similarity = track.transition_similarity;
              const title = index === 0
                ? `1. ${track.track} by ${track.artist}, start`
                : `${index + 1}. ${track.track} by ${track.artist}, ${qualityText(similarity)} from previous track`;

              return (
                <li
                  key={`${track.track_id}-${index}`}
                  className={index === 0 ? 'shrink-0' : 'flex flex-1 items-center'}
                >
                  {index > 0 && (
                    <span
                      aria-hidden="true"
                      className="h-1 min-w-2 flex-1"
                      style={{ backgroundColor: qualityColor(similarity) }}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => onPositionChange(index)}
                    aria-label={title}
                    aria-current={isActive ? 'step' : undefined}
                    title={title}
                    className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border font-mono text-[10px] transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-secondary)] ${
                      isActive
                        ? 'scale-110 bg-[var(--accent-primary)] text-[var(--bg-primary)]'
                        : 'bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:scale-110 hover:text-[var(--text-primary)]'
                    }`}
                    style={{
                      borderColor: isActive
                        ? 'var(--accent-primary)'
                        : index === 0
                          ? 'var(--accent-secondary)'
                          : qualityColor(similarity),
                    }}
                  >
                    {index + 1}
                  </button>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-[var(--text-secondary)]">
          <span className="flex items-center gap-1.5">
            <span className="h-1 w-5 bg-[var(--accent-primary)]" aria-hidden="true" />
            Tiny hop
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-1 w-5 bg-[var(--accent-tertiary)]" aria-hidden="true" />
            Noticeable hop
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-1 w-5 bg-[var(--accent-pink)]" aria-hidden="true" />
            Big leap
          </span>
        </div>
      </div>
    </section>
  );
}
