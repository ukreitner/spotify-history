import type {
  FrogExploration,
  FrogGraphEdge,
  FrogGraphNode,
} from './api';

export interface FrogGraphPoint {
  x: number;
  y: number;
}

export type FrogGraphSemanticRole =
  | 'start'
  | 'end'
  | 'route'
  | 'meeting'
  | 'forward'
  | 'backward';

export interface FrogGraphLayoutOptions {
  width?: number;
  height?: number;
  padding?: number;
  maxNodes?: number;
  iterations?: number;
  /**
   * Positions from the previous live-search frame. Supplying these makes new
   * nodes grow around the existing web instead of redrawing the whole graph.
   */
  previousPositions?:
    | ReadonlyMap<string, FrogGraphPoint>
    | Readonly<Record<string, FrogGraphPoint>>;
  /**
   * How strongly to retain previous positions, from 0 (fresh layout) to 1
   * (retain them as the initial state). Defaults to 0.78.
   */
  stability?: number;
}

export interface FrogGraphLayoutNode extends FrogGraphNode {
  x: number;
  y: number;
  /** A 0–1 start-to-end coordinate, useful for gradients and navigation. */
  progress: number;
  role: FrogGraphSemanticRole;
  communityId: string;
  componentId: string;
  degree: number;
  weightedDegree: number;
  distanceFromStart: number | null;
  distanceFromEnd: number | null;
  isAnchor: boolean;
  /** Suggested paint order; route nodes should appear above search nodes. */
  zIndex: number;
}

export interface FrogGraphLayoutEdge extends FrogGraphEdge {
  sourceIndex: number;
  targetIndex: number;
  idealLength: number;
  renderedLength: number;
  sameCommunity: boolean;
}

export interface FrogGraphCommunity {
  id: string;
  nodeIds: string[];
  size: number;
  centerX: number;
  centerY: number;
  dominantDirection: FrogGraphNode['direction'];
}

export interface FrogGraphLayoutResult {
  nodes: FrogGraphLayoutNode[];
  edges: FrogGraphLayoutEdge[];
  nodeById: ReadonlyMap<string, FrogGraphLayoutNode>;
  communities: FrogGraphCommunity[];
  anchors: {
    startId: string | null;
    endId: string | null;
  };
  bounds: {
    width: number;
    height: number;
    padding: number;
  };
  omittedNodeCount: number;
}

interface UndirectedLink {
  source: number;
  target: number;
  similarity: number;
  layoutWeight: number;
  idealLength: number;
}

interface AdjacentNode {
  index: number;
  weight: number;
  similarity: number;
}

const DEFAULT_WIDTH = 1200;
const DEFAULT_HEIGHT = 390;
const DEFAULT_PADDING = 42;
const DEFAULT_MAX_NODES = 180;
const DEFAULT_ITERATIONS = 96;
const EPSILON = 1e-6;

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function finiteOr(value: number | undefined, fallback: number) {
  return Number.isFinite(value) ? value as number : fallback;
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function hashUnit(value: string) {
  return stableHash(value) / 0xffffffff;
}

function compareIds(left: FrogGraphNode, right: FrogGraphNode) {
  return left.id.localeCompare(right.id);
}

function getPreviousPosition(
  previous: FrogGraphLayoutOptions['previousPositions'],
  id: string,
) {
  if (!previous) return undefined;
  if (typeof (previous as ReadonlyMap<string, FrogGraphPoint>).get === 'function') {
    return (previous as ReadonlyMap<string, FrogGraphPoint>).get(id);
  }
  return (previous as Readonly<Record<string, FrogGraphPoint>>)[id];
}

function canonicalizeNodes(nodes: readonly FrogGraphNode[]) {
  const byId = new Map<string, FrogGraphNode>();
  for (const node of nodes) {
    if (!node.id) continue;
    const existing = byId.get(node.id);
    if (!existing) {
      byId.set(node.id, { ...node });
      continue;
    }

    // Route records contain the richest interaction metadata, while a search
    // record may contain the latest expanded state. Keep both pieces.
    const routeRecord = node.route_position !== undefined
      ? node
      : existing.route_position !== undefined
        ? existing
        : undefined;
    const freshest = node.state === 'expanded' ? node : existing;
    byId.set(node.id, {
      ...existing,
      ...freshest,
      ...(routeRecord || {}),
      direction: routeRecord ? 'route' : freshest.direction,
      state: routeRecord?.state || freshest.state,
    });
  }
  return [...byId.values()];
}

function selectNodes(
  nodes: readonly FrogGraphNode[],
  edges: readonly FrogGraphEdge[],
  maxNodes: number,
) {
  if (nodes.length <= maxNodes) return [...nodes].sort(compareIds);

  const degree = new Map<string, number>();
  for (const edge of edges) {
    const similarity = clamp(finiteOr(edge.similarity, 0), 0, 1);
    degree.set(edge.source, (degree.get(edge.source) || 0) + 0.25 + similarity);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 0.25 + similarity);
  }

  return [...nodes]
    .sort((left, right) => {
      const priority = (node: FrogGraphNode) => {
        if (node.route_position !== undefined) return 1_000_000 - node.route_position;
        if (node.state === 'start' || node.state === 'end') return 900_000;
        const stateScore = node.state === 'expanded' ? 40_000 : 0;
        const directionScore = node.depth === 0 ? 30_000 : 0;
        const depthScore = Math.max(0, 20_000 - node.depth * 250);
        return stateScore + directionScore + depthScore + (degree.get(node.id) || 0) * 100;
      };
      return priority(right) - priority(left) || left.id.localeCompare(right.id);
    })
    .slice(0, maxNodes)
    .sort(compareIds);
}

function buildGraph(
  nodes: readonly FrogGraphNode[],
  edges: readonly FrogGraphEdge[],
) {
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const pairWeights = new Map<string, UndirectedLink>();
  const visibleEdges: FrogGraphEdge[] = [];
  const seenEdgeIds = new Set<string>();

  for (const edge of edges) {
    const source = indexById.get(edge.source);
    const target = indexById.get(edge.target);
    if (source === undefined || target === undefined || source === target) continue;

    if (!seenEdgeIds.has(edge.id)) {
      seenEdgeIds.add(edge.id);
      visibleEdges.push({
        ...edge,
        similarity: clamp(finiteOr(edge.similarity, 0), 0, 1),
      });
    }

    const low = Math.min(source, target);
    const high = Math.max(source, target);
    const key = `${low}:${high}`;
    const similarity = clamp(finiteOr(edge.similarity, 0), 0, 1);
    const layoutWeight = 0.12 + similarity * similarity * 1.65
      + (edge.kind === 'route' ? 0.24 : 0);
    const idealLength = 28 + Math.pow(1 - similarity, 1.2) * 88;
    const existing = pairWeights.get(key);
    if (!existing || layoutWeight > existing.layoutWeight) {
      pairWeights.set(key, {
        source: low,
        target: high,
        similarity,
        layoutWeight,
        idealLength,
      });
    }
  }

  const links = [...pairWeights.values()].sort(
    (left, right) => left.source - right.source || left.target - right.target,
  );
  const adjacency: AdjacentNode[][] = Array.from({ length: nodes.length }, () => []);
  for (const link of links) {
    adjacency[link.source].push({
      index: link.target,
      weight: link.layoutWeight,
      similarity: link.similarity,
    });
    adjacency[link.target].push({
      index: link.source,
      weight: link.layoutWeight,
      similarity: link.similarity,
    });
  }
  for (const neighbors of adjacency) {
    neighbors.sort((left, right) => left.index - right.index);
  }

  return { indexById, visibleEdges, links, adjacency };
}

function chooseAnchors(nodes: readonly FrogGraphNode[]) {
  const routeNodes = nodes
    .filter((node) => node.route_position !== undefined)
    .sort((left, right) => (
      (left.route_position as number) - (right.route_position as number)
      || left.id.localeCompare(right.id)
    ));

  const firstMatching = (
    predicate: (node: FrogGraphNode) => boolean,
    depthOrder = true,
  ) => nodes
    .filter(predicate)
    .sort((left, right) => (
      (depthOrder ? left.depth - right.depth : 0)
      || left.id.localeCompare(right.id)
    ))[0];

  const start = routeNodes[0]
    || firstMatching((node) => node.state === 'start', false)
    || firstMatching((node) => node.direction === 'forward');
  const end = routeNodes.length > 1
    ? routeNodes[routeNodes.length - 1]
    : firstMatching((node) => node.state === 'end', false)
      || firstMatching((node) => node.direction === 'backward');

  return {
    startId: start?.id || null,
    endId: end?.id && end.id !== start?.id ? end.id : null,
  };
}

function shortestDistances(
  startIndex: number | undefined,
  adjacency: readonly AdjacentNode[][],
) {
  const distances = Array(adjacency.length).fill(Number.POSITIVE_INFINITY) as number[];
  const visited = Array(adjacency.length).fill(false) as boolean[];
  if (startIndex === undefined) return distances;
  distances[startIndex] = 0;

  // At 180 nodes, this deterministic O(V² + E) Dijkstra is cheaper than
  // maintaining a heap and avoids any dependency or tie-order ambiguity.
  for (let step = 0; step < adjacency.length; step += 1) {
    let current = -1;
    let best = Number.POSITIVE_INFINITY;
    for (let index = 0; index < distances.length; index += 1) {
      if (!visited[index] && distances[index] < best) {
        current = index;
        best = distances[index];
      }
    }
    if (current < 0) break;
    visited[current] = true;

    for (const neighbor of adjacency[current]) {
      const traversalCost = 0.55 + Math.pow(1 - neighbor.similarity, 1.35) * 2.45;
      const candidate = distances[current] + traversalCost;
      if (candidate + EPSILON < distances[neighbor.index]) {
        distances[neighbor.index] = candidate;
      }
    }
  }
  return distances;
}

function connectedComponents(adjacency: readonly AdjacentNode[][]) {
  const components = Array(adjacency.length).fill(-1) as number[];
  let componentCount = 0;

  for (let seed = 0; seed < adjacency.length; seed += 1) {
    if (components[seed] >= 0) continue;
    const queue = [seed];
    components[seed] = componentCount;
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      for (const neighbor of adjacency[current]) {
        if (components[neighbor.index] < 0) {
          components[neighbor.index] = componentCount;
          queue.push(neighbor.index);
        }
      }
    }
    componentCount += 1;
  }
  return components;
}

/**
 * A small deterministic Louvain-style pass. It makes dense similarity pockets
 * available to the UI without pulling in a graph package.
 */
function detectCommunities(
  nodes: readonly FrogGraphNode[],
  adjacency: readonly AdjacentNode[][],
) {
  const count = nodes.length;
  const membership = Array.from({ length: count }, (_, index) => index);
  const nodeWeights = adjacency.map((neighbors) => (
    neighbors.reduce((sum, neighbor) => sum + neighbor.weight, 0)
  ));
  const communityWeights = [...nodeWeights];
  const totalWeight = nodeWeights.reduce((sum, weight) => sum + weight, 0);

  if (totalWeight > EPSILON) {
    for (let pass = 0; pass < 12; pass += 1) {
      let moved = false;
      for (let nodeIndex = 0; nodeIndex < count; nodeIndex += 1) {
        const currentCommunity = membership[nodeIndex];
        const weightByCommunity = new Map<number, number>();
        for (const neighbor of adjacency[nodeIndex]) {
          const community = membership[neighbor.index];
          weightByCommunity.set(
            community,
            (weightByCommunity.get(community) || 0) + neighbor.weight,
          );
        }

        communityWeights[currentCommunity] -= nodeWeights[nodeIndex];
        let bestCommunity = currentCommunity;
        let bestGain = 0;
        for (const [community, insideWeight] of [...weightByCommunity.entries()]
          .sort((left, right) => left[0] - right[0])) {
          const gain = insideWeight
            - (nodeWeights[nodeIndex] * communityWeights[community]) / totalWeight;
          if (
            gain > bestGain + EPSILON
            || (Math.abs(gain - bestGain) <= EPSILON && community < bestCommunity)
          ) {
            bestGain = gain;
            bestCommunity = community;
          }
        }
        membership[nodeIndex] = bestCommunity;
        communityWeights[bestCommunity] += nodeWeights[nodeIndex];
        if (bestCommunity !== currentCommunity) moved = true;
      }
      if (!moved) break;
    }
  }

  const membersByRawCommunity = new Map<number, number[]>();
  membership.forEach((community, nodeIndex) => {
    const members = membersByRawCommunity.get(community) || [];
    members.push(nodeIndex);
    membersByRawCommunity.set(community, members);
  });

  const stableCommunityIds = new Map<number, string>();
  for (const [community, members] of membersByRawCommunity) {
    const signature = members.map((index) => nodes[index].id).sort()[0];
    stableCommunityIds.set(
      community,
      `frog-${stableHash(signature).toString(36)}`,
    );
  }
  return membership.map((community) => stableCommunityIds.get(community) as string);
}

function semanticRoles(
  nodes: readonly FrogGraphNode[],
  adjacency: readonly AdjacentNode[][],
  startId: string | null,
  endId: string | null,
) {
  return nodes.map((node, index): FrogGraphSemanticRole => {
    if (node.id === startId || node.state === 'start') return 'start';
    if (node.id === endId || node.state === 'end') return 'end';
    if (node.route_position !== undefined || node.direction === 'route') return 'route';

    const neighborDirections = new Set(
      adjacency[index].map((neighbor) => nodes[neighbor.index].direction),
    );
    if (
      neighborDirections.has('forward')
      && neighborDirections.has('backward')
    ) {
      return 'meeting';
    }
    return node.direction === 'backward' ? 'backward' : 'forward';
  });
}

function roleZIndex(role: FrogGraphSemanticRole) {
  if (role === 'start' || role === 'end') return 4;
  if (role === 'route') return 3;
  if (role === 'meeting') return 2;
  return 1;
}

function dominantDirection(nodes: readonly FrogGraphNode[]) {
  const counts = new Map<FrogGraphNode['direction'], number>();
  for (const node of nodes) {
    counts.set(node.direction, (counts.get(node.direction) || 0) + 1);
  }
  return ([...counts.entries()] as [FrogGraphNode['direction'], number][])
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0]?.[0]
    || 'forward';
}

export function layoutFrogGraph(
  inputNodes: readonly FrogGraphNode[],
  inputEdges: readonly FrogGraphEdge[],
  options: FrogGraphLayoutOptions = {},
): FrogGraphLayoutResult {
  const width = Math.max(240, finiteOr(options.width, DEFAULT_WIDTH));
  const height = Math.max(180, finiteOr(options.height, DEFAULT_HEIGHT));
  const padding = clamp(
    finiteOr(options.padding, DEFAULT_PADDING),
    12,
    Math.min(width, height) * 0.25,
  );
  const maxNodes = Math.max(
    2,
    Math.min(DEFAULT_MAX_NODES, Math.floor(finiteOr(options.maxNodes, DEFAULT_MAX_NODES))),
  );
  const iterations = Math.max(
    0,
    Math.min(240, Math.floor(finiteOr(options.iterations, DEFAULT_ITERATIONS))),
  );
  const stability = clamp(finiteOr(options.stability, 0.78), 0, 1);
  const canonicalNodes = canonicalizeNodes(inputNodes);
  const nodes = selectNodes(canonicalNodes, inputEdges, maxNodes);
  const {
    indexById,
    visibleEdges,
    links,
    adjacency,
  } = buildGraph(nodes, inputEdges);
  const anchors = chooseAnchors(nodes);
  const startIndex = anchors.startId ? indexById.get(anchors.startId) : undefined;
  const endIndex = anchors.endId ? indexById.get(anchors.endId) : undefined;
  const distancesFromStart = shortestDistances(startIndex, adjacency);
  const distancesFromEnd = shortestDistances(endIndex, adjacency);
  const components = connectedComponents(adjacency);
  const communityIds = detectCommunities(nodes, adjacency);
  const roles = semanticRoles(
    nodes,
    adjacency,
    anchors.startId,
    anchors.endId,
  );

  if (!nodes.length) {
    return {
      nodes: [],
      edges: [],
      nodeById: new Map(),
      communities: [],
      anchors,
      bounds: { width, height, padding },
      omittedNodeCount: canonicalNodes.length,
    };
  }

  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const maxForwardDepth = Math.max(
    1,
    ...nodes
      .filter((node) => node.direction === 'forward')
      .map((node) => node.depth),
  );
  const maxBackwardDepth = Math.max(
    1,
    ...nodes
      .filter((node) => node.direction === 'backward')
      .map((node) => node.depth),
  );
  const routePositions = nodes
    .map((node) => node.route_position)
    .filter((position): position is number => position !== undefined);
  const maxRoutePosition = Math.max(1, ...routePositions);

  const communitySeedY = new Map<string, number>();
  for (const communityId of new Set(communityIds)) {
    communitySeedY.set(
      communityId,
      padding + usableHeight * (0.16 + hashUnit(communityId) * 0.68),
    );
  }

  const targetX = nodes.map((node, index) => {
    if (node.route_position !== undefined) {
      return padding + usableWidth * (node.route_position / maxRoutePosition);
    }
    if (node.id === anchors.startId) return padding;
    if (node.id === anchors.endId) return width - padding;

    const fromStart = distancesFromStart[index];
    const fromEnd = distancesFromEnd[index];
    if (Number.isFinite(fromStart) && Number.isFinite(fromEnd) && fromStart + fromEnd > EPSILON) {
      return padding + usableWidth * (fromStart / (fromStart + fromEnd));
    }
    if (node.direction === 'forward') {
      return padding + usableWidth * 0.48 * clamp(node.depth / maxForwardDepth, 0, 1);
    }
    if (node.direction === 'backward') {
      return width - padding
        - usableWidth * 0.48 * clamp(node.depth / maxBackwardDepth, 0, 1);
    }
    return padding + usableWidth * (0.25 + hashUnit(node.id) * 0.5);
  });

  const targetY = nodes.map((node, index) => {
    if (roles[index] === 'start' || roles[index] === 'end' || roles[index] === 'route') {
      return height / 2;
    }
    const lane = communitySeedY.get(communityIds[index]) as number;
    const jitter = (hashUnit(`${node.id}:y`) - 0.5) * Math.min(80, usableHeight * 0.26);
    return clamp(lane + jitter, padding, height - padding);
  });

  const x = [...targetX];
  const y = [...targetY];
  for (let index = 0; index < nodes.length; index += 1) {
    const previous = getPreviousPosition(options.previousPositions, nodes[index].id);
    if (previous && Number.isFinite(previous.x) && Number.isFinite(previous.y)) {
      x[index] = clamp(
        previous.x * stability + targetX[index] * (1 - stability),
        padding,
        width - padding,
      );
      y[index] = clamp(
        previous.y * stability + targetY[index] * (1 - stability),
        padding,
        height - padding,
      );
    }
  }

  const velocityX = Array(nodes.length).fill(0) as number[];
  const velocityY = Array(nodes.length).fill(0) as number[];
  const forceX = Array(nodes.length).fill(0) as number[];
  const forceY = Array(nodes.length).fill(0) as number[];

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    forceX.fill(0);
    forceY.fill(0);
    const alpha = 1 - iteration / Math.max(1, iterations);

    for (let index = 0; index < nodes.length; index += 1) {
      const routeLike = roles[index] === 'start'
        || roles[index] === 'end'
        || roles[index] === 'route';
      forceX[index] += (targetX[index] - x[index]) * (routeLike ? 0.13 : 0.035);
      forceY[index] += (targetY[index] - y[index]) * (routeLike ? 0.11 : 0.014);
    }

    for (const link of links) {
      let deltaX = x[link.target] - x[link.source];
      let deltaY = y[link.target] - y[link.source];
      let distance = Math.hypot(deltaX, deltaY);
      if (distance < EPSILON) {
        const angle = hashUnit(`${nodes[link.source].id}:${nodes[link.target].id}`) * Math.PI * 2;
        deltaX = Math.cos(angle);
        deltaY = Math.sin(angle);
        distance = 1;
      }
      const springStrength = (0.006 + link.layoutWeight * 0.014) * alpha;
      const pull = (distance - link.idealLength) * springStrength;
      const pullX = (deltaX / distance) * pull;
      const pullY = (deltaY / distance) * pull;
      forceX[link.source] += pullX;
      forceY[link.source] += pullY;
      forceX[link.target] -= pullX;
      forceY[link.target] -= pullY;
    }

    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        let deltaX = x[right] - x[left];
        let deltaY = y[right] - y[left];
        let distance = Math.hypot(deltaX, deltaY);
        if (distance < EPSILON) {
          const angle = hashUnit(`${nodes[left].id}|${nodes[right].id}`) * Math.PI * 2;
          deltaX = Math.cos(angle);
          deltaY = Math.sin(angle);
          distance = 1;
        }
        if (distance > 64) continue;
        const leftRadius = roles[left] === 'route' ? 8 : 4;
        const rightRadius = roles[right] === 'route' ? 8 : 4;
        const desired = leftRadius + rightRadius + 9;
        const collision = Math.max(0, desired - distance) * 0.09;
        const atmosphere = (1 - distance / 64) * 0.045;
        const push = (collision + atmosphere) * alpha;
        const pushX = (deltaX / distance) * push;
        const pushY = (deltaY / distance) * push;
        forceX[left] -= pushX;
        forceY[left] -= pushY;
        forceX[right] += pushX;
        forceY[right] += pushY;
      }
    }

    for (let index = 0; index < nodes.length; index += 1) {
      velocityX[index] = (velocityX[index] + forceX[index]) * 0.7;
      velocityY[index] = (velocityY[index] + forceY[index]) * 0.7;
      x[index] = clamp(
        x[index] + clamp(velocityX[index], -8, 8),
        padding,
        width - padding,
      );
      y[index] = clamp(
        y[index] + clamp(velocityY[index], -8, 8),
        padding,
        height - padding,
      );
    }

    if (startIndex !== undefined) {
      x[startIndex] = padding;
      y[startIndex] = height / 2;
      velocityX[startIndex] = 0;
      velocityY[startIndex] = 0;
    }
    if (endIndex !== undefined) {
      x[endIndex] = width - padding;
      y[endIndex] = height / 2;
      velocityX[endIndex] = 0;
      velocityY[endIndex] = 0;
    }
  }

  // A short vertical-biased collision sweep keeps dots pickable without
  // destroying the semantic left-to-right distance established above.
  for (let pass = 0; pass < 7; pass += 1) {
    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const deltaX = x[right] - x[left];
        let deltaY = y[right] - y[left];
        const desired = 13
          + (roles[left] === 'route' ? 3 : 0)
          + (roles[right] === 'route' ? 3 : 0);
        const distance = Math.hypot(deltaX, deltaY);
        if (distance >= desired) continue;
        if (Math.abs(deltaY) < EPSILON) {
          deltaY = hashUnit(`${nodes[left].id}:collision`) > 0.5 ? 1 : -1;
        }
        const displacement = (desired - distance) * 0.53;
        const direction = Math.sign(deltaY);
        const leftPinned = left === startIndex || left === endIndex;
        const rightPinned = right === startIndex || right === endIndex;
        if (!leftPinned) {
          y[left] = clamp(y[left] - direction * displacement, padding, height - padding);
        }
        if (!rightPinned) {
          y[right] = clamp(y[right] + direction * displacement, padding, height - padding);
        }
      }
    }
  }

  const layoutNodes: FrogGraphLayoutNode[] = nodes.map((node, index) => {
    const degree = adjacency[index].length;
    const weightedDegree = adjacency[index]
      .reduce((sum, neighbor) => sum + neighbor.weight, 0);
    return {
      ...node,
      x: x[index],
      y: y[index],
      progress: clamp((x[index] - padding) / Math.max(EPSILON, usableWidth), 0, 1),
      role: roles[index],
      communityId: communityIds[index],
      componentId: `component-${components[index] + 1}`,
      degree,
      weightedDegree,
      distanceFromStart: Number.isFinite(distancesFromStart[index])
        ? distancesFromStart[index]
        : null,
      distanceFromEnd: Number.isFinite(distancesFromEnd[index])
        ? distancesFromEnd[index]
        : null,
      isAnchor: index === startIndex || index === endIndex,
      zIndex: roleZIndex(roles[index]),
    };
  });
  const nodeById = new Map(layoutNodes.map((node) => [node.id, node]));
  const outputIndexById = new Map(layoutNodes.map((node, index) => [node.id, index]));
  const linkByPair = new Map(
    links.map((link) => [`${link.source}:${link.target}`, link]),
  );
  const layoutEdges: FrogGraphLayoutEdge[] = visibleEdges.flatMap((edge) => {
    const sourceIndexValue = outputIndexById.get(edge.source);
    const targetIndexValue = outputIndexById.get(edge.target);
    if (sourceIndexValue === undefined || targetIndexValue === undefined) return [];
    const low = Math.min(sourceIndexValue, targetIndexValue);
    const high = Math.max(sourceIndexValue, targetIndexValue);
    const link = linkByPair.get(`${low}:${high}`);
    const source = layoutNodes[sourceIndexValue];
    const target = layoutNodes[targetIndexValue];
    return [{
      ...edge,
      sourceIndex: sourceIndexValue,
      targetIndex: targetIndexValue,
      idealLength: link?.idealLength
        ?? 28 + Math.pow(1 - edge.similarity, 1.2) * 88,
      renderedLength: Math.hypot(target.x - source.x, target.y - source.y),
      sameCommunity: source.communityId === target.communityId,
    }];
  });

  const membersByCommunity = new Map<string, FrogGraphLayoutNode[]>();
  for (const node of layoutNodes) {
    const members = membersByCommunity.get(node.communityId) || [];
    members.push(node);
    membersByCommunity.set(node.communityId, members);
  }
  const communities: FrogGraphCommunity[] = [...membersByCommunity.entries()]
    .map(([id, members]) => ({
      id,
      nodeIds: members.map((node) => node.id).sort(),
      size: members.length,
      centerX: members.reduce((sum, node) => sum + node.x, 0) / members.length,
      centerY: members.reduce((sum, node) => sum + node.y, 0) / members.length,
      dominantDirection: dominantDirection(members),
    }))
    .sort((left, right) => left.centerX - right.centerX || left.id.localeCompare(right.id));

  return {
    nodes: layoutNodes,
    edges: layoutEdges,
    nodeById,
    communities,
    anchors,
    bounds: { width, height, padding },
    omittedNodeCount: canonicalNodes.length - nodes.length,
  };
}

export function layoutFrogExploration(
  exploration: FrogExploration | null | undefined,
  options: FrogGraphLayoutOptions = {},
) {
  return layoutFrogGraph(
    exploration?.nodes || [],
    exploration?.edges || [],
    options,
  );
}
