'use client';

import Image from 'next/image';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  FrogAlternative,
  FrogAlternativesResult,
  FrogExploration,
  FrogGraphEdge,
  FrogGraphNode,
  FrogTrack,
  getFrogAlternatives,
} from '@/lib/api';
import {
  frogAlternativesMatchRoute,
  getFrogAlternativeScores,
} from '@/lib/frogAlternative';
import {
  FrogGraphCommunity,
  FrogGraphLayoutNode,
  layoutFrogExploration,
} from '@/lib/frogGraphLayout';
import FrogJourneyRail from '@/components/FrogJourneyRail';
import FrogTrackLens from '@/components/FrogTrackLens';

type GraphMode = 'route' | 'search';

interface FrogGraphExplorerProps {
  exploration?: FrogExploration;
  tracks: FrogTrack[];
  isLoading: boolean;
  canReset: boolean;
  onReplace: (position: number, alternative: FrogAlternative) => void;
  onReset: () => void;
}

interface AtlasNode extends FrogGraphNode {
  x: number;
  y: number;
  role?: FrogGraphLayoutNode['role'];
  degree?: number;
  preview?: boolean;
}

interface AtlasEdge extends FrogGraphEdge {
  preview?: boolean;
}

interface AtlasGraph {
  nodes: AtlasNode[];
  edges: AtlasEdge[];
  communities: FrogGraphCommunity[];
  omittedNodeCount: number;
}

interface SelectionState {
  signature: string;
  nodeId: string;
  position: number | null;
}

interface AlternativesState {
  signature: string;
  position: number;
  loading: boolean;
  result: FrogAlternativesResult | null;
  error: string | null;
}

interface PreviewState {
  signature: string;
  alternative: FrogAlternative;
}

const WIDTH = 1200;
const HEIGHT = 440;

function shortLabel(value: string, max = 22) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function routeTier(similarity: number) {
  if (similarity < 0.12) {
    return {
      color: 'var(--accent-pink)',
      label: 'wide jump',
      dash: '9 7',
    };
  }
  if (similarity < 0.25) {
    return {
      color: 'var(--accent-tertiary)',
      label: 'noticeable hop',
      dash: undefined,
    };
  }
  return {
    color: 'var(--accent-primary)',
    label: 'tiny hop',
    dash: undefined,
  };
}

function searchColor(direction: FrogGraphEdge['direction']) {
  return direction === 'backward'
    ? 'var(--accent-pink)'
    : direction === 'route'
      ? 'var(--accent-primary)'
      : 'var(--accent-secondary)';
}

function routeRows(trackCount: number) {
  if (trackCount <= 24) return 1;
  if (trackCount <= 36) return 2;
  return 3;
}

function layoutRouteRiver(
  routeNodes: readonly FrogGraphNode[],
  tracks: readonly FrogTrack[],
  previewPosition: number | null,
  previewAlternative: FrogAlternative | null,
): AtlasGraph {
  const rows = routeRows(tracks.length);
  const columns = Math.max(1, Math.ceil(tracks.length / rows));
  const positioned = routeNodes.map((sourceNode, index): AtlasNode => {
    const row = Math.floor(index / columns);
    const firstIndex = row * columns;
    const countInRow = Math.min(columns, tracks.length - firstIndex);
    const localIndex = index - firstIndex;
    const displayIndex = row % 2 === 0
      ? localIndex
      : countInRow - localIndex - 1;
    const x = countInRow <= 1
      ? WIDTH / 2
      : 72 + (displayIndex / (countInRow - 1)) * (WIDTH - 144);
    const y = rows === 1
      ? HEIGHT / 2
      : 88 + row * ((HEIGHT - 176) / (rows - 1));
    const isPreview = index === previewPosition && !!previewAlternative;

    return {
      ...sourceNode,
      ...(isPreview ? {
        track: previewAlternative.track.track,
        artist: previewAlternative.track.artist,
        track_id: previewAlternative.track.track_id,
        image_url: previewAlternative.track.image_url,
      } : {}),
      x,
      y,
      role: index === 0
        ? 'start'
        : index === tracks.length - 1
          ? 'end'
          : 'route',
      preview: isPreview,
    };
  });

  const previewScores = previewAlternative
    ? getFrogAlternativeScores(previewAlternative)
    : null;
  const edges = positioned.slice(1).map((node, edgeIndex): AtlasEdge => {
    const touchesPreview = previewPosition !== null
      && (edgeIndex === previewPosition - 1 || edgeIndex === previewPosition);
    const similarity = touchesPreview && previewScores
      ? edgeIndex === previewPosition - 1
        ? previewScores.left
        : previewScores.right
      : tracks[edgeIndex + 1]?.transition_similarity ?? 0;
    return {
      id: `route-atlas:${edgeIndex}`,
      source: positioned[edgeIndex].id,
      target: node.id,
      similarity,
      direction: 'route',
      kind: 'route',
      preview: touchesPreview && !!previewAlternative,
    };
  });

  return {
    nodes: positioned,
    edges,
    communities: [],
    omittedNodeCount: 0,
  };
}

function routePath(source: AtlasNode, target: AtlasNode) {
  if (Math.abs(source.y - target.y) < 2) {
    const bend = Math.min(16, Math.abs(target.x - source.x) * 0.12);
    return `M ${source.x} ${source.y} C ${source.x} ${source.y + bend}, ${target.x} ${target.y + bend}, ${target.x} ${target.y}`;
  }
  const sideX = source.x < WIDTH / 2 ? 38 : WIDTH - 38;
  const middleY = (source.y + target.y) / 2;
  return `M ${source.x} ${source.y} C ${sideX} ${source.y}, ${sideX} ${middleY}, ${sideX} ${middleY} C ${sideX} ${middleY}, ${sideX} ${target.y}, ${target.x} ${target.y}`;
}

function evidenceLabel(alternative: FrogAlternative) {
  const scores = getFrogAlternativeScores(alternative);
  if (!alternative.evidence) return scores.confidenceLabel;
  return `${scores.evidenceCount}/${scores.evidenceTotal} directional links · ${scores.confidenceLabel}`;
}

export default function FrogGraphExplorer({
  exploration,
  tracks,
  isLoading,
  canReset,
  onReplace,
  onReset,
}: FrogGraphExplorerProps) {
  const [pinnedMode, setPinnedMode] = useState<GraphMode | null>(null);
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [alternativesState, setAlternativesState] = useState<AlternativesState | null>(null);
  const [previewState, setPreviewState] = useState<PreviewState | null>(null);
  const [draggedAlternative, setDraggedAlternative] = useState<FrogAlternative | null>(null);
  const [journeyPosition, setJourneyPosition] = useState(0);
  const [journeyPlaying, setJourneyPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const requestSequence = useRef(0);
  const requestAbort = useRef<AbortController | null>(null);
  const routeSignature = tracks.map((track) => track.track_id).join('|');
  const routeSignatureRef = useRef(routeSignature);
  const mode: GraphMode = pinnedMode ?? (tracks.length ? 'route' : 'search');

  useEffect(() => {
    routeSignatureRef.current = routeSignature;
    requestSequence.current += 1;
    requestAbort.current?.abort();
    requestAbort.current = null;
    return () => requestAbort.current?.abort();
  }, [routeSignature]);

  const activeSelection = selection?.signature === routeSignature ? selection : null;
  const selectedPosition = activeSelection?.position ?? null;
  const activeAlternatives = alternativesState?.signature === routeSignature
    && alternativesState.position === selectedPosition
    ? alternativesState
    : null;
  const previewAlternative = previewState?.signature === routeSignature
    ? previewState.alternative
    : null;

  const routeNodes = useMemo(() => {
    const graphByTrackId = new Map(
      (exploration?.nodes || [])
        .filter((node) => node.track_id)
        .map((node) => [node.track_id as string, node]),
    );
    return tracks.map((track, index): FrogGraphNode => {
      const graphNode = graphByTrackId.get(track.track_id);
      if (graphNode) {
        return {
          ...graphNode,
          direction: 'route',
          depth: index,
          state: track.role,
          route_position: index,
          track_id: track.track_id,
          image_url: track.image_url,
        };
      }
      return {
        id: `spotify:${track.track_id}`,
        artist: track.artist,
        track: track.track,
        direction: 'route',
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
        const rightIndex = offset + 1;
        return {
          edgeIndex: offset,
          editablePosition: rightIndex === tracks.length - 1 ? rightIndex - 1 : rightIndex,
          left: tracks[offset],
          right,
          score: right.transition_similarity ?? 0,
        };
      })
      .sort((left, right) => left.score - right.score)
      .slice(0, 6),
    [tracks],
  );

  const weakRankByEdge = useMemo(
    () => new Map(weakestHops.map((hop, index) => [hop.edgeIndex, index + 1])),
    [weakestHops],
  );

  const searchLayout = useMemo(
    () => layoutFrogExploration(exploration, {
      width: WIDTH,
      height: HEIGHT,
      padding: 48,
      maxNodes: 180,
      iterations: isLoading ? 18 : 76,
      stability: isLoading ? 0.9 : 0.72,
    }),
    [exploration, isLoading],
  );

  const graph: AtlasGraph = useMemo(() => {
    if (mode === 'route') {
      return layoutRouteRiver(
        routeNodes,
        tracks,
        selectedPosition,
        previewAlternative,
      );
    }
    return {
      nodes: [...searchLayout.nodes].sort((left, right) => left.zIndex - right.zIndex),
      edges: searchLayout.edges,
      communities: searchLayout.communities,
      omittedNodeCount: searchLayout.omittedNodeCount,
    };
  }, [
    mode,
    previewAlternative,
    routeNodes,
    searchLayout,
    selectedPosition,
    tracks,
  ]);

  const positions = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes],
  );
  const selectedNode = activeSelection
    ? graph.nodes.find((node) => node.id === activeSelection.nodeId) || null
    : null;
  const connectedNodeIds = useMemo(() => {
    if (!selectedNode) return null;
    const connected = new Set([selectedNode.id]);
    graph.edges.forEach((edge) => {
      if (edge.source === selectedNode.id) connected.add(edge.target);
      if (edge.target === selectedNode.id) connected.add(edge.source);
    });
    return connected;
  }, [graph.edges, selectedNode]);

  const selectNode = (node: AtlasNode, findAlternatives = false) => {
    const position = node.route_position !== undefined ? node.route_position : null;
    setSelection({
      signature: routeSignature,
      nodeId: node.id,
      position,
    });
    if (position !== null) setJourneyPosition(position);
    if (
      findAlternatives
      && position !== null
      && position > 0
      && position < tracks.length - 1
    ) {
      void loadAlternatives(position, node.id);
    }
  };

  const loadAlternatives = async (position: number, nodeId?: string) => {
    if (
      position <= 0
      || position >= tracks.length - 1
      || tracks.some((track) => !track.track_id)
    ) return;

    requestAbort.current?.abort();
    const controller = new AbortController();
    requestAbort.current = controller;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    const signature = routeSignature;
    const snapshot = tracks.map((track) => ({ ...track }));
    setSelection({
      signature,
      nodeId: nodeId || routeNodes[position]?.id || `spotify:${tracks[position].track_id}`,
      position,
    });
    setPreviewState(null);
    setAlternativesState({
      signature,
      position,
      loading: true,
      result: null,
      error: null,
    });

    try {
      const result = await getFrogAlternatives(
        snapshot.map((track) => track.track_id),
        position,
        snapshot[position].transition_similarity,
        snapshot[position + 1].transition_similarity,
        8,
        controller.signal,
      );
      if (
        requestSequence.current !== sequence
        || routeSignatureRef.current !== signature
      ) return;
      if (!frogAlternativesMatchRoute(result, snapshot, position)) {
        setAlternativesState({
          signature,
          position,
          loading: false,
          result: null,
          error: 'That route changed while the repair was loading. Pick the hop again.',
        });
        return;
      }
      setAlternativesState({
        signature,
        position,
        loading: false,
        result,
        error: null,
      });
    } catch (error) {
      if (
        controller.signal.aborted
        || requestSequence.current !== sequence
        || routeSignatureRef.current !== signature
      ) return;
      setAlternativesState({
        signature,
        position,
        loading: false,
        result: null,
        error: error instanceof Error ? error.message : 'Could not load nearby songs.',
      });
    }
  };

  const applyAlternative = (alternative: FrogAlternative) => {
    if (
      selectedPosition === null
      || !activeAlternatives?.result
      || !frogAlternativesMatchRoute(activeAlternatives.result, tracks, selectedPosition)
    ) {
      if (selectedPosition !== null) {
        setAlternativesState({
          signature: routeSignature,
          position: selectedPosition,
          loading: false,
          result: null,
          error: 'This repair belongs to an older route. Reload the hop before applying it.',
        });
      }
      return;
    }
    onReplace(selectedPosition, alternative);
    setDraggedAlternative(null);
    setPreviewState(null);
  };

  const handleDrop = (event: React.DragEvent<SVGGElement>, position: number) => {
    event.preventDefault();
    const trackId = event.dataTransfer.getData('text/plain');
    const alternative = activeAlternatives?.result?.alternatives.find(
      (item) => item.track.track_id === trackId,
    );
    if (alternative && position === selectedPosition) applyAlternative(alternative);
  };

  const handleJourneyPosition = useCallback((position: number) => {
    const clamped = Math.max(0, Math.min(position, tracks.length - 1));
    setJourneyPosition(clamped);
    setPreviewState(null);
    const node = routeNodes[clamped];
    if (node) {
      setSelection({
        signature: routeSignature,
        nodeId: node.id,
        position: clamped,
      });
    }
  }, [routeNodes, routeSignature, tracks.length]);

  useEffect(() => {
    if (!journeyPlaying || tracks.length < 2) return;
    const nextPosition = Math.min(journeyPosition + 1, tracks.length - 1);
    const timer = window.setTimeout(() => {
      handleJourneyPosition(nextPosition);
      if (nextPosition >= tracks.length - 1) setJourneyPlaying(false);
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [
    handleJourneyPosition,
    journeyPlaying,
    journeyPosition,
    tracks.length,
  ]);

  const caption = mode === 'route'
    ? 'Order is exact. Lime marks at least 25%, amber 12–24%, and dashed pink under 12% on the observed Last.fm signal. Spacing is for readability.'
    : 'Horizontal progress follows weighted graph distance from the endpoints; stronger sampled Last.fm links pull dots closer. Vertical placement separates communities. This trace is not audio-feature space.';

  return (
    <section className="glass-card p-4 sm:p-5 space-y-5 animate-fade-in">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-2xl" aria-hidden="true">🐸</span>
            <h2 className="text-xl font-semibold">Frog Atlas</h2>
            {isLoading && (
              <span className="rounded-full border border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/10 px-2 py-1 text-xs text-[var(--accent-primary)]">
                mapping live
              </span>
            )}
          </div>
          <p className="mt-1 max-w-2xl text-sm text-[var(--text-secondary)]">
            Walk the finished route, inspect the sampled search, and preview conservative repairs before you splice them in.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)] p-1">
            <button
              type="button"
              onClick={() => {
                setPinnedMode('route');
              }}
              aria-pressed={mode === 'route'}
              className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                mode === 'route'
                  ? 'bg-[var(--accent-primary)]/15 text-[var(--accent-primary)]'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              Route River
            </button>
            <button
              type="button"
              onClick={() => {
                setPinnedMode('search');
              }}
              aria-pressed={mode === 'search'}
              className={`rounded-lg px-3 py-2 text-sm transition-colors ${
                mode === 'search'
                  ? 'bg-[var(--accent-secondary)]/15 text-[var(--accent-secondary)]'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              Search Currents
            </button>
          </div>
          {canReset && (
            <button type="button" onClick={onReset} className="btn-secondary !px-3 !py-2 text-sm">
              Reset edits
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)]/65 px-4 py-3 text-sm">
        <span className="font-mono text-[var(--text-primary)]">
          {tracks.length || '—'}-track route
        </span>
        <span className="text-[var(--border-strong)]" aria-hidden="true">·</span>
        <span className="font-mono text-[var(--accent-secondary)]">
          {exploration?.total_nodes ?? exploration?.nodes.length ?? 0} tracks mapped
        </span>
        <span className="text-[var(--border-strong)]" aria-hidden="true">·</span>
        <span className="font-mono text-[var(--accent-pink)]">
          {exploration?.total_edges ?? exploration?.edges.length ?? 0} sampled links
        </span>
        {graph.omittedNodeCount > 0 && (
          <>
            <span className="text-[var(--border-strong)]" aria-hidden="true">·</span>
            <span className="text-xs text-[var(--text-secondary)]">
              overview hides {graph.omittedNodeCount} lower-priority dots
            </span>
          </>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2.25fr)_minmax(300px,0.75fr)]">
        <div className="min-w-0 space-y-3">
          <div className="relative overflow-hidden rounded-2xl border border-[var(--border-soft)] bg-[var(--bg-secondary)]/70">
            <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-lg border border-[var(--border-soft)] bg-[var(--bg-primary)]/90 p-1 shadow-lg">
              <button
                type="button"
                onClick={() => setZoom((value) => Math.max(1, value - 0.2))}
                disabled={zoom <= 1}
                className="grid h-8 w-8 place-items-center rounded text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="Zoom out"
              >
                −
              </button>
              <button
                type="button"
                onClick={() => setZoom(1)}
                className="h-8 rounded px-2 font-mono text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text-primary)]"
                aria-label="Fit graph"
              >
                fit
              </button>
              <button
                type="button"
                onClick={() => setZoom((value) => Math.min(2, value + 0.2))}
                disabled={zoom >= 2}
                className="grid h-8 w-8 place-items-center rounded text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="Zoom in"
              >
                +
              </button>
            </div>

            {graph.nodes.length ? (
              <div className="overflow-x-auto">
                <svg
                  viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                  className="block min-h-[310px] w-full"
                  style={{ minWidth: zoom <= 1 ? '100%' : `${Math.round(720 * zoom)}px` }}
                  role="group"
                  aria-labelledby="frog-atlas-title frog-atlas-description"
                >
                  <title id="frog-atlas-title">
                    {mode === 'route' ? 'Frog Route River' : 'Frog Search Currents'}
                  </title>
                  <desc id="frog-atlas-description">{caption}</desc>
                  <defs>
                    <filter id="frog-selected-glow" x="-90%" y="-90%" width="280%" height="280%">
                      <feGaussianBlur stdDeviation="5" result="blur" />
                      <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  </defs>

                  {mode === 'search' && graph.communities
                    .filter((community) => community.size >= 3)
                    .map((community) => (
                      <circle
                        key={community.id}
                        cx={community.centerX}
                        cy={community.centerY}
                        r={22 + Math.sqrt(community.size) * 12}
                        fill="var(--accent-secondary)"
                        fillOpacity="0.025"
                        stroke="var(--accent-secondary)"
                        strokeOpacity="0.08"
                        strokeDasharray="4 9"
                        aria-hidden="true"
                      />
                    ))}

                  {graph.edges.map((edge) => {
                    const source = positions.get(edge.source);
                    const target = positions.get(edge.target);
                    if (!source || !target) return null;
                    const tier = routeTier(edge.similarity);
                    const isRoute = edge.kind === 'route';
                    const focusDimmed = !!connectedNodeIds
                      && !(connectedNodeIds.has(edge.source) && connectedNodeIds.has(edge.target));
                    const strokeOpacity = focusDimmed
                      ? 0.07
                      : isRoute
                        ? edge.preview ? 1 : 0.82
                        : 0.22 + edge.similarity * 0.5;
                    const path = isRoute
                      ? routePath(source, target)
                      : `M ${source.x} ${source.y} L ${target.x} ${target.y}`;
                    const edgeIndex = source.route_position ?? -1;
                    const weakRank = isRoute ? weakRankByEdge.get(edgeIndex) : undefined;
                    const midpointX = (source.x + target.x) / 2;
                    const midpointY = (source.y + target.y) / 2;

                    return (
                      <g key={edge.id}>
                        <path
                          d={path}
                          fill="none"
                          stroke={isRoute ? tier.color : searchColor(edge.direction)}
                          strokeWidth={isRoute ? 3 : 0.85 + edge.similarity * 2.1}
                          strokeOpacity={strokeOpacity}
                          strokeDasharray={isRoute ? tier.dash : undefined}
                          vectorEffect="non-scaling-stroke"
                        >
                          <title>
                            {source.track} to {target.track}: {Math.round(edge.similarity * 100)}% observed signal
                          </title>
                        </path>
                        {weakRank && !focusDimmed && (
                          <g transform={`translate(${midpointX} ${midpointY})`} aria-hidden="true">
                            <circle
                              r="10"
                              fill="var(--bg-primary)"
                              stroke={tier.color}
                              strokeWidth="1.5"
                            />
                            <text
                              y="3.5"
                              textAnchor="middle"
                              fill={tier.color}
                              fontSize="9"
                              fontWeight="700"
                            >
                              {weakRank}
                            </text>
                          </g>
                        )}
                      </g>
                    );
                  })}

                  {graph.nodes.map((node) => {
                    const selected = node.id === activeSelection?.nodeId;
                    const isRoute = node.route_position !== undefined || node.direction === 'route';
                    const isEndpoint = node.role === 'start'
                      || node.role === 'end'
                      || node.route_position === 0
                      || node.route_position === tracks.length - 1;
                    const canReplace = isRoute
                      && !isEndpoint
                      && !isLoading
                      && node.route_position !== undefined;
                    const dimmed = !!connectedNodeIds && !connectedNodeIds.has(node.id);
                    const labelVisible = selected
                      || isEndpoint
                      || node.preview
                      || (mode === 'route' && (node.route_position || 0) % 5 === 0);
                    const nodeColor = node.preview
                      ? 'var(--accent-tertiary)'
                      : isRoute
                        ? 'var(--accent-primary)'
                        : node.direction === 'forward'
                          ? 'var(--accent-secondary)'
                          : 'var(--accent-pink)';
                    const nodeLabel = `${node.track} by ${node.artist}${
                      canReplace
                        ? ', press Enter to inspect; repairs are available in the track lens'
                        : ', press Enter to inspect'
                    }`;
                    const keyboardActionable = isRoute
                      || isEndpoint
                      || node.state === 'meeting';
                    const activate = () => selectNode(node);

                    return (
                      <g
                        key={node.id}
                        transform={`translate(${node.x} ${node.y})`}
                        opacity={dimmed ? 0.16 : 1}
                        onClick={activate}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            activate();
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
                        className="group cursor-pointer focus-visible:outline-none"
                        role="button"
                        tabIndex={keyboardActionable && !dimmed ? 0 : -1}
                        aria-label={nodeLabel}
                      >
                        <circle r="22" fill="transparent" />
                        <circle
                          r={isRoute ? 20 : 16}
                          fill="none"
                          stroke="var(--text-primary)"
                          strokeWidth="2"
                          strokeDasharray="3 3"
                          className="pointer-events-none opacity-0 group-focus-visible:opacity-100"
                          aria-hidden="true"
                        />
                        {(selected || (
                          draggedAlternative
                          && node.route_position === selectedPosition
                        )) && (
                          <circle
                            r={isRoute ? 18 : 14}
                            fill="none"
                            stroke="var(--accent-tertiary)"
                            strokeWidth="2"
                            strokeDasharray={draggedAlternative ? '5 4' : undefined}
                            filter="url(#frog-selected-glow)"
                          />
                        )}
                        {isEndpoint && (
                          <circle
                            r={isRoute ? 15 : 10}
                            fill="none"
                            stroke={nodeColor}
                            strokeWidth="1.5"
                            strokeOpacity="0.62"
                          />
                        )}
                        {isRoute ? (
                          <>
                            <rect
                              x="-10"
                              y="-10"
                              width="20"
                              height="20"
                              rx="6"
                              fill={nodeColor}
                              fillOpacity={node.preview ? 0.95 : 0.88}
                              stroke="var(--bg-primary)"
                              strokeWidth="2"
                            />
                            <text
                              y="3.5"
                              textAnchor="middle"
                              fill="var(--bg-primary)"
                              fontSize="8.5"
                              fontWeight="800"
                              aria-hidden="true"
                            >
                              {(node.route_position ?? 0) + 1}
                            </text>
                          </>
                        ) : node.direction === 'backward' ? (
                          <rect
                            x={node.state === 'expanded' ? -5 : -4}
                            y={node.state === 'expanded' ? -5 : -4}
                            width={node.state === 'expanded' ? 10 : 8}
                            height={node.state === 'expanded' ? 10 : 8}
                            transform="rotate(45)"
                            fill={node.state === 'discovered' ? 'var(--bg-secondary)' : nodeColor}
                            stroke={nodeColor}
                            strokeWidth="1.5"
                          />
                        ) : (
                          <circle
                            r={node.state === 'expanded' ? 5 : 4}
                            fill={node.state === 'discovered' ? 'var(--bg-secondary)' : nodeColor}
                            stroke={nodeColor}
                            strokeWidth="1.5"
                          />
                        )}
                        {labelVisible && (
                          <text
                            x="0"
                            y={isRoute ? -17 : -11}
                            textAnchor="middle"
                            fill="var(--text-primary)"
                            fontSize="11"
                            fontWeight="600"
                            paintOrder="stroke"
                            stroke="var(--bg-secondary)"
                            strokeWidth="3"
                          >
                            {node.preview ? `preview: ${shortLabel(node.track, 18)}` : shortLabel(node.track, 18)}
                          </text>
                        )}
                        <title>{node.track} — {node.artist}</title>
                      </g>
                    );
                  })}
                </svg>
              </div>
            ) : (
              <div className="grid h-[330px] place-items-center px-6 text-center text-sm text-[var(--text-secondary)]">
                {isLoading
                  ? 'Search Currents will grow here as both frontiers explore.'
                  : 'Generate a route to open the atlas.'}
              </div>
            )}
          </div>

          <div
            id="frog-atlas-caption"
            className="flex flex-wrap items-start justify-between gap-3 text-xs text-[var(--text-secondary)]"
          >
            <p className="max-w-3xl">{caption}</p>
            <div className="flex flex-wrap items-center gap-3" aria-label="Atlas legend">
              {mode === 'route' ? (
                <>
                  <span><span className="text-[var(--accent-primary)]">━━</span> tiny</span>
                  <span><span className="text-[var(--accent-tertiary)]">━━</span> noticeable</span>
                  <span><span className="text-[var(--accent-pink)]">┄┄</span> wide</span>
                </>
              ) : (
                <>
                  <span><span className="text-[var(--accent-secondary)]">●</span> from start</span>
                  <span><span className="text-[var(--accent-pink)]">◆</span> from end</span>
                  <span><span className="text-[var(--accent-primary)]">■</span> final route</span>
                </>
              )}
            </div>
          </div>
        </div>

        <FrogTrackLens
          selectedNode={selectedNode}
          nodes={graph.nodes}
          edges={graph.edges}
          onSelectNode={(node) => selectNode(node as AtlasNode)}
          onFindReplacements={(node) => {
            if (node.route_position !== undefined) {
              void loadAlternatives(node.route_position, node.id);
            }
          }}
          findingReplacements={!!activeAlternatives?.loading}
          replacementDisabled={
            isLoading
            || selectedNode?.route_position === undefined
            || selectedNode.route_position <= 0
            || selectedNode.route_position >= tracks.length - 1
          }
        />
      </div>

      {!!tracks.length && (
        <FrogJourneyRail
          tracks={tracks}
          activePosition={Math.min(journeyPosition, tracks.length - 1)}
          playing={journeyPlaying}
          onPositionChange={handleJourneyPosition}
          onPlayingChange={setJourneyPlaying}
        />
      )}

      {!!tracks.length && (
        <div className="grid gap-5 lg:grid-cols-[minmax(280px,0.72fr)_minmax(0,2fr)]">
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-medium">Repair queue</h3>
              <span className="text-xs text-[var(--text-secondary)]">weakest first</span>
            </div>
            <div className="space-y-2">
              {weakestHops.map((hop, index) => {
                const tier = routeTier(hop.score);
                return (
                  <button
                    key={`${hop.left.track_id}-${hop.right.track_id}`}
                    type="button"
                    onClick={() => {
                      setPinnedMode('route');
                      void loadAlternatives(
                        hop.editablePosition,
                        routeNodes[hop.editablePosition]?.id,
                      );
                    }}
                    className={`w-full rounded-xl border p-3 text-left transition-colors ${
                      hop.editablePosition === selectedPosition
                        ? 'border-[var(--accent-tertiary)]/65 bg-[var(--accent-tertiary)]/10'
                        : 'border-[var(--border-soft)] bg-[var(--bg-secondary)] hover:border-[var(--border-strong)]'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full border font-mono text-xs"
                        style={{ borderColor: tier.color, color: tier.color }}
                      >
                        {index + 1}
                      </span>
                      <span className="min-w-0 flex-1 text-sm">
                        <span className="font-medium">{shortLabel(hop.left.track, 17)}</span>
                        <span className="text-[var(--text-muted)]"> → </span>
                        <span className="font-medium">{shortLabel(hop.right.track, 17)}</span>
                        <span className="mt-1 block text-xs text-[var(--text-secondary)]">
                          {tier.label}
                        </span>
                      </span>
                      <span className="font-mono text-sm" style={{ color: tier.color }}>
                        {Math.round(hop.score * 100)}%
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="font-medium">
                  {selectedPosition === null
                    ? 'Splice bench'
                    : `Replace #${selectedPosition + 1}: ${tracks[selectedPosition]?.track}`}
                </h3>
                <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
                  Hover to preview both conservative sides. Click or drag to commit.
                </p>
              </div>
              {activeAlternatives?.result && (
                <span className="rounded-full border border-[var(--border-soft)] px-2 py-1 text-xs text-[var(--text-secondary)]">
                  preserves {tracks.length} tracks
                </span>
              )}
            </div>

            {activeAlternatives?.loading ? (
              <div
                role="status"
                aria-live="polite"
                className="grid h-44 place-items-center rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)]"
              >
                <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--accent-primary)]/30 border-t-[var(--accent-primary)]" />
                  Checking both directions around both neighbors…
                </div>
              </div>
            ) : activeAlternatives?.error ? (
              <div
                role="alert"
                className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300"
              >
                {activeAlternatives.error}
              </div>
            ) : activeAlternatives?.result ? (
              activeAlternatives.result.alternatives.length ? (
                <div
                  aria-live="polite"
                  aria-label={`${activeAlternatives.result.alternatives.length} splice candidates`}
                  className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
                >
                  {activeAlternatives.result.alternatives.map((alternative) => {
                    const scores = getFrogAlternativeScores(alternative);
                    const gainPoints = scores.improvement * 100;
                    const isPreviewed = previewAlternative?.track.track_id
                      === alternative.track.track_id;
                    return (
                      <article
                        key={alternative.track.track_id}
                        draggable
                        onPointerEnter={() => setPreviewState({
                          signature: routeSignature,
                          alternative,
                        })}
                        onPointerLeave={() => {
                          if (!draggedAlternative) setPreviewState(null);
                        }}
                        onDragStart={(event) => {
                          event.dataTransfer.setData('text/plain', alternative.track.track_id);
                          event.dataTransfer.effectAllowed = 'move';
                          setDraggedAlternative(alternative);
                          setPreviewState({ signature: routeSignature, alternative });
                        }}
                        onDragEnd={() => {
                          setDraggedAlternative(null);
                          setPreviewState(null);
                        }}
                        className={`rounded-xl border bg-[var(--bg-secondary)] p-3 transition ${
                          isPreviewed
                            ? 'border-[var(--accent-tertiary)]/70 shadow-lg shadow-[var(--accent-tertiary)]/5'
                            : 'border-[var(--border-soft)] hover:border-[var(--accent-primary)]/40'
                        }`}
                        style={{ touchAction: 'pan-y' }}
                      >
                        <div className="flex items-center gap-3">
                          {alternative.track.image_url ? (
                            <Image
                              src={alternative.track.image_url}
                              alt=""
                              width={48}
                              height={48}
                              unoptimized
                              className="h-12 w-12 rounded-lg object-cover"
                            />
                          ) : (
                            <div className="grid h-12 w-12 place-items-center rounded-lg bg-[var(--bg-elevated)] text-[var(--text-muted)]">
                              ♪
                            </div>
                          )}
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium">{alternative.track.track}</div>
                            <div className="truncate text-xs text-[var(--text-secondary)]">
                              {alternative.track.artist}
                            </div>
                          </div>
                        </div>

                        <div className="mt-3 rounded-lg border border-[var(--border-soft)] bg-[var(--bg-primary)]/45 p-3">
                          <div className="flex items-end justify-between gap-3">
                            <div>
                              <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                                worst side
                              </div>
                              <div className="font-mono text-xl text-[var(--text-primary)]">
                                {Math.round(scores.bottleneck * 100)}%
                              </div>
                            </div>
                            <div className={`font-mono text-sm ${
                              gainPoints > 0
                                ? 'text-[var(--accent-success)]'
                                : gainPoints < 0
                                  ? 'text-[var(--accent-pink)]'
                                  : 'text-[var(--text-secondary)]'
                            }`}>
                              {gainPoints > 0 ? '+' : ''}
                              {gainPoints.toFixed(0)} pts
                            </div>
                          </div>
                          <div className="mt-2 flex items-center justify-between font-mono text-xs text-[var(--text-secondary)]">
                            <span>left {Math.round(scores.left * 100)}</span>
                            <span aria-hidden="true">·</span>
                            <span>right {Math.round(scores.right * 100)}</span>
                          </div>
                        </div>

                        <div className="mt-2 text-[10px] text-[var(--text-secondary)]">
                          {evidenceLabel(alternative)}
                        </div>
                        {alternative.reason && (
                          <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-[var(--text-muted)]">
                            {alternative.reason}
                          </p>
                        )}
                        <button
                          type="button"
                          onFocus={() => setPreviewState({
                            signature: routeSignature,
                            alternative,
                          })}
                          onBlur={() => {
                            if (!draggedAlternative) setPreviewState(null);
                          }}
                          onClick={() => applyAlternative(alternative)}
                          className="mt-3 w-full rounded-lg bg-[var(--accent-primary)]/10 px-3 py-2 text-xs font-medium text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
                        >
                          Use this splice
                        </button>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="grid h-44 place-items-center rounded-xl border border-[var(--border-soft)] bg-[var(--bg-secondary)] px-6 text-center text-sm text-[var(--text-secondary)]">
                  No distinct Spotify track had usable sampled links to both neighbors.
                </div>
              )
            ) : (
              <div className="grid h-44 place-items-center rounded-xl border border-dashed border-[var(--border-strong)] p-6 text-center">
                <div>
                  <div className="mb-2 text-2xl" aria-hidden="true">🧩</div>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Pick a numbered rough hop or an internal stepping stone.
                  </p>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    Every splice must connect to both neighbors and preserve the exact route length.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
