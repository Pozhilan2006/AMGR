import type {
  FlowGraphResponse,
  GraphEdgeRecord,
  GraphNodeRecord,
  GraphSnapshotResponse,
} from "@/lib/types";

export function adaptGraphResponse(
  input: Partial<FlowGraphResponse> | Partial<GraphSnapshotResponse> | null | undefined,
): {
  edges: GraphEdgeRecord[];
  nodes: GraphNodeRecord[];
  truncated: boolean;
  generatedAt: string | null;
} {
  const response = input ?? {};
  const nodes = Array.isArray(response.nodes) ? response.nodes : [];
  const edges = Array.isArray(response.edges) ? response.edges : [];

  return {
    edges: edges.map((edge) => ({
      action: edge.action ?? edge.relationship_type ?? "observed interaction",
      id: edge.id ?? `${edge.source ?? "unknown"}-${edge.target ?? "unknown"}`,
      metadata: edge.metadata ?? {},
      relationship_type: edge.relationship_type ?? edge.action ?? "observed",
      source: edge.source ?? "",
      target: edge.target ?? "",
    })),
    generatedAt: response.generated_at ?? null,
    nodes: nodes.map((node) => ({
      component: node.component ?? {
        component_id: node.id ?? "unknown",
        component_type: node.component_type ?? "unknown",
        name: node.label ?? null,
      },
      component_type: node.component_type ?? node.component?.component_type ?? "unknown",
      id: node.id ?? node.component?.component_id ?? "unknown",
      label: node.label ?? node.component?.name ?? node.component?.component_id ?? "unknown",
      metadata: node.metadata ?? {},
    })),
    truncated: Boolean(response.truncated),
  };
}
