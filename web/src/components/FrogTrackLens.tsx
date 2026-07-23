'use client';

import { useMemo } from 'react';
import type { FrogGraphEdge, FrogGraphNode } from '@/lib/api';

export interface FrogTrackLensProps {
  /** The graph node currently in focus. Pass null to render the gentle empty state. */
  selectedNode: FrogGraphNode | null;
  /** All currently visible graph nodes, used to resolve connected tracks. */
  nodes: readonly FrogGraphNode[];
  /** All currently visible graph edges, used to calculate degree and similarity. */
  edges: readonly FrogGraphEdge[];
  /** Called when the listener chooses one of the connected tracks. */
  onSelectNode: (node: FrogGraphNode) => void;
  /** When supplied, adds a replacement-search action for the selected track. */
  onFindReplacements?: (node: FrogGraphNode) => void;
  findingReplacements?: boolean;
  replacementDisabled?: boolean;
  maxNeighbors?: number;
  className?: string;
}

interface NeighborConnection {
  node: FrogGraphNode;
  similarity: number;
  onRoute: boolean;
}

function clampSimilarity(value: number) {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

function nodeContext(node: FrogGraphNode) {
  if (node.direction === 'route' || node.route_position !== undefined) {
    return {
      label: 'On final route',
      detail: node.route_position === undefined
        ? 'Chosen path'
        : `Route step ${node.route_position + 1}`,
      dot: 'bg-[var(--accent-primary)]',
      badge: 'border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]',
    };
  }

  if (node.direction === 'forward') {
    return {
      label: 'Reached from start',
      detail: node.depth === 1 ? '1 search hop' : `${node.depth} search hops`,
      dot: 'bg-[var(--accent-secondary)]',
      badge: 'border-[var(--accent-secondary)]/30 bg-[var(--accent-secondary)]/10 text-[var(--accent-secondary)]',
    };
  }

  return {
    label: 'Reached from end',
    detail: node.depth === 1 ? '1 search hop' : `${node.depth} search hops`,
    dot: 'bg-[var(--accent-pink)]',
    badge: 'border-[var(--accent-pink)]/30 bg-[var(--accent-pink)]/10 text-[var(--accent-pink)]',
  };
}

function similarityTone(similarity: number) {
  if (similarity >= 0.4) return 'text-[var(--accent-success)]';
  if (similarity >= 0.2) return 'text-[var(--accent-tertiary)]';
  return 'text-[var(--accent-pink)]';
}

export default function FrogTrackLens({
  selectedNode,
  nodes,
  edges,
  onSelectNode,
  onFindReplacements,
  findingReplacements = false,
  replacementDisabled = false,
  maxNeighbors = 5,
  className = '',
}: FrogTrackLensProps) {
  const graphDetails = useMemo(() => {
    if (!selectedNode) {
      return { degree: 0, neighbors: [] as NeighborConnection[] };
    }

    const nodesById = new Map(nodes.map((node) => [node.id, node]));
    const connectedIds = new Set<string>();
    const connections = new Map<string, NeighborConnection>();

    edges.forEach((edge) => {
      let neighborId: string | null = null;
      if (edge.source === selectedNode.id) neighborId = edge.target;
      if (edge.target === selectedNode.id) neighborId = edge.source;
      if (!neighborId || neighborId === selectedNode.id) return;

      connectedIds.add(neighborId);
      const neighbor = nodesById.get(neighborId);
      if (!neighbor) return;

      const similarity = clampSimilarity(edge.similarity);
      const current = connections.get(neighborId);
      if (!current) {
        connections.set(neighborId, {
          node: neighbor,
          similarity,
          onRoute: edge.kind === 'route',
        });
        return;
      }

      current.similarity = Math.max(current.similarity, similarity);
      current.onRoute = current.onRoute || edge.kind === 'route';
    });

    return {
      degree: connectedIds.size,
      neighbors: Array.from(connections.values())
        .sort((left, right) => (
          right.similarity - left.similarity
          || left.node.track.localeCompare(right.node.track)
        ))
        .slice(0, Math.max(0, maxNeighbors)),
    };
  }, [edges, maxNeighbors, nodes, selectedNode]);

  if (!selectedNode) {
    return (
      <aside
        className={`rounded-2xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-secondary)]/55 p-6 text-center ${className}`}
        aria-label="Track lens"
      >
        <div
          className="mx-auto grid h-11 w-11 place-items-center rounded-full border border-[var(--border-soft)] bg-[var(--bg-elevated)] text-[var(--accent-primary)]"
          aria-hidden="true"
        >
          ◎
        </div>
        <h3 className="mt-3 font-semibold text-[var(--text-primary)]">Pick a track to inspect</h3>
        <p className="mx-auto mt-1 max-w-xs text-sm text-[var(--text-muted)]">
          Select any dot to see how Frog Mode reached it and its strongest sampled Last.fm links.
        </p>
      </aside>
    );
  }

  const context = nodeContext(selectedNode);
  const stateLabel = selectedNode.state.replaceAll('_', ' ');
  const safeMaxNeighbors = Math.max(0, maxNeighbors);

  return (
    <aside
      className={`overflow-hidden rounded-2xl border border-[var(--border-soft)] bg-[var(--bg-card)]/95 shadow-2xl shadow-black/20 ${className}`}
      aria-label={`Track lens for ${selectedNode.track} by ${selectedNode.artist}`}
      aria-live="polite"
    >
      <div className="relative border-b border-[var(--border-soft)] p-4 sm:p-5">
        <div
          className="pointer-events-none absolute inset-0 opacity-70"
          style={{
            background: 'radial-gradient(circle at 100% 0%, color-mix(in srgb, var(--accent-primary) 14%, transparent), transparent 48%)',
          }}
          aria-hidden="true"
        />

        <div className="relative flex min-w-0 items-start gap-3.5">
          <div
            className="grid h-14 w-14 shrink-0 place-items-center overflow-hidden rounded-xl border border-[var(--border-soft)] bg-gradient-to-br from-[var(--accent-primary)]/20 to-[var(--accent-secondary)]/10 shadow-lg"
            style={selectedNode.image_url ? {
              backgroundImage: `linear-gradient(rgba(8, 10, 8, 0.06), rgba(8, 10, 8, 0.06)), url(${JSON.stringify(selectedNode.image_url)})`,
              backgroundPosition: 'center',
              backgroundSize: 'cover',
            } : undefined}
            aria-hidden="true"
          >
            {!selectedNode.image_url && (
              <svg
                className="h-6 w-6 text-[var(--text-muted)]"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6Z" />
              </svg>
            )}
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] ${context.badge}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${context.dot}`} aria-hidden="true" />
                {context.label}
              </span>
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-muted)]">
                {stateLabel}
              </span>
            </div>
            <h3 className="mt-2 truncate text-lg font-semibold leading-tight text-[var(--text-primary)]">
              {selectedNode.track}
            </h3>
            <p className="mt-0.5 truncate text-sm text-[var(--text-secondary)]">
              {selectedNode.artist}
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px border-b border-[var(--border-soft)] bg-[var(--border-soft)]">
        <div className="bg-[var(--bg-secondary)]/95 px-4 py-3 sm:px-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            Depth
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="font-mono text-xl text-[var(--text-primary)]">{selectedNode.depth}</span>
            <span className="truncate text-xs text-[var(--text-muted)]">{context.detail}</span>
          </div>
        </div>
        <div className="bg-[var(--bg-secondary)]/95 px-4 py-3 sm:px-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            Degree
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="font-mono text-xl text-[var(--text-primary)]">{graphDetails.degree}</span>
            <span className="truncate text-xs text-[var(--text-muted)]">
              {graphDetails.degree === 1 ? 'connection' : 'connections'}
            </span>
          </div>
        </div>
      </div>

      <div className="p-4 sm:p-5">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">
              Strongest neighbors
            </h4>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              Strongest sampled Last.fm links touching this track
            </p>
          </div>
          {!!graphDetails.neighbors.length && (
            <span className="shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
              top {Math.min(graphDetails.neighbors.length, safeMaxNeighbors)}
            </span>
          )}
        </div>

        {graphDetails.neighbors.length ? (
          <ol className="mt-3 space-y-2">
            {graphDetails.neighbors.map((connection, index) => {
              const neighborContext = nodeContext(connection.node);
              const similarityPercent = Math.round(connection.similarity * 100);
              return (
                <li key={connection.node.id}>
                  <button
                    type="button"
                    onClick={() => onSelectNode(connection.node)}
                    aria-label={`Inspect ${connection.node.track} by ${connection.node.artist}, ${similarityPercent}% similarity`}
                    className="group grid w-full grid-cols-[1.6rem_minmax(0,1fr)_auto] items-center gap-2.5 rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)]/85 px-3 py-2.5 text-left transition hover:-translate-y-0.5 hover:border-[var(--border-strong)] hover:bg-[var(--bg-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]/70"
                  >
                    <span className="font-mono text-[10px] text-[var(--text-muted)]" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="min-w-0">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${neighborContext.dot}`} aria-hidden="true" />
                        <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                          {connection.node.track}
                        </span>
                        {connection.onRoute && (
                          <span className="shrink-0 rounded-full bg-[var(--accent-primary)]/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-[var(--accent-primary)]">
                            route
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block truncate pl-3.5 text-xs text-[var(--text-muted)]">
                        {connection.node.artist}
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="hidden h-1.5 w-14 overflow-hidden rounded-full bg-[var(--bg-elevated)] sm:block" aria-hidden="true">
                        <span
                          className="block h-full rounded-full"
                          style={{
                            width: `${similarityPercent}%`,
                            background: 'var(--gradient-primary)',
                          }}
                        />
                      </span>
                      <span className={`w-10 text-right font-mono text-sm ${similarityTone(connection.similarity)}`}>
                        {similarityPercent}%
                      </span>
                      <svg
                        className="h-3.5 w-3.5 text-[var(--text-muted)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--text-primary)]"
                        viewBox="0 0 20 20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        aria-hidden="true"
                      >
                        <path d="m7 4 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        ) : (
          <div className="mt-3 rounded-xl border border-dashed border-[var(--border-soft)] bg-[var(--bg-secondary)]/60 px-4 py-5 text-center text-sm text-[var(--text-muted)]">
            No visible connections yet. The live search may still discover one.
          </div>
        )}

        {onFindReplacements && (
          <button
            type="button"
            onClick={() => onFindReplacements(selectedNode)}
            disabled={findingReplacements || replacementDisabled}
            aria-busy={findingReplacements}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--accent-tertiary)]/35 bg-[var(--accent-tertiary)]/10 px-4 py-3 text-sm font-semibold text-[var(--accent-tertiary)] transition hover:border-[var(--accent-tertiary)]/65 hover:bg-[var(--accent-tertiary)]/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-tertiary)]/70 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {findingReplacements ? (
              <>
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[var(--accent-tertiary)]/25 border-t-[var(--accent-tertiary)]"
                  aria-hidden="true"
                />
                Finding nearby songs…
              </>
            ) : (
              <>
                <span aria-hidden="true">↝</span>
                Find replacements
              </>
            )}
          </button>
        )}
      </div>
    </aside>
  );
}
