import type {
  FrogAlternative,
  FrogAlternativesResult,
  FrogTrack,
} from './api';

export interface FrogAlternativeScores {
  left: number;
  right: number;
  bottleneck: number;
  improvement: number;
  evidenceCount: number;
  evidenceTotal: number;
  confidenceLabel: string;
}

function boundedScore(value: number | null | undefined) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value as number));
}

export function getFrogAlternativeScores(
  alternative: FrogAlternative,
): FrogAlternativeScores {
  const left = boundedScore(
    alternative.evidence?.left_edge.conservative_similarity
      ?? alternative.left_similarity,
  );
  const right = boundedScore(
    alternative.evidence?.right_edge.conservative_similarity
      ?? alternative.right_similarity,
  );
  const evidenceCount = Math.min(
    4,
    Math.max(
      0,
      (alternative.evidence?.left_edge.direction_count ?? 1)
        + (alternative.evidence?.right_edge.direction_count ?? 1),
    ),
  );

  return {
    left,
    right,
    bottleneck: boundedScore(alternative.ranking_score ?? Math.min(left, right)),
    improvement: alternative.conservative_improvement ?? alternative.improvement,
    evidenceCount,
    evidenceTotal: 4,
    confidenceLabel: alternative.confidence?.level === 'limited'
      ? 'limited evidence'
      : alternative.confidence?.level
        ? `${alternative.confidence.level} confidence`
        : `${evidenceCount}/4 directional links`,
  };
}

export function frogAlternativesMatchRoute(
  result: FrogAlternativesResult,
  tracks: readonly FrogTrack[],
  position: number,
) {
  if (position <= 0 || position >= tracks.length - 1) return false;
  return result.position === position
    && result.left_track.track_id === tracks[position - 1]?.track_id
    && result.current_track.track_id === tracks[position]?.track_id
    && result.right_track.track_id === tracks[position + 1]?.track_id;
}
