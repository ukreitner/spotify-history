'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import axios from 'axios';
import {
  AnchorTrack,
  FlowMode,
  VibePlaylistRequest,
  VibePlaylistResult,
  FrogAlternative,
  FrogExploration,
  FrogPlaylistResult,
  FrogProgressEvent,
  FrogTrack,
  createPlaylist,
  generateVibePlaylist,
  generateFrogPlaylistStreaming,
} from '@/lib/api';
import TrackCard from '@/components/TrackCard';
import AnchorTrackPicker from '@/components/AnchorTrackPicker';
import FrogGraphExplorer from '@/components/FrogGraphExplorer';
import { getFrogAlternativeScores } from '@/lib/frogAlternative';

type PlaylistMode = 'vibe' | 'frog';

interface VibeRecipe {
  anchorTrackIds: string[];
  trackCount: number;
  discoveryRatio: number;
  flowMode: FlowMode;
  excludeArtists: string[];
  coherenceThreshold: number;
  maxPerAnchorArtist: number;
  maxPerSimilarArtist: number;
}

interface VibeRunVariables {
  token: number;
  recipe: VibeRecipe;
  request: VibePlaylistRequest;
}

interface CreatePlaylistVariables {
  name: string;
  trackIds: string[];
  mode: PlaylistMode;
}

function vibeRecipeKey(recipe: VibeRecipe): string {
  return JSON.stringify(recipe);
}

function vibeRequestFromRecipe(recipe: VibeRecipe): VibePlaylistRequest {
  return {
    anchor_track_ids: recipe.anchorTrackIds,
    track_count: recipe.trackCount,
    discovery_ratio: recipe.discoveryRatio,
    flow_mode: recipe.flowMode,
    exclude_artists: recipe.excludeArtists,
    coherence_threshold: recipe.coherenceThreshold,
    max_per_anchor_artist: recipe.maxPerAnchorArtist,
    max_per_similar_artist: recipe.maxPerSimilarArtist,
  };
}

const EXPLORATION_NODE_LIMIT = 600;
const EXPLORATION_EDGE_LIMIT = 1200;

interface FrogRunInputs {
  startTrackId: string | null;
  endTrackId: string | null;
  trackCount: number;
}

interface ActiveFrogRun extends FrogRunInputs {
  startTrackId: string;
  endTrackId: string;
  token: number;
  cancel: () => void;
}

function frogRunMatchesInputs(
  run: FrogRunInputs,
  inputs: FrogRunInputs,
): boolean {
  return run.startTrackId === inputs.startTrackId
    && run.endTrackId === inputs.endTrackId
    && run.trackCount === inputs.trackCount;
}

function mergeExploration(
  current: FrogExploration,
  incoming?: FrogExploration,
): FrogExploration {
  if (!incoming) return current;
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  const incomingNodeIds = new Set(incoming.nodes.map((node) => node.id));
  const incomingEdgeIds = new Set(incoming.edges.map((edge) => edge.id));
  incoming.nodes.forEach((node) => nodes.set(node.id, node));
  incoming.edges.forEach((edge) => edges.set(edge.id, edge));

  const retainedNodes = Array.from(nodes.values())
    .sort((left, right) => {
      const priority = (node: FrogExploration['nodes'][number]) => {
        if (node.direction === 'route' || node.route_position !== undefined) return 0;
        if (node.state === 'start' || node.state === 'end') return 1;
        if (node.state === 'meeting') return 2;
        if (incomingNodeIds.has(node.id) && node.state === 'expanded') return 3;
        if (incomingNodeIds.has(node.id)) return 4;
        if (node.state === 'expanded') return 5;
        return 6;
      };
      return priority(left) - priority(right)
        || left.depth - right.depth
        || left.id.localeCompare(right.id);
    })
    .slice(0, incoming.node_limit ?? EXPLORATION_NODE_LIMIT);
  const retainedNodeIds = new Set(retainedNodes.map((node) => node.id));
  const retainedEdges = Array.from(edges.values())
    .filter((edge) => (
      retainedNodeIds.has(edge.source)
      && retainedNodeIds.has(edge.target)
    ))
    .sort((left, right) => {
      const priority = (edge: FrogExploration['edges'][number]) => {
        if (edge.kind === 'route') return 0;
        if (incomingEdgeIds.has(edge.id)) return 1;
        return 2;
      };
      return priority(left) - priority(right)
        || right.similarity - left.similarity
        || left.id.localeCompare(right.id);
    })
    .slice(0, incoming.edge_limit ?? EXPLORATION_EDGE_LIMIT);
  const wasTruncated = retainedNodes.length < nodes.size
    || retainedEdges.length < edges.size;

  return {
    ...current,
    ...incoming,
    nodes: retainedNodes,
    edges: retainedEdges,
    total_nodes: Math.max(
      current.total_nodes ?? current.nodes.length,
      incoming.total_nodes ?? incoming.nodes.length,
    ),
    total_edges: Math.max(
      current.total_edges ?? current.edges.length,
      incoming.total_edges ?? incoming.edges.length,
    ),
    retained_nodes: retainedNodes.length,
    retained_edges: retainedEdges.length,
    truncated: current.truncated || incoming.truncated || wasTruncated,
  };
}

function explorationWithRoute(
  exploration: FrogExploration,
  tracks: FrogTrack[],
): FrogExploration {
  const graphNodeByTrackId = new Map(
    exploration.nodes
      .filter((node) => node.track_id)
      .map((node) => [node.track_id as string, node]),
  );
  const searchNodes = exploration.nodes.filter((node) => node.direction !== 'route');
  const searchEdges = exploration.edges.filter((edge) => edge.kind !== 'route');
  const routeNodes = tracks.map((track, position) => {
    const existingNode = graphNodeByTrackId.get(track.track_id);
    return {
      id: existingNode?.id || `spotify:${track.track_id}`,
      artist: track.artist,
      track: track.track,
      direction: 'route' as const,
      depth: position,
      state: track.role,
      route_position: position,
      track_id: track.track_id,
      image_url: track.image_url,
    };
  });
  const routeEdges = tracks.slice(1).map((track, offset) => ({
    id: `route:${routeNodes[offset].id}>${routeNodes[offset + 1].id}`,
    source: routeNodes[offset].id,
    target: routeNodes[offset + 1].id,
    similarity: track.transition_similarity ?? 0,
    direction: 'route' as const,
    kind: 'route' as const,
  }));
  return {
    ...exploration,
    nodes: [...searchNodes, ...routeNodes],
    edges: [...searchEdges, ...routeEdges],
  };
}

export default function PlaylistsPage() {
  const [mode, setMode] = useState<PlaylistMode>('vibe');

  // Vibe mode state
  const [anchors, setAnchors] = useState<AnchorTrack[]>([]);
  const [playlistName, setPlaylistName] = useState('My Vibe Mix');
  const [trackCount, setTrackCount] = useState(30);
  const [discoveryRatio, setDiscoveryRatio] = useState(50);
  const [flowMode, setFlowMode] = useState<FlowMode>('smooth');
  const [excludeArtists, setExcludeArtists] = useState<string[]>([]);
  const [excludeInput, setExcludeInput] = useState('');

  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [coherenceThreshold, setCoherenceThreshold] = useState(50);
  const [maxPerAnchorArtist, setMaxPerAnchorArtist] = useState(3);
  const [maxPerSimilarArtist, setMaxPerSimilarArtist] = useState(2);

  // Frog mode state
  const [startTrack, setStartTrack] = useState<AnchorTrack | null>(null);
  const [endTrack, setEndTrack] = useState<AnchorTrack | null>(null);
  const [frogTrackCount, setFrogTrackCount] = useState(20);
  const [frogPlaylistName, setFrogPlaylistName] = useState('Frog Transition');

  const [vibeResult, setVibeResult] = useState<VibePlaylistResult | null>(null);
  const [vibeResultRecipe, setVibeResultRecipe] = useState<VibeRecipe | null>(null);
  const [vibeResultValid, setVibeResultValid] = useState(false);
  const [vibeLoading, setVibeLoading] = useState(false);
  const [vibeError, setVibeError] = useState<unknown>(null);
  const [frogResult, setFrogResult] = useState<FrogPlaylistResult | null>(null);

  // Frog streaming state
  const [frogProgress, setFrogProgress] = useState<FrogProgressEvent | null>(null);
  const [frogLoading, setFrogLoading] = useState(false);
  const [frogError, setFrogError] = useState<string | null>(null);
  const [showFrogExplorer, setShowFrogExplorer] = useState(false);
  const [frogExplorerRunKey, setFrogExplorerRunKey] = useState(0);
  const [frogExploration, setFrogExploration] = useState<FrogExploration>({
    nodes: [],
    edges: [],
  });
  const [frogOriginalResult, setFrogOriginalResult] = useState<FrogPlaylistResult | null>(null);
  const frogRunTokenRef = useRef(0);
  const vibeRunTokenRef = useRef(0);
  const activeVibeRunRef = useRef<{ token: number; recipeKey: string } | null>(null);
  const createInFlightRef = useRef(false);
  const vibePageMountedRef = useRef(true);
  const activeFrogRunRef = useRef<ActiveFrogRun | null>(null);
  const frogInputsRef = useRef<FrogRunInputs>({
    startTrackId: null,
    endTrackId: null,
    trackCount: 20,
  });
  const frogPageMountedRef = useRef(true);

  const cancelActiveFrogRun = useCallback(() => {
    const activeRun = activeFrogRunRef.current;
    activeFrogRunRef.current = null;
    activeRun?.cancel();
  }, []);

  const clearFrogRouteForInputChange = useCallback((inputs: FrogRunInputs) => {
    frogInputsRef.current = inputs;
    cancelActiveFrogRun();
    setFrogLoading(false);
    setFrogProgress(null);
    setFrogError(null);
    setFrogResult(null);
    setFrogOriginalResult(null);
    setFrogExploration({ nodes: [], edges: [] });
  }, [cancelActiveFrogRun]);

  useEffect(() => {
    const currentInputs: FrogRunInputs = {
      startTrackId: startTrack?.track_id ?? null,
      endTrackId: endTrack?.track_id ?? null,
      trackCount: frogTrackCount,
    };
    frogInputsRef.current = currentInputs;
    const activeRun = activeFrogRunRef.current;
    if (activeRun && !frogRunMatchesInputs(activeRun, currentInputs)) {
      cancelActiveFrogRun();
    }
  }, [
    startTrack?.track_id,
    endTrack?.track_id,
    frogTrackCount,
    cancelActiveFrogRun,
  ]);

  useEffect(() => {
    vibePageMountedRef.current = true;
    frogPageMountedRef.current = true;
    return () => {
      vibePageMountedRef.current = false;
      activeVibeRunRef.current = null;
      frogPageMountedRef.current = false;
      cancelActiveFrogRun();
    };
  }, [cancelActiveFrogRun]);

  const currentVibeRecipe: VibeRecipe = {
    anchorTrackIds: anchors.map((anchor) => anchor.track_id),
    trackCount,
    discoveryRatio,
    flowMode,
    excludeArtists: excludeArtists
      .map((artist) => artist.trim())
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right)),
    coherenceThreshold,
    maxPerAnchorArtist,
    maxPerSimilarArtist,
  };
  const currentVibeRecipeKey = vibeRecipeKey(currentVibeRecipe);
  const vibeRecipeChanged = !!vibeResult
    && (!vibeResultRecipe || vibeRecipeKey(vibeResultRecipe) !== currentVibeRecipeKey);
  const vibeResultIsStale = !!vibeResult && (!vibeResultValid || vibeRecipeChanged);
  const requestedDiscoveryCount = vibeResult
    ? vibeResult.counts.requested_discovery
      ?? (vibeResultRecipe
        ? Math.floor(vibeResultRecipe.trackCount * vibeResultRecipe.discoveryRatio / 100)
        : undefined)
    : undefined;
  const requestedHistoryCount = vibeResult
    ? vibeResult.counts.requested_history
      ?? (vibeResultRecipe && requestedDiscoveryCount !== undefined
        ? vibeResultRecipe.trackCount - requestedDiscoveryCount
        : undefined)
    : undefined;
  const discoveryShortfall = vibeResult && requestedDiscoveryCount !== undefined
    ? requestedDiscoveryCount - vibeResult.counts.discovery
    : 0;
  const anchorMixMax = Math.max(
    1,
    ...(vibeResult?.anchor_mix ?? []).map((anchor) => anchor.count),
  );
  const measuredFlowTransitions = vibeResult?.flow_stats.measured_transitions ?? 0;
  const totalFlowTransitions = vibeResult?.flow_stats.total_transitions ?? 0;
  const hasMeasuredAudioFlow = !!vibeResult
    && vibeResult.flow_stats.measurement_basis !== 'unavailable'
    && measuredFlowTransitions > 0;

  const generateVibeMutation = useMutation({
    mutationFn: ({ request }: VibeRunVariables) => generateVibePlaylist(request),
    onSuccess: (data, variables) => {
      const activeRun = activeVibeRunRef.current;
      if (
        !vibePageMountedRef.current
        || !activeRun
        || activeRun.token !== variables.token
        || activeRun.recipeKey !== vibeRecipeKey(variables.recipe)
      ) return;
      setVibeResult(data);
      setVibeResultRecipe(variables.recipe);
      setVibeResultValid(true);
      setVibeError(null);
    },
    onError: (error, variables) => {
      if (
        !vibePageMountedRef.current
        || activeVibeRunRef.current?.token !== variables.token
      ) return;
      setVibeError(error);
    },
    onSettled: (_data, _error, variables) => {
      if (
        !vibePageMountedRef.current
        || activeVibeRunRef.current?.token !== variables.token
      ) return;
      activeVibeRunRef.current = null;
      setVibeLoading(false);
    },
  });

  const handleGenerateFrog = useCallback(() => {
    if (!startTrack || !endTrack) return;

    cancelActiveFrogRun();

    const submittedInputs = {
      startTrackId: startTrack.track_id,
      endTrackId: endTrack.track_id,
      trackCount: frogTrackCount,
    };
    const run: ActiveFrogRun = {
      ...submittedInputs,
      token: ++frogRunTokenRef.current,
      cancel: () => {},
    };
    activeFrogRunRef.current = run;
    setFrogExplorerRunKey(run.token);

    const isCurrentRun = () => (
      frogPageMountedRef.current
      && activeFrogRunRef.current?.token === run.token
      && frogRunMatchesInputs(run, frogInputsRef.current)
    );

    setFrogLoading(true);
    setFrogError(null);
    setFrogProgress(null);
    setFrogResult(null);
    setFrogOriginalResult(null);
    setFrogExploration({ nodes: [], edges: [] });

    const cancel = generateFrogPlaylistStreaming(
      {
        start_track_id: submittedInputs.startTrackId,
        end_track_id: submittedInputs.endTrackId,
        track_count: submittedInputs.trackCount,
      },
      (progress) => {
        if (!isCurrentRun()) return;
        setFrogProgress(progress);
        if (progress.exploration) {
          setFrogExploration((current) => (
            isCurrentRun()
              ? mergeExploration(current, progress.exploration)
              : current
          ));
        }
      },
      (result) => {
        if (!isCurrentRun()) return;
        const resultMatchesSubmission = result.tracks.length === run.trackCount
          && result.tracks[0]?.track_id === run.startTrackId
          && result.tracks[result.tracks.length - 1]?.track_id === run.endTrackId;
        activeFrogRunRef.current = null;
        setFrogLoading(false);
        setFrogProgress(null);
        if (!resultMatchesSubmission) {
          setFrogError('Frog Mode returned a route for different inputs. Please try again.');
          return;
        }
        setFrogResult(result);
        setFrogOriginalResult({
          ...result,
          tracks: result.tracks.map((track) => ({ ...track })),
        });
        setFrogExploration(result.exploration || { nodes: [], edges: [] });
      },
      (error) => {
        if (!isCurrentRun()) return;
        activeFrogRunRef.current = null;
        setFrogLoading(false);
        setFrogProgress(null);
        setFrogError(error.error);
      }
    );
    run.cancel = cancel;
    if (!isCurrentRun()) cancel();
  }, [startTrack, endTrack, frogTrackCount, cancelActiveFrogRun]);

  const handleCancelFrog = useCallback(() => {
    cancelActiveFrogRun();
    setFrogLoading(false);
    setFrogProgress(null);
    setFrogError('Route search cancelled.');
  }, [cancelActiveFrogRun]);

  const handleStartTrackChange = useCallback((track: AnchorTrack | null) => {
    if ((track?.track_id ?? null) === frogInputsRef.current.startTrackId) return;
    clearFrogRouteForInputChange({
      ...frogInputsRef.current,
      startTrackId: track?.track_id ?? null,
    });
    setStartTrack(track);
  }, [clearFrogRouteForInputChange]);

  const handleEndTrackChange = useCallback((track: AnchorTrack | null) => {
    if ((track?.track_id ?? null) === frogInputsRef.current.endTrackId) return;
    clearFrogRouteForInputChange({
      ...frogInputsRef.current,
      endTrackId: track?.track_id ?? null,
    });
    setEndTrack(track);
  }, [clearFrogRouteForInputChange]);

  const handleFrogTrackCountChange = useCallback((trackCount: number) => {
    if (trackCount === frogInputsRef.current.trackCount) return;
    clearFrogRouteForInputChange({
      ...frogInputsRef.current,
      trackCount,
    });
    setFrogTrackCount(trackCount);
  }, [clearFrogRouteForInputChange]);

  const handleFrogReplace = useCallback((
    position: number,
    alternative: FrogAlternative,
  ) => {
    if (
      !frogResult
      || position <= 0
      || position >= frogResult.tracks.length - 1
    ) return;

    const tracks = frogResult.tracks.map((track) => ({ ...track }));
    const conservative = getFrogAlternativeScores(alternative);
    tracks[position] = {
      ...alternative.track,
      position,
      role: 'bridge',
      transition_similarity: conservative.left,
    };
    tracks[position + 1] = {
      ...tracks[position + 1],
      transition_similarity: conservative.right,
    };
    const transitionScores = tracks
      .slice(1)
      .map((track) => track.transition_similarity ?? 0);
    const weakest = Math.min(...transitionScores);
    const average = transitionScores.reduce((sum, score) => sum + score, 0)
      / transitionScores.length;
    setFrogResult({
      ...frogResult,
      tracks,
      weakest_transition: weakest,
      average_transition: average,
      meets_smoothness_target: weakest >= 0.12,
      quality_warning: weakest < 0.12
        ? `This edited route has a ${(weakest * 100).toFixed(0)}% weakest similarity signal.`
        : null,
    });
    setFrogExploration((graph) => explorationWithRoute(graph, tracks));
  }, [frogResult]);

  const handleFrogReset = useCallback(() => {
    if (!frogOriginalResult) return;
    const restored = {
      ...frogOriginalResult,
      tracks: frogOriginalResult.tracks.map((track) => ({ ...track })),
    };
    setFrogResult(restored);
    setFrogExploration(restored.exploration || { nodes: [], edges: [] });
  }, [frogOriginalResult]);

  const createMutation = useMutation({
    mutationFn: ({ name, trackIds, mode: submittedMode }: CreatePlaylistVariables) =>
      createPlaylist(
        name,
        trackIds,
        submittedMode === 'vibe'
          ? 'Vibe playlist from Spotify History'
          : 'Frog transition playlist',
      ),
    onSuccess: (data) => {
      if (data.url) window.open(data.url, '_blank');
    },
    onSettled: () => {
      createInFlightRef.current = false;
    },
  });

  const handleAddAnchor = (track: AnchorTrack) => {
    if (anchors.length < 5 && !anchors.find((a) => a.track_id === track.track_id)) {
      setAnchors([...anchors, track]);
    }
  };

  const handleRemoveAnchor = (trackId: string) => {
    setAnchors(anchors.filter((a) => a.track_id !== trackId));
  };

  const handleSelectStart = (track: AnchorTrack) => {
    if (endTrack?.track_id !== track.track_id) {
      handleStartTrackChange(track);
    }
  };

  const handleSelectEnd = (track: AnchorTrack) => {
    if (startTrack?.track_id !== track.track_id) {
      handleEndTrackChange(track);
    }
  };

  const handleGenerate = () => {
    if (createInFlightRef.current || createMutation.isPending) return;
    if (mode === 'vibe') {
      if (anchors.length === 0) return;
      createMutation.reset();
      const recipe: VibeRecipe = {
        ...currentVibeRecipe,
        anchorTrackIds: [...currentVibeRecipe.anchorTrackIds],
        excludeArtists: [...currentVibeRecipe.excludeArtists],
      };
      const token = ++vibeRunTokenRef.current;
      activeVibeRunRef.current = {
        token,
        recipeKey: vibeRecipeKey(recipe),
      };
      setVibeLoading(true);
      setVibeError(null);
      setVibeResultValid(false);
      generateVibeMutation.mutate({
        token,
        recipe,
        request: vibeRequestFromRecipe(recipe),
      });
    } else {
      handleGenerateFrog();
    }
  };

  const handleCreate = () => {
    if (createInFlightRef.current || createMutation.isPending) return;
    if (mode === 'vibe') {
      if (
        !vibeResult?.tracks.length
        || vibeResultIsStale
        || vibeLoading
        || activeVibeRunRef.current
      ) return;
      const trackIds = vibeResult.tracks.map((t) => t.track_id).filter(Boolean);
      createInFlightRef.current = true;
      createMutation.mutate({ name: playlistName, trackIds, mode: 'vibe' });
    } else {
      if (!frogResult?.tracks.length) return;
      const trackIds = frogResult.tracks.map((t) => t.track_id).filter(Boolean);
      createInFlightRef.current = true;
      createMutation.mutate({ name: frogPlaylistName, trackIds, mode: 'frog' });
    }
  };

  const handleAddExclude = () => {
    const artist = excludeInput.trim();
    if (artist && !excludeArtists.includes(artist)) {
      setExcludeArtists([...excludeArtists, artist]);
      setExcludeInput('');
    }
  };

  const flowModes: { value: FlowMode; label: string; desc: string }[] = [
    { value: 'smooth', label: 'Smooth Flow', desc: 'Woven mini-runs across your anchors' },
    { value: 'energy_arc', label: 'Energy Arc', desc: 'Uses energy data when Spotify provides it' },
    { value: 'shuffle', label: 'Shuffle', desc: 'Random order' },
  ];

  const isGenerating = mode === 'vibe' ? vibeLoading : frogLoading;
  const hasError = mode === 'vibe' ? vibeError !== null : !!frogError;
  const canGenerate = mode === 'vibe' ? anchors.length > 0 : (startTrack && endTrack);
  const vibeErrorMessage = axios.isAxiosError<{ detail?: string }>(vibeError)
    ? vibeError.response?.data?.detail || vibeError.message
    : vibeError instanceof Error
      ? vibeError.message
      : 'Failed to generate playlist.';
  const createErrorMessage = axios.isAxiosError<{ detail?: string }>(createMutation.error)
    ? createMutation.error.response?.data?.detail || createMutation.error.message
    : createMutation.error instanceof Error
      ? createMutation.error.message
      : 'Failed to create the playlist on Spotify.';
  const createFeedbackForCurrentMode = createMutation.variables?.mode === mode;
  const frogRouteEdited = !!frogResult
    && !!frogOriginalResult
    && frogResult.tracks.some(
      (track, index) => track.track_id !== frogOriginalResult.tracks[index]?.track_id,
    );

  return (
    <div className="space-y-6">
      {/* Header with Mode Toggle */}
      <div className="animate-fade-in">
        <div className="flex items-center gap-4 mb-2">
          <h1 className="text-4xl font-bold">
            <span className="gradient-text">{mode === 'vibe' ? 'Vibe' : 'Frog'}</span> Playlist
          </h1>
        </div>
        <p className="text-[var(--text-secondary)] mb-4">
          {mode === 'vibe'
            ? 'Pick anchor tracks that define your vibe, and get a coherent playlist'
            : 'Pick start and end tracks, and create a smooth transition between them'}
        </p>

        {/* Mode Toggle */}
        <div className="flex gap-2">
          <button
            onClick={() => setMode('vibe')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'vibe'
                ? 'bg-[var(--accent-primary)] text-white'
                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            Vibe Mode
          </button>
          <button
            onClick={() => setMode('frog')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'frog'
                ? 'bg-[var(--accent-primary)] text-white'
                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            Frog Mode
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Track Selection */}
        <div className="lg:col-span-1 glass-card p-4">
          {mode === 'vibe' ? (
            <>
              <h3 className="font-semibold mb-4">1. Pick Your Anchors</h3>
              <p className="text-sm text-[var(--text-muted)] mb-4">
                Select 1-5 tracks that define the vibe you want
              </p>
              <AnchorTrackPicker
                selectedAnchors={anchors}
                onSelect={handleAddAnchor}
                onRemove={handleRemoveAnchor}
                maxAnchors={5}
              />
            </>
          ) : (
            <>
              <h3 className="font-semibold mb-4">1. Pick Start & End</h3>
              <p className="text-sm text-[var(--text-muted)] mb-4">
                Select the tracks to transition between
              </p>

              {/* Start Track */}
              <div className="mb-6">
                <h4 className="text-sm font-medium text-[var(--text-secondary)] mb-2">Start Track</h4>
                {startTrack ? (
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-green-500/10 border border-green-500/30">
                    {startTrack.image_url && (
                      <img src={startTrack.image_url} alt="" className="w-10 h-10 rounded" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{startTrack.track}</div>
                      <div className="text-xs text-[var(--text-muted)] truncate">{startTrack.artist}</div>
                    </div>
                    <button
                      onClick={() => handleStartTrackChange(null)}
                      className="p-1 hover:bg-white/10 rounded"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ) : (
                  <div className="border-2 border-dashed border-green-500/30 rounded-lg p-4">
                    <AnchorTrackPicker
                      selectedAnchors={startTrack ? [startTrack] : []}
                      onSelect={handleSelectStart}
                      onRemove={() => handleStartTrackChange(null)}
                      maxAnchors={1}
                      compact
                    />
                  </div>
                )}
              </div>

              {/* Arrow */}
              <div className="flex justify-center mb-6">
                <svg className="w-6 h-6 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
              </div>

              {/* End Track */}
              <div>
                <h4 className="text-sm font-medium text-[var(--text-secondary)] mb-2">End Track</h4>
                {endTrack ? (
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-purple-500/10 border border-purple-500/30">
                    {endTrack.image_url && (
                      <img src={endTrack.image_url} alt="" className="w-10 h-10 rounded" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{endTrack.track}</div>
                      <div className="text-xs text-[var(--text-muted)] truncate">{endTrack.artist}</div>
                    </div>
                    <button
                      onClick={() => handleEndTrackChange(null)}
                      className="p-1 hover:bg-white/10 rounded"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ) : (
                  <div className="border-2 border-dashed border-purple-500/30 rounded-lg p-4">
                    <AnchorTrackPicker
                      selectedAnchors={endTrack ? [endTrack] : []}
                      onSelect={handleSelectEnd}
                      onRemove={() => handleEndTrackChange(null)}
                      maxAnchors={1}
                      compact
                    />
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Settings Panel */}
        <div className="lg:col-span-1 space-y-4">
          {/* Playlist Name */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Playlist Name
            </label>
            <input
              type="text"
              value={mode === 'vibe' ? playlistName : frogPlaylistName}
              onChange={(e) => mode === 'vibe' ? setPlaylistName(e.target.value) : setFrogPlaylistName(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg focus:border-[var(--accent-primary)] focus:outline-none text-[var(--text-primary)]"
              placeholder={mode === 'vibe' ? 'My Vibe Mix' : 'Frog Transition'}
            />
          </div>

          {/* Track Count */}
          <div className="glass-card p-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
              Number of Tracks: {mode === 'vibe' ? trackCount : frogTrackCount}
            </label>
            <input
              type="range"
              min={mode === 'vibe' ? 10 : 5}
              max={mode === 'vibe' ? 100 : 50}
              step="5"
              value={mode === 'vibe' ? trackCount : frogTrackCount}
              onChange={(e) => {
                const nextCount = parseInt(e.target.value);
                if (mode === 'vibe') {
                  setTrackCount(nextCount);
                } else {
                  handleFrogTrackCountChange(nextCount);
                }
              }}
              className="w-full accent-[var(--accent-primary)]"
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>{mode === 'vibe' ? 10 : 5}</span>
              <span>{mode === 'vibe' ? 100 : 50}</span>
            </div>
          </div>

          {/* Vibe-only settings */}
          {mode === 'vibe' && (
            <>
              {/* Discovery Ratio */}
              <div className="glass-card p-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  New Music: {discoveryRatio}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="10"
                  value={discoveryRatio}
                  onChange={(e) => setDiscoveryRatio(parseInt(e.target.value))}
                  className="w-full accent-[var(--accent-primary)]"
                />
                <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                  <span>All familiar</span>
                  <span>All new</span>
                </div>
              </div>

              {/* Flow Mode */}
              <div className="glass-card p-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  Flow Mode
                </label>
                <div className="space-y-2">
                  {flowModes.map((m) => (
                    <label
                      key={m.value}
                      className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                        flowMode === m.value
                          ? 'bg-[var(--accent-primary)]/20 border border-[var(--accent-primary)]'
                          : 'bg-[var(--bg-secondary)] border border-transparent hover:border-white/10'
                      }`}
                    >
                      <input
                        type="radio"
                        name="flowMode"
                        value={m.value}
                        checked={flowMode === m.value}
                        onChange={(e) => setFlowMode(e.target.value as FlowMode)}
                        className="mt-1 accent-[var(--accent-primary)]"
                      />
                      <div>
                        <div className="font-medium text-sm">{m.label}</div>
                        <div className="text-xs text-[var(--text-muted)]">{m.desc}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              {/* Exclude Artists */}
              <div className="glass-card p-4">
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                  Exclude Artists
                </label>
                {excludeArtists.length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-3">
                    {excludeArtists.map((a) => (
                      <button
                        key={a}
                        onClick={() => setExcludeArtists(excludeArtists.filter((x) => x !== a))}
                        className="px-2 py-1 text-xs rounded-full bg-red-500/20 text-red-400 hover:bg-red-500/30"
                      >
                        {a} x
                      </button>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={excludeInput}
                    onChange={(e) => setExcludeInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAddExclude()}
                    className="flex-1 px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-primary)] text-sm focus:outline-none focus:border-[var(--accent-primary)]"
                    placeholder="Artist name..."
                  />
                  <button
                    onClick={handleAddExclude}
                    className="px-3 py-2 bg-[var(--bg-secondary)] border border-white/10 rounded-lg text-[var(--text-secondary)] hover:border-white/20"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Advanced Settings Toggle */}
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="w-full flex items-center justify-between px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              >
                <span>Advanced Settings</span>
                <svg
                  className={`w-4 h-4 transition-transform ${showAdvanced ? 'rotate-180' : ''}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {/* Advanced Settings Panel */}
              {showAdvanced && (
                <div className="glass-card p-4 space-y-4">
                  {/* Coherence Threshold */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                      Strictness: {coherenceThreshold}%
                    </label>
                    <input
                      type="range"
                      min="20"
                      max="80"
                      step="5"
                      value={coherenceThreshold}
                      onChange={(e) => setCoherenceThreshold(parseInt(e.target.value))}
                      className="w-full accent-[var(--accent-primary)]"
                    />
                    <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                      <span>Loose</span>
                      <span>Strict</span>
                    </div>
                  </div>

                  {/* Max per anchor artist */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                      Tracks per anchor artist: {maxPerAnchorArtist}
                    </label>
                    <input
                      type="range"
                      min="0"
                      max="10"
                      step="1"
                      value={maxPerAnchorArtist}
                      onChange={(e) => setMaxPerAnchorArtist(parseInt(e.target.value))}
                      className="w-full accent-[var(--accent-primary)]"
                    />
                    <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                      <span>None</span>
                      <span>Many</span>
                    </div>
                  </div>

                  {/* Max per similar artist */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
                      Tracks per similar artist: {maxPerSimilarArtist}
                    </label>
                    <input
                      type="range"
                      min="1"
                      max="5"
                      step="1"
                      value={maxPerSimilarArtist}
                      onChange={(e) => setMaxPerSimilarArtist(parseInt(e.target.value))}
                      className="w-full accent-[var(--accent-primary)]"
                    />
                    <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
                      <span>1</span>
                      <span>5</span>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Frog mode explanation / progress */}
          {mode === 'frog' && (
            <div className="glass-card p-4">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-muted)]">
                  Graph lab
                </div>
                <button
                  type="button"
                  onClick={() => setShowFrogExplorer((visible) => !visible)}
                  aria-pressed={showFrogExplorer}
                  className={`px-3 py-2 rounded-lg text-xs border transition-colors ${
                    showFrogExplorer
                      ? 'bg-[var(--accent-secondary)]/15 border-[var(--accent-secondary)]/50 text-[var(--accent-secondary)]'
                      : 'bg-[var(--bg-secondary)] border-[var(--border-soft)] text-[var(--text-secondary)]'
                  }`}
                >
                  {showFrogExplorer ? 'Hide exploration' : 'Show exploration'}
                </button>
              </div>
              {frogProgress ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-[var(--accent-primary)]/30 border-t-[var(--accent-primary)] rounded-full animate-spin" />
                    <h4 className="font-medium text-sm">Finding path...</h4>
                  </div>

                  {/* Phase indicator */}
                  <div className="text-xs text-[var(--text-muted)]">
                    {frogProgress.message || (
                      frogProgress.phase === 'neighborhood' ? 'Building neighborhood...' :
                      frogProgress.phase === 'search' ? 'Searching...' :
                      frogProgress.phase === 'expanding' ? 'Building the exact-length route...' :
                      frogProgress.phase === 'resolving' ? 'Resolving to Spotify...' :
                      'Initializing...'
                    )}
                  </div>

                  {/* Search stats */}
                  {frogProgress.phase === 'search' && (
                    <div className="space-y-2">
                      <div className="grid grid-cols-3 gap-2 text-center">
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-[var(--accent-primary)]">
                            {frogProgress.iteration || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">Iteration</div>
                        </div>
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-green-400">
                            {frogProgress.visited || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">Visited</div>
                        </div>
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-blue-400">
                            {frogProgress.queue_size || 0}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">In Queue</div>
                        </div>
                      </div>

                      {/* Current track */}
                      {frogProgress.current_track && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)] text-xs">
                          <span className="text-[var(--text-muted)]">Exploring: </span>
                          <span className="text-[var(--text-primary)]">{frogProgress.current_track}</span>
                        </div>
                      )}

                      {/* Heuristic progress bar */}
                      {frogProgress.best_h !== undefined && (
                        <div>
                          <div className="flex justify-between text-xs text-[var(--text-muted)] mb-1">
                            <span>Distance to goal</span>
                            <span>{((1 - frogProgress.best_h) * 100).toFixed(0)}%</span>
                          </div>
                          <div className="h-2 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
                            <div
                              className="h-full bg-gradient-to-r from-[var(--accent-primary)] to-green-400 transition-all duration-300"
                              style={{ width: `${(1 - frogProgress.best_h) * 100}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Neighborhood building */}
                  {frogProgress.phase === 'neighborhood' && (
                    <div className="grid grid-cols-2 gap-2 text-center">
                      {frogProgress.neighborhood_1hop !== undefined && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-[var(--accent-primary)]">
                            {frogProgress.neighborhood_1hop}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">1-hop tracks</div>
                        </div>
                      )}
                      {frogProgress.neighborhood_2hop !== undefined && (
                        <div className="p-2 rounded bg-[var(--bg-secondary)]">
                          <div className="text-lg font-mono font-bold text-purple-400">
                            {frogProgress.neighborhood_2hop}
                          </div>
                          <div className="text-xs text-[var(--text-muted)]">2-hop tracks</div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Exact-length route growth */}
                  {frogProgress.phase === 'expanding' && frogProgress.built_length !== undefined && (
                    <div className="space-y-2">
                      <div className="flex justify-between text-xs text-[var(--text-muted)]">
                        <span>
                          {frogProgress.built_length} / {frogProgress.target_length || frogTrackCount} tracks
                        </span>
                        <span>
                          {frogProgress.elapsed_seconds !== undefined
                            ? `${frogProgress.elapsed_seconds.toFixed(1)}s`
                            : ''}
                        </span>
                      </div>
                      <div className="h-2 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-[var(--accent-primary)] to-green-400 transition-all duration-300"
                          style={{
                            width: `${Math.min(
                              100,
                              (frogProgress.built_length / (frogProgress.target_length || frogTrackCount)) * 100
                            )}%`,
                          }}
                        />
                      </div>
                      {frogProgress.weakest_transition !== undefined && (
                        <div className="text-xs text-[var(--text-muted)]">
                          Weakest hop so far:{' '}
                          <span className="text-[var(--text-primary)]">
                            {(frogProgress.weakest_transition * 100).toFixed(0)}%
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <>
                  <h4 className="font-medium text-sm mb-2">How it works</h4>
                  <p className="text-xs text-[var(--text-muted)]">
                    Frog Mode finds a short route spine, then subdivides its weakest
                    jumps until it reaches your exact song count. Every inserted song
                    must be similar to both of its neighbors. If the endpoints force a
                    wider hop, it returns the best exact-length route with a warning.
                  </p>
                </>
              )}
            </div>
          )}

          {/* Generate Button */}
          {mode === 'frog' && frogLoading ? (
            <button
              onClick={handleCancelFrog}
              className="w-full py-3 rounded-lg border border-red-400/40 bg-red-500/10 text-red-300 hover:bg-red-500/20 transition-colors flex items-center justify-center gap-2"
            >
              <div className="w-4 h-4 border-2 border-red-200/30 border-t-red-200 rounded-full animate-spin" />
              Cancel route search
            </button>
          ) : (
            <button
              onClick={handleGenerate}
              disabled={!canGenerate || isGenerating || createMutation.isPending}
              className="w-full btn-primary py-3 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {isGenerating ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Generating...
                </>
              ) : (
                mode === 'frog' ? 'Find Path' : 'Generate Playlist'
              )}
            </button>
          )}

          {hasError && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {mode === 'frog' ? (frogError || 'No path found. Try tracks that are more similar.') : vibeErrorMessage}
            </div>
          )}
        </div>

        {/* Preview Panel */}
        <div className="lg:col-span-1 space-y-4">
          {mode === 'vibe' && vibeResult ? (
            <>
              {/* Playlist Header */}
              <div className="glass-card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold">{playlistName}</h3>
                    <p className="text-sm text-[var(--text-muted)]">
                      {vibeResult.tracks.length} tracks
                    </p>
                  </div>
                  <button
                    onClick={handleCreate}
                    disabled={createMutation.isPending || vibeResultIsStale || vibeLoading}
                    title={vibeResultIsStale
                      ? 'Regenerate this playlist after changing its recipe.'
                      : undefined}
                    className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                  >
                    {createMutation.isPending ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Creating...
                      </>
                    ) : vibeResultIsStale ? (
                      'Regenerate first'
                    ) : vibeLoading ? (
                      'Refreshing...'
                    ) : (
                      'Create on Spotify'
                    )}
                  </button>
                </div>

                {vibeResultIsStale && (
                  <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-400/30 text-amber-200 text-sm">
                    <span className="font-medium">
                      {vibeRecipeChanged
                        ? 'Recipe changed.'
                        : vibeLoading
                          ? 'Regenerating playlist.'
                          : vibeError
                            ? 'Latest generation failed.'
                            : 'Regeneration required.'}
                    </span>{' '}
                    {vibeRecipeChanged
                      ? 'This preview uses your previous anchors or settings.'
                      : 'This is the previous preview.'}{' '}
                    Generate it successfully before creating it on Spotify.
                  </div>
                )}

                {!vibeResultIsStale && createFeedbackForCurrentMode && createMutation.isSuccess && (
                  <div className="mb-4 p-3 rounded-lg bg-green-500/10 border border-green-500/20 text-green-400 text-sm">
                    Playlist created successfully.{' '}
                    {createMutation.data?.url && (
                      <a
                        href={createMutation.data.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium underline underline-offset-2"
                      >
                        Open playlist
                      </a>
                    )}
                  </div>
                )}

                {createFeedbackForCurrentMode && createMutation.isError && (
                  <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                    {createErrorMessage}
                  </div>
                )}

                {vibeResult.warnings && vibeResult.warnings.length > 0 && (
                  <div className="mb-4 space-y-2">
                    {vibeResult.warnings.map((warning, index) => (
                      <div
                        key={`${warning}-${index}`}
                        className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-400/20 text-amber-100/90 text-xs"
                      >
                        {warning}
                      </div>
                    ))}
                  </div>
                )}

                {/* Vibe Profile */}
                {vibeResult.vibe_profile.top_genres.length > 0 && (
                  <div className="mb-3">
                    <p className="text-xs text-[var(--text-muted)] mb-1">Available anchor genres:</p>
                    <div className="flex flex-wrap gap-1">
                      {vibeResult.vibe_profile.top_genres.map((g) => (
                        <span
                          key={g}
                          className="px-2 py-0.5 text-xs rounded-full bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]"
                        >
                          {g}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {vibeResult.anchor_mix && vibeResult.anchor_mix.length > 0 && (
                  <div className="mb-4">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs text-[var(--text-muted)]">Anchor blend</p>
                      <p className="text-[10px] text-[var(--text-muted)]">familiar + new</p>
                    </div>
                    <div className="space-y-2">
                      {vibeResult.anchor_mix.map((anchor) => {
                        const historyWidth = anchor.count > 0
                          ? (anchor.history / anchor.count) * 100
                          : 0;
                        const discoveryWidth = anchor.count > 0
                          ? (anchor.discovery / anchor.count) * 100
                          : 0;
                        return (
                          <div key={anchor.anchor_track_id}>
                            <div className="flex items-baseline justify-between gap-3 text-xs mb-1">
                              <p className="truncate text-[var(--text-secondary)]">
                                <span className="text-[var(--text-primary)]">{anchor.anchor_track}</span>
                                {' · '}{anchor.anchor_artist}
                              </p>
                              <span className="shrink-0 tabular-nums text-[var(--text-muted)]">
                                {anchor.count} ({anchor.history}+{anchor.discovery})
                              </span>
                            </div>
                            <div className="h-1.5 rounded-full overflow-hidden bg-[var(--bg-secondary)]">
                              <div
                                className="h-full flex rounded-full overflow-hidden"
                                style={{ width: `${(anchor.count / anchorMixMax) * 100}%` }}
                              >
                                <div
                                  className="h-full bg-[var(--accent-primary)]"
                                  style={{ width: `${historyWidth}%` }}
                                />
                                <div
                                  className="h-full bg-[var(--accent-secondary)]"
                                  style={{ width: `${discoveryWidth}%` }}
                                />
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Stats */}
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
                  <span>
                    Familiar: {vibeResult.counts.history}
                    {requestedHistoryCount !== undefined && ` / ${requestedHistoryCount} target`}
                  </span>
                  <span>
                    New: {vibeResult.counts.discovery}
                    {requestedDiscoveryCount !== undefined && ` / ${requestedDiscoveryCount} target`}
                  </span>
                  {discoveryShortfall > 0 && (
                    <span className="text-amber-300">{discoveryShortfall} new tracks short</span>
                  )}
                  {discoveryShortfall < 0 && (
                    <span className="text-[var(--text-secondary)]">{Math.abs(discoveryShortfall)} extra new</span>
                  )}
                  <span>
                    {vibeResultRecipe?.flowMode === 'shuffle'
                      ? 'Flow: shuffled'
                      : vibeResultRecipe?.flowMode === 'energy_arc'
                      ? hasMeasuredAudioFlow
                        ? `Energy arc · audio measured ${measuredFlowTransitions}/${totalFlowTransitions} hops`
                        : 'Energy data unavailable · used balanced anchor weave'
                      : hasMeasuredAudioFlow
                      ? `Flow: audio-smoothed · measured ${measuredFlowTransitions}/${totalFlowTransitions} hops`
                      : 'Flow: balanced anchor weave · audio smoothness not measured'}
                  </span>
                </div>
              </div>

              {/* Track List */}
              <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
                <div className="space-y-2">
                  {vibeResult.tracks.map((track, i) => (
                    <TrackCard
                      key={track.track_id || i}
                      track={track.track}
                      artist={track.artist}
                      imageUrl={track.image_url}
                      previewUrl={track.preview_url}
                      spotifyUrl={track.spotify_url}
                      subtitle={
                        track.source === 'discovery'
                          ? `New · ${track.discovered_via || 'discovery'}`
                          : track.discovered_via === 'anchor'
                            ? `Anchor · ${track.play_count} plays`
                            : `${track.play_count} plays${track.discovered_via ? ` · ${track.discovered_via}` : ''}`
                      }
                      index={i}
                      energy={track.energy}
                      valence={track.valence}
                      tempo={track.tempo}
                      showFeatures={vibeResult.vibe_profile.has_audio_features}
                    />
                  ))}
                </div>
              </div>
            </>
          ) : mode === 'frog' && frogResult ? (
            <>
              {/* Frog Playlist Header */}
              <div className="glass-card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="font-semibold">{frogPlaylistName}</h3>
                    <p className="text-sm text-[var(--text-muted)]">
                      {frogResult.tracks.length} / {frogResult.requested_length || frogResult.tracks.length} tracks
                      {frogResult.weakest_transition !== undefined && (
                        <span>
                          {' '}· weakest hop {(frogResult.weakest_transition * 100).toFixed(0)}%
                          {' '}similarity signal · average {(frogResult.average_transition! * 100).toFixed(0)}%
                        </span>
                      )}
                    </p>
                  </div>
                  <button
                    onClick={handleCreate}
                    disabled={createMutation.isPending}
                    className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                  >
                    {createMutation.isPending ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Creating...
                      </>
                    ) : (
                      'Create on Spotify'
                    )}
                  </button>
                </div>

                {createFeedbackForCurrentMode && createMutation.isSuccess && (
                  <div className="mb-4 p-3 rounded-lg bg-green-500/10 border border-green-500/20 text-green-400 text-sm">
                    Playlist created successfully.{' '}
                    {createMutation.data?.url && (
                      <a
                        href={createMutation.data.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium underline underline-offset-2"
                      >
                        Open playlist
                      </a>
                    )}
                  </div>
                )}

                {createFeedbackForCurrentMode && createMutation.isError && (
                  <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                    {createErrorMessage}
                  </div>
                )}

                {frogResult.quality_warning && (
                  <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
                    {frogResult.quality_warning}
                  </div>
                )}
              </div>

              {/* Frog Track List */}
              <div className="glass-card p-4 max-h-[550px] overflow-y-auto">
                <div className="space-y-2">
                  {frogResult.tracks.map((track, i) => (
                    <div key={track.track_id || i} className="relative">
                      {/* Role badge */}
                      {(track.role === 'start' || track.role === 'end') && (
                        <div className={`absolute -left-2 top-1/2 -translate-y-1/2 px-1.5 py-0.5 text-xs rounded ${
                          track.role === 'start' ? 'bg-green-500/20 text-green-400' : 'bg-purple-500/20 text-purple-400'
                        }`}>
                          {track.role === 'start' ? 'START' : 'END'}
                        </div>
                      )}
                      <TrackCard
                        track={track.track}
                        artist={track.artist}
                        imageUrl={track.image_url}
                        previewUrl={track.preview_url}
                        spotifyUrl={track.spotify_url}
                        subtitle={
                          track.transition_similarity == null
                            ? track.role
                            : `${track.role} · ${(track.transition_similarity * 100).toFixed(0)}% Last.fm similarity signal`
                        }
                        index={i}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="glass-card p-8 text-center">
              <div className="text-4xl mb-4">{mode === 'vibe' ? '🎵' : '🐸'}</div>
              <h3 className="font-semibold mb-2">
                {mode === 'vibe' ? 'Pick Your Anchors' : 'Pick Start & End'}
              </h3>
              <p className="text-sm text-[var(--text-muted)]">
                {mode === 'vibe'
                  ? 'Select 1-5 tracks that define the vibe you want, then click Generate'
                  : 'Select your start and end tracks, then click Find Path'}
              </p>
            </div>
          )}
        </div>
      </div>

      {mode === 'frog'
        && showFrogExplorer
        && (frogLoading || !!frogResult || frogExploration.nodes.length > 0)
        && (
          <FrogGraphExplorer
            key={`frog-run:${frogExplorerRunKey}:${
              frogResult?.tracks.map((track) => track.track_id).join('|') || 'searching'
            }`}
            exploration={frogExploration}
            tracks={frogResult?.tracks || []}
            isLoading={frogLoading}
            canReset={frogRouteEdited}
            onReplace={handleFrogReplace}
            onReset={handleFrogReset}
          />
        )}
    </div>
  );
}
