import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

import { ComponentNode } from "./ComponentNode";

export type GraphCanvasNode = {
  id: string;
  label?: string | null;
  type?: string | null;
  metadata?: Record<string, unknown>;
  component?: {
    component_id?: string;
    component_type?: string;
    name?: string | null;
  } | null;
};

export type GraphCanvasEdge = {
  id: string;
  source: string;
  target: string;
  action?: string | null;
  relationship_type?: string | null;
};

export function FlowGraph({
  edges = [],
  error,
  loading = false,
  nodes = [],
  onRetry,
  onSelectEdge,
  onSelectNode,
  selectedEdgeId,
  selectedNodeId,
  unavailable = false,
}: {
  edges?: GraphCanvasEdge[];
  error?: string | null;
  loading?: boolean;
  nodes?: GraphCanvasNode[];
  onRetry?: () => void;
  onSelectEdge?: (edgeId: string) => void;
  onSelectNode?: (nodeId: string) => void;
  selectedEdgeId?: string | null;
  selectedNodeId?: string | null;
  unavailable?: boolean;
}) {
  if (loading) {
    return <LoadingState message="Loading graph snapshot and related metadata…" />;
  }

  if (error) {
    return <ErrorState message={error} onRetry={onRetry} />;
  }

  if (unavailable || (!nodes.length && !edges.length)) {
    return (
      <EmptyState
        description="No authoritative graph snapshot records are available. Component lineage endpoints exist, but they require a component ID from a usable graph snapshot."
        state="integration"
        title="Graph data unavailable"
      />
    );
  }

  return (
    <section aria-label="Flow graph" className="content-section" style={{ border: "1px solid #dce4df", background: "#fff", padding: 18 }}>
      <div className="section-heading" style={{ marginBottom: 14 }}>
        <div>
          <p className="eyebrow">Observed relationships</p>
          <h2>Runtime graph</h2>
        </div>
      </div>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        {nodes.map((node) => (
          <ComponentNode
            key={node.id}
            node={node}
            onSelect={onSelectNode}
            selected={selectedNodeId === node.id}
          />
        ))}
      </div>
      <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
        {edges.map((edge) => (
          <button
            aria-pressed={selectedEdgeId === edge.id}
            className="secondary-button"
            key={edge.id}
            onClick={() => onSelectEdge?.(edge.id)}
            style={{
              background: selectedEdgeId === edge.id ? "#edf6ef" : "#fff",
              borderColor: selectedEdgeId === edge.id ? "#9bc1a8" : "#dce4df",
              justifyContent: "space-between",
              minHeight: 48,
              padding: "10px 12px",
              textAlign: "left",
            }}
            type="button"
          >
            <span>{edge.action ?? edge.relationship_type ?? "observed interaction"}</span>
            <span style={{ color: "#687570", fontSize: 11 }}>
              {edge.source} → {edge.target}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
