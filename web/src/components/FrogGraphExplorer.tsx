'use client';

import { useMemo, useRef, useState } from 'react';
import {
  FrogAlternative,
  FrogAlternativesResult,
  FrogExploration,
  FrogGraphNode,
  FrogTrack,
  getFrogAlternatives,
} from '@/lib/api';

type GraphMode = 'route' | 'search';

interface FrogGraphExplorerProps {
  exploration?: FrogExploration;
  tracks: FrogTrack[];
  isLoading: boolean;
  canReset: boolean;
  onReplace: (position: number, alternative: FrogAlternative) => void;
  onReset: () => void;
}

interface PositionedNode extends FrogGraphNode {
  x: number;
  y: number;
}

const WIDTH = 1200;
const HEIGHT = 390;

function shortLabel(value: string, max = 22) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash);
}

function edgeColor(similarity: number, kind: 'search' | 'route') {
  if (kind === 'search') return 'var(--border-strong)';
  if (similarity < 0.15) return 'var(--accent-pink)';
  if (similarity < 0.25) return 'var(--accent-tertiary)';
  return 'var(--accent-primary)';
}

function nodeColor(direction: FrogGraphNode['direction'], selected: boolean) {
  if (selected) return 'var(--accent-tertiary)';
  if (direction === 'route') return 'var(--accent-primary)';
  if (direction === 'forward') return 'var(--accent-secondary)';
  return 'var(--accent-pink)';
}

export default function FrogGraphExplorer({
  exploration,
  tracks,
  isLoading,
  canReset,
  onReplace,
  onReset,
}: FrogGraphExplorerProps) {
  const [mode, setMode] = useState<GraphMode>(tracks.length ? 'route' : 'search');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null);
  const [alternatives, setAlternatives] = useState<FrogAlternativesResult | null>(null);
  const [alternativesLoading, setAlternativesLoading] = useState(false);
  const [alternativesError, setAlternativesError] = useState<string | null>(null);
  const [draggedAlternative, setDraggedAlternative] = useState<FrogAlternative | null>(null);
  const [dragPosition, setDragPosition] = useState<{ x: number; y: number } | null>(null);
  const requestSequence = useRef(0);

  const routeNodes = useMemo(() => {
    const graphByPosition = new Map(
      (exploration?.nodes || [])
        .filter((node) => node.route_position !== undefined)
        .map((node) => [node.route_position as number, node]),
    );
    return tracks.map((track, index) => {
      const graphNode = graphByPosition.get(index);
      if (graphNode?.track_id === track.track_id) return graphNode;
      return {
        id: `spotify:${track.track_id}`,
        artist: track.artist,
        track: track.track,
        direction: 'route' as const,
        depth: index,
        state: track.role,
        route_position: index,
        track_id: track.track_id,
        image_url: track.image_url,
      };
    });
  }, [exploration, tracks]);

  const weakestHops = useMemo(
    () => tracks
      .slice(1)
      .map((right, offset) => {
        const edgeIndex = offset;
        const rightIndex = offset + 1;
        const editablePosition = rightIndex === tracks.length - 1
          ? rightIndex - 1
          : rightIndex;
        return {
          edgeIndex,
          editablePosition,
          left: tracks[edgeIndex],
          right,
          score: right.transition_similarity ?? 0,
        };
      })
      .sort((left, right) => left.score - right.score)
      .slice(0, 6),
    [tracks],
  );

  const worstEdgeIndexes = useMemo(
    () => new Set(weakestHops.map((hop) => hop.edgeIndex)),
    [weakestHops],
  );

  const graph = useMemo(() => {
    if (mode === 'route') {
      const positioned = routeNodes.map((node, index) => ({
        ...node,
        x: tracks.length <= 1
          ? WIDTH / 2
          : 52 + (index / (tracks.length - 1)) * (WIDTH - 104),
        y: (HEIGHT / 2) + Math.sin(index * 0.78) * 44,
      }));
      const edges = positioned.slice(1).map((node, index) => ({
        id: `route-ui:${index}`,
        source: positioned[index].id,
        target: node.id,
        similarity: tracks[index + 1]?.transition_similarity ?? 0,
        direction: 'route' as const,
        kind: 'route' as const,
      }));
      return { nodes: positioned, edges };
    }

    const allNodes = exploration?.nodes || [];
    const maxForwardDepth = Math.max(
      1,
      ...allNodes.filter((node) => node.direction === 'forward').map((node) => node.depth),
    );
    const maxBackwardDepth = Math.max(
      1,
      ...allNodes.filter((node) => node.direction === 'backward').map((node) => node.depth),
    );

    const positioned = allNodes.slice(-180).map((node) => {
      if (node.route_position !== undefined && tracks.length > 1) {
        return {
          ...node,
          x: 52 + (node.route_position / (tracks.length - 1)) * (WIDTH - 104),
          y: HEIGHT / 2,
        };
      }
      const ratio = node.direction === 'backward'
        ? node.depth / maxBackwardDepth
        : node.depth / maxForwardDepth;
      const x = node.direction === 'backward'
        ? WIDTH - 65 - ratio * (WIDTH * 0.42)
        : 65 + ratio * (WIDTH * 0.42);
      const y = 32 + (stableHash(node.id) % (HEIGHT - 64));
      return { ...node, x, y };
    });
    const visibleIds = new Set(positioned.map((node) => node.id));
    const edges = (exploration?.edges || [])
      .filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
      .slice(-260);
    return { nodes: positioned, edges };
  }, [exploration, mode, routeNodes, tracks]);

  const positions = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes],
  );
  const selectedNode = graph.nodes.find((node) => node.id === selectedNodeId);

  const loadAlternatives = async (position: number) => {
    if (
      position <= 0
      || position >= tracks.length - 1
      || tracks.some((track) => !track.track_id)
    ) return;

    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setSelectedPosition(position);
    setSelectedNodeId(routeNodes[position]?.id || null);
    setAlternatives(null);
    setAlternativesError(null);
    setAlternativesLoading(true);
    try {
      const result = await getFrogAlternatives(
        tracks.map((track) => track.track_id),
        position,
        tracks[position].transition_similarity,
        tracks[position + 1].transition_similarity,
      );
      if (requestSequence.current === sequence) {
        setAlternatives(result);
      }
    } catch (error) {
      if (requestSequence.current === sequence) {
        setAlternativesError(
          error instanceof Error ? error.message : 'Could not load nearby songs.',
        );
      }
    } finally {
      if (requestSequence.current === sequence) {
        setAlternativesLoading(false);
      }
    }
  };

  const applyAlternative = (alternative: FrogAlternative) => {
    if (selectedPosition === null) return;
    onReplace(selectedPosition, alternative);
    setDraggedAlternative(null);
    setDragPosition(null);
    setAlternatives(null);
    setAlternativesError(null);
    setSelectedNodeId(null);
    setSelectedPosition(null);
  };

  const handleDrop = (event: React.DragEvent<SVGGElement>, position: number) => {
    event.preventDefault();
    const trackId = event.dataTransfer.getData('text/plain');
    const alternative = alternatives?.alternatives.find(
      (item) => item.track.track_id === trackId,
    );
    if (alternative && position === selectedPosition) {
      applyAlternative(alternative);
    }
  };

  return (
    <section
      className="glass-card p-5 space-y-5 animate-fade-in"
      onPointerMove={(event) => {
        if (draggedAlternative) {
          setDragPosition({ x: event.clientX, y: event.clientY });
        }
      }}
      onPointerUp={() => {
        setDraggedAlternative(null);
        setDragPosition(null);
      }}
      onPointerCancel={() => {
        setDraggedAlternative(null);
        setDragPosition(null);
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl" aria-hidden="true">🕸️</span>
            <h2 className="text-xl font-semibold">Frog graph lab</h2>
            {isLoading && (
              <span className="px-2 py-1 rounded-full bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] text-xs">
                live
              </span>
            )}
          </div>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Inspect the search web, find the weakest hops, and replace any bridge.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setMode('route')}
            aria-pressed={mode === 'route'}
            className={`px-3 py-2 rounded-lg text-sm border transition-colors ${
              mode === 'route'
                ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)]/50 text-[var(--accent-primary)]'
                : 'bg-[var(--bg-secondary)] border-[var(--border-soft)] text-[var(--text-secondary)]'
            }`}
          >
            Route
          </button>
          <button
            type="button"
            onClick={() => setMode('search')}
            aria-pressed={mode === 'search'}
            className={`px-3 py-2 rounded-lg text-sm border transition-colors ${
              mode === 'search'
                ? 'bg-[var(--accent-secondary)]/15 border-[var(--accent-secondary)]/50 text-[var(--accent-secondary)]'
                : 'bg-[var(--bg-secondary)] border-[var(--border-soft)] text-[var(--text-secondary)]'
            }`}
          >
            Search web
          </button>
          {canReset && (
            <button
              type="button"
              onClick={onReset}
              className="btn-secondary !py-2 !px-3 text-sm"
            >
              Reset route
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 text-sm">
        <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-soft)] p-3">
          <div className="text-[var(--text-muted)] text-xs">Route</div>
          <div className="font-mono text-lg">{tracks.length || '—'} tracks</div>
        </div>
        <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-soft)] p-3">
          <div className="text-[var(--text-muted)] text-xs">Explored</div>
          <div className="font-mono text-lg">{exploration?.nodes.length || 0} nodes</div>
        </div>
        <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-soft)] p-3">
          <div className="text-[var(--text-muted)] text-xs">Connections</div>
          <div className="font-mono text-lg">{exploration?.edges.length || 0} edges</div>
        </div>
      </div>

      <div className="relative rounded-xl overflow-hidden border border-[var(--border-soft)] bg-[var(--bg-secondary)]/70">
        {graph.nodes.length ? (
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="block w-full min-h-[280px]"
            role="img"
            aria-label={
              mode === 'route'
                ? `Route graph with ${tracks.length} tracks`
                : `Search graph with ${graph.nodes.length} explored tracks`
            }
          >
            <defs>
              <filter id="frog-node-glow" x="-80%" y="-80%" width="260%" height="260%">
                <feGaussianBlur stdDeviation="5" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {graph.edges.map((edge) => {
              const source = positions.get(edge.source);
              const target = positions.get(edge.target);
              if (!source || !target) return null;
              const isWorst = edge.kind === 'route'
                && worstEdgeIndexes.has(source.route_position ?? -1);
              return (
                <line
                  key={edge.id}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={edgeColor(edge.similarity, edge.kind)}
                  strokeWidth={isWorst ? 5 : edge.kind === 'route' ? 3 : 1.25}
                  strokeOpacity={isWorst ? 0.95 : edge.kind === 'route' ? 0.7 : 0.28}
                >
                  <title>
                    {source.track} → {target.track}: {(edge.similarity * 100).toFixed(0)}% signal
                  </title>
                </line>
              );
            })}

            {graph.nodes.map((node: PositionedNode) => {
              const selected = node.id === selectedNodeId;
              const isRoute = node.route_position !== undefined;
              const isEndpoint = node.route_position === 0
                || node.route_position === tracks.length - 1;
              const labelVisible = selected
                || isEndpoint
                || (mode === 'route' && (node.route_position || 0) % 5 === 0);
              const canReplace = isRoute && !isEndpoint && !isLoading;
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x} ${node.y})`}
                  onClick={() => {
                    setSelectedNodeId(node.id);
                    if (canReplace && node.route_position !== undefined) {
                      void loadAlternatives(node.route_position);
                    }
                  }}
                  onDragOver={(event) => {
                    if (node.route_position === selectedPosition) event.preventDefault();
                  }}
                  onDrop={(event) => {
                    if (node.route_position !== undefined) {
                      handleDrop(event, node.route_position);
                    }
                  }}
                  onPointerUp={(event) => {
                    if (
                      draggedAlternative
                      && node.route_position === selectedPosition
                    ) {
                      event.preventDefault();
                      event.stopPropagation();
                      applyAlternative(draggedAlternative);
                    }
                  }}
                  className={canReplace || mode === 'search' ? 'cursor-pointer' : ''}
                  role="button"
                  aria-label={`${node.track} by ${node.artist}${
                    canReplace ? ', click to find replacements' : ''
                  }`}
                >
                  {(selected || (
                    draggedAlternative
                    && node.route_position === selectedPosition
                  )) && (
                    <circle
                      r={18}
                      fill="none"
                      stroke="var(--accent-tertiary)"
                      strokeWidth="2"
                      strokeDasharray="4 4"
                    />
                  )}
                  <circle
                    r={isRoute ? 8 : node.state === 'expanded' ? 5 : 3.5}
                    fill={nodeColor(node.direction, selected)}
                    fillOpacity={node.state === 'discovered' ? 0.55 : 0.95}
                    stroke="var(--bg-primary)"
                    strokeWidth={isRoute ? 2 : 1}
                    filter={selected ? 'url(#frog-node-glow)' : undefined}
                  >
                    <title>{node.track} — {node.artist}</title>
                  </circle>
                  {labelVisible && (
                    <text
                      x={0}
                      y={isRoute ? -14 : -10}
                      textAnchor="middle"
                      fill="var(--text-primary)"
                      fontSize="11"
                      fontWeight="500"
                    >
                      {shortLabel(node.track, 18)}
                    </text>
                  )}
                  {selectedPosition === node.route_position && alternatives && (
                    <text
                      x={0}
                      y={27}
                      textAnchor="middle"
                      fill="var(--accent-tertiary)"
                      fontSize="11"
                    >
                      {draggedAlternative ? 'release to replace' : 'drop replacement here'}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        ) : (
          <div className="h-[300px] grid place-items-center text-sm text-[var(--text-muted)]">
            {isLoading ? 'The explored graph will appear as the search expands.' : 'Generate a route to explore it.'}
          </div>
        )}
      </div>

      {selectedNode && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <div>
            <span className="font-medium">{selectedNode.track}</span>
            <span className="text-[var(--text-muted)]"> — {selectedNode.artist}</span>
          </div>
          {selectedNode.route_position !== undefined
            && selectedNode.route_position > 0
            && selectedNode.route_position < tracks.length - 1
            && (
              <button
                type="button"
                onClick={() => void loadAlternatives(selectedNode.route_position as number)}
                className="btn-secondary !py-2 !px-3 text-sm"
              >
                Find nearby replacements
              </button>
            )}
        </div>
      )}

      {!!tracks.length && (
        <div className="grid lg:grid-cols-[minmax(280px,0.8fr)_minmax(0,2fr)] gap-5">
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium">Worst hops</h3>
              <span className="text-xs text-[var(--text-muted)]">click to repair</span>
            </div>
            <div className="space-y-2">
              {weakestHops.map((hop) => (
                <button
                  key={`${hop.left.track_id}-${hop.right.track_id}`}
                  type="button"
                  onClick={() => void loadAlternatives(hop.editablePosition)}
                  className={`w-full text-left p-3 rounded-xl border transition-colors ${
                    hop.editablePosition === selectedPosition
                      ? 'border-[var(--accent-tertiary)]/60 bg-[var(--accent-tertiary)]/10'
                      : 'border-[var(--border-soft)] bg-[var(--bg-secondary)] hover:border-[var(--border-strong)]'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm min-w-0">
                      <span className="font-medium">{shortLabel(hop.left.track, 18)}</span>
                      <span className="text-[var(--text-muted)]"> → </span>
                      <span className="font-medium">{shortLabel(hop.right.track, 18)}</span>
                    </span>
                    <span className="font-mono text-[var(--accent-tertiary)] text-sm">
                      {(hop.score * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="text-xs text-[var(--text-muted)] mt-1">
                    {hop.left.artist} → {hop.right.artist}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium">
                {selectedPosition === null
                  ? 'Nearby alternatives'
                  : `Replace #${selectedPosition + 1}: ${tracks[selectedPosition]?.track}`}
              </h3>
              {alternatives && (
                <span className="text-xs text-[var(--text-muted)]">
                  drag onto the glowing node
                </span>
              )}
            </div>

            {alternativesLoading ? (
              <div className="h-40 rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)] grid place-items-center">
                <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
                  <div className="w-4 h-4 border-2 border-[var(--accent-primary)]/30 border-t-[var(--accent-primary)] rounded-full animate-spin" />
                  Checking both neighboring songs…
                </div>
              </div>
            ) : alternativesError ? (
              <div className="p-4 rounded-xl border border-red-500/30 bg-red-500/10 text-red-300 text-sm">
                {alternativesError}
              </div>
            ) : alternatives ? (
              alternatives.alternatives.length ? (
                <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  {alternatives.alternatives.map((alternative) => (
                    <article
                      key={alternative.track.track_id}
                      draggable
                      onPointerDown={(event) => {
                        if (
                          event.button !== 0
                          || (event.target as HTMLElement).closest('button')
                        ) {
                          return;
                        }
                        event.preventDefault();
                        setDraggedAlternative(alternative);
                        setDragPosition({ x: event.clientX, y: event.clientY });
                      }}
                      onDragStart={(event) => {
                        event.dataTransfer.setData('text/plain', alternative.track.track_id);
                        event.dataTransfer.effectAllowed = 'move';
                      }}
                      className="rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)] p-3 cursor-grab active:cursor-grabbing hover:border-[var(--accent-primary)]/40 transition-colors"
                      style={{ touchAction: 'none' }}
                    >
                      <div className="flex items-center gap-3">
                        {alternative.track.image_url ? (
                          <img
                            src={alternative.track.image_url}
                            alt=""
                            className="w-11 h-11 rounded-lg object-cover"
                          />
                        ) : (
                          <div className="w-11 h-11 rounded-lg bg-[var(--bg-elevated)] grid place-items-center">
                            ♪
                          </div>
                        )}
                        <div className="min-w-0">
                          <div className="font-medium text-sm truncate">{alternative.track.track}</div>
                          <div className="text-xs text-[var(--text-muted)] truncate">
                            {alternative.track.artist}
                          </div>
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-1 mt-3 text-center">
                        <div>
                          <div className="font-mono text-sm">{(alternative.left_similarity * 100).toFixed(0)}%</div>
                          <div className="text-[10px] text-[var(--text-muted)]">left</div>
                        </div>
                        <div>
                          <div className="font-mono text-sm">{(alternative.right_similarity * 100).toFixed(0)}%</div>
                          <div className="text-[10px] text-[var(--text-muted)]">right</div>
                        </div>
                        <div>
                          <div className={`font-mono text-sm ${
                            alternative.improvement > 0
                              ? 'text-[var(--accent-success)]'
                              : 'text-[var(--text-secondary)]'
                          }`}>
                            {alternative.improvement > 0 ? '+' : ''}
                            {(alternative.improvement * 100).toFixed(0)}%
                          </div>
                          <div className="text-[10px] text-[var(--text-muted)]">change</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => applyAlternative(alternative)}
                        className="w-full mt-3 px-3 py-2 rounded-lg bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] text-xs font-medium hover:bg-[var(--accent-primary)]/20"
                      >
                        Use here
                      </button>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="h-40 rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)] grid place-items-center text-sm text-[var(--text-muted)]">
                  No distinct Spotify track fit both neighbors better.
                </div>
              )
            ) : (
              <div className="h-40 rounded-xl border border-dashed border-[var(--border-strong)] grid place-items-center text-center p-6">
                <div>
                  <div className="text-2xl mb-2" aria-hidden="true">🧩</div>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Pick a weak hop or click any internal route node.
                  </p>
                  <p className="text-xs text-[var(--text-muted)] mt-1">
                    Alternatives must connect to both neighbors and keep the same playlist length.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      {draggedAlternative && dragPosition && (
        <div
          className="fixed z-50 pointer-events-none rounded-xl border border-[var(--accent-primary)]/60 bg-[var(--bg-elevated)]/95 px-3 py-2 shadow-2xl"
          style={{
            left: dragPosition.x,
            top: dragPosition.y,
            transform: 'translate(-50%, calc(-100% - 14px))',
          }}
        >
          <div className="text-sm font-medium">{draggedAlternative.track.track}</div>
          <div className="text-xs text-[var(--text-muted)]">
            {draggedAlternative.track.artist}
          </div>
        </div>
      )}
    </section>
  );
}
