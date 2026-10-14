import { PageHeader } from "@/components/layout/page-header";
import { GraphPlaceholder } from "@/components/graph/graph-placeholder";

const interfaces = [
  {
    name: "Graph snapshot",
    endpoint: "GET /api/v1/graph",
    status: "Route exists; returns 501 Not Implemented.",
  },
  {
    name: "Component details and neighborhoods",
    endpoint: "No read endpoint is implemented.",
    status: "Member 2 graph persistence and query APIs are required.",
  },
  {
    name: "Relationship event history",
    endpoint: "POST /api/v1/flow-events",
    status: "Ingestion-only route; currently returns 501. No history query is exposed.",
  },
  {
    name: "Upstream and downstream lineage",
    endpoint: "No API route is implemented.",
    status: "LineageService.resolve_lineage is a placeholder that raises NotImplementedError.",
  },
];

export default function FlowGraphPage() {
  return (
    <div className="page-content">
      <PageHeader
        title="Flow graph"
        description="Explore observed component relationships once Member 2's graph and lineage APIs provide authoritative records."
      />

      <GraphPlaceholder />

      <section className="content-section" aria-labelledby="graph-interfaces-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Backend integration</p>
            <h2 id="graph-interfaces-heading">Required interfaces</h2>
          </div>
        </div>
        <dl className="integration-list">
          {interfaces.map((item) => (
            <div className="integration-row" key={item.name}>
              <dt>
                <span className="integration-name">{item.name}</span>
                <code>{item.endpoint}</code>
              </dt>
              <dd>{item.status}</dd>
            </div>
          ))}
        </dl>
      </section>

      <aside className="scope-note" aria-label="Graph evidence limitation">
        <p className="eyebrow">Coverage and evidence</p>
        <p>
          An observed interaction does not by itself establish that specific data content was
          transferred. When available, graph data will represent only records collected by the
          instrumented AMGR environment, not all activity in uninstrumented systems.
        </p>
      </aside>
    </div>
  );
}
