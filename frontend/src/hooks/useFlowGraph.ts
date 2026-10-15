"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, getFlowGraph, getGraphEvents, getGraphNeighborhood, getLineage } from "@/lib/api";
import type {
  GraphComponentDetail,
  GraphEventHistoryResponse,
  GraphFilterState,
  GraphLineageResponse,
  GraphSnapshotResponse,
} from "@/lib/types";

export function useFlowGraph(initialFilters: GraphFilterState = {}) {
  const [filters, setFilters] = useState<GraphFilterState>(initialFilters);
  const [snapshot, setSnapshot] = useState<GraphSnapshotResponse | null>(null);
  const [component, setComponent] = useState<GraphComponentDetail | null>(null);
  const [lineages, setLineages] = useState<
    Record<"upstream" | "downstream", { data: GraphLineageResponse | null; error: string | null; loading: boolean }>
  >({
    upstream: { data: null, error: null, loading: false },
    downstream: { data: null, error: null, loading: false },
  });
  const [eventHistory, setEventHistory] = useState<GraphEventHistoryResponse | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextSnapshot = await getFlowGraph();
      setSnapshot(nextSnapshot);
      setUnavailable(false);
      setComponent(null);
      setLineages({
        upstream: { data: null, error: null, loading: false },
        downstream: { data: null, error: null, loading: false },
      });
      setEventHistory(null);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "Unable to load graph data.";
      setError(message);
      setUnavailable(true);
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectedNode = useMemo(
    () => snapshot?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [selectedNodeId, snapshot],
  );

  const selectedEdge = useMemo(
    () => snapshot?.edges.find((edge) => edge.id === selectedEdgeId) ?? null,
    [selectedEdgeId, snapshot],
  );

  const loadComponentDetails = useCallback(async (nodeId: string) => {
    setSelectedNodeId(nodeId);
    try {
      const detail = await getGraphNeighborhood(nodeId, filters);
      setComponent(detail);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "Component detail is unavailable.";
      setComponent({
        component_id: nodeId,
        component_type: "unknown",
        metadata: {},
        name: nodeId,
        unavailable: true,
        message,
      });
    }
  }, [filters]);

  const loadEdgeHistory = useCallback(async (edgeId: string) => {
    setSelectedEdgeId(edgeId);
    try {
      const history = await getGraphEvents({ edge_id: edgeId, ...filters });
      setEventHistory(history);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "Event history is unavailable.";
      setEventHistory({ events: [], message, unavailable: true });
    }
  }, [filters]);

  const loadLineage = useCallback(async (componentId: string, direction: "upstream" | "downstream") => {
    setLineages((current) => ({
      ...current,
      [direction]: { data: null, error: null, loading: true },
    }));
    try {
      const nextLineage = await getLineage({ component_id: componentId, direction, limit: 10 });
      setLineages((current) => ({
        ...current,
        [direction]: { data: nextLineage, error: null, loading: false },
      }));
    } catch (caughtError) {
      const message = caughtError instanceof ApiError
        ? caughtError.userMessage
        : "Lineage records are unavailable.";
      setLineages((current) => ({
        ...current,
        [direction]: { data: null, error: message, loading: false },
      }));
    }
  }, []);

  const clearFilters = useCallback(() => setFilters({}), []);

  return {
    clearFilters,
    component,
    error,
    eventHistory,
    filters,
    lineages,
    loadComponentDetails,
    loadEdgeHistory,
    loadLineage,
    loading,
    refresh,
    selectedEdge,
    selectedEdgeId,
    selectedNode,
    selectedNodeId,
    setFilters,
    setSelectedEdgeId,
    setSelectedNodeId,
    snapshot,
    unavailable,
  };
}
