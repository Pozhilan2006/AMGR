"use client";

import { ComponentDetailsPanel } from "@/components/graph/ComponentDetailsPanel";
import { FlowGraph } from "@/components/graph/FlowGraph";
import { GraphFilters } from "@/components/graph/GraphFilters";
import { GraphToolbar } from "@/components/graph/GraphToolbar";
import { LineagePanel } from "@/components/graph/LineagePanel";
import { PageHeader } from "@/components/layout/page-header";
import { useFlowGraph } from "@/hooks/useFlowGraph";

export default function FlowGraphPage() {
  const graph = useFlowGraph();

  return (
    <div className="page-content">
      <PageHeader
        title="Flow graph"
        description="Inspect observed component relationships and explicitly recorded event lineage when authoritative backend records are available."
      />

      <GraphToolbar
        disabled={graph.loading || graph.unavailable}
        onFit={() => undefined}
        onRefresh={() => void graph.refresh()}
        onReset={() => {
          graph.setSelectedNodeId(null);
          graph.setSelectedEdgeId(null);
        }}
        onZoomIn={() => undefined}
        onZoomOut={() => undefined}
      />

      <GraphFilters
        available={false}
        filters={graph.filters}
        onChange={(next) => graph.setFilters(next)}
        onReset={() => graph.clearFilters()}
      />

      <FlowGraph
        edges={graph.snapshot?.edges.map((edge) => ({
          action: edge.action,
          id: edge.id,
          relationship_type: edge.relationship_type ?? edge.action,
          source: edge.source,
          target: edge.target,
        })) ?? []}
        error={graph.error}
        loading={graph.loading}
        nodes={graph.snapshot?.nodes.map((node) => ({
          component: node.component,
          id: node.id,
          label: node.label ?? node.component?.name ?? node.component?.component_id,
          metadata: node.metadata,
          type: node.component_type ?? node.component?.component_type ?? "unknown",
        })) ?? []}
        onRetry={() => void graph.refresh()}
        onSelectEdge={(edgeId) => {
          graph.setSelectedEdgeId(edgeId);
          void graph.loadEdgeHistory(edgeId);
        }}
        onSelectNode={(nodeId) => {
          graph.setSelectedNodeId(nodeId);
          void graph.loadComponentDetails(nodeId);
          const componentId = graph.snapshot?.nodes.find((node) => node.id === nodeId)?.component.component_id;
          if (componentId) {
            void graph.loadLineage(componentId, "upstream");
            void graph.loadLineage(componentId, "downstream");
          }
        }}
        selectedEdgeId={graph.selectedEdgeId}
        selectedNodeId={graph.selectedNodeId}
        unavailable={graph.unavailable}
      />

      <div style={{ display: "grid", gap: 24, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <ComponentDetailsPanel component={graph.component ?? null} />
        <LineagePanel
          componentId={graph.snapshot?.nodes.find((node) => node.id === graph.selectedNodeId)?.component.component_id}
          direction="upstream"
          error={graph.lineages.upstream.error}
          lineage={graph.lineages.upstream.data}
          loading={graph.lineages.upstream.loading}
        />
        <LineagePanel
          componentId={graph.snapshot?.nodes.find((node) => node.id === graph.selectedNodeId)?.component.component_id}
          direction="downstream"
          error={graph.lineages.downstream.error}
          lineage={graph.lineages.downstream.data}
          loading={graph.lineages.downstream.loading}
        />
      </div>

      <section className="content-section" aria-labelledby="graph-interfaces-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Backend integration</p>
            <h2 id="graph-interfaces-heading">Required interfaces</h2>
          </div>
        </div>
        <dl className="integration-list">
          <div className="integration-row">
            <dt>
              <span className="integration-name">Graph snapshot</span>
              <code>GET /api/v1/graph</code>
            </dt>
            <dd>Route exists but currently returns 501 Not Implemented until Member 2 persists graph data.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Component details and neighborhoods</span>
              <code>No read-only route</code>
            </dt>
            <dd>Member 2 must implement component and neighborhood query contracts before metadata can be shown.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Relationship event history</span>
              <code>POST /api/v1/flow-events</code>
            </dt>
            <dd>Scoped event ingestion now persists records. The API does not yet provide a generic edge-history query.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Upstream and downstream lineage</span>
              <code>GET /api/v1/lineage/components/&#123;component_id&#125;</code>
            </dt>
            <dd>Returns one-hop recorded component interactions and explicit parent references. Results require authenticated scope and report pagination and unresolved evidence.</dd>
          </div>
        </dl>
      </section>

      <aside className="scope-note" aria-label="Graph evidence limitation">
        <p className="eyebrow">Coverage and evidence</p>
        <p>
          An observed interaction does not establish that specific data content was transferred.
          Graph records represent only what the instrumented AMGR environment has actually collected.
        </p>
      </aside>
    </div>
  );
}
