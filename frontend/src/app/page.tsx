import Link from "next/link";

import { BackendHealth } from "@/components/overview/backend-health";
import { PageHeader } from "@/components/layout/page-header";
import { StatusBadge } from "@/components/ui/status-badge";

const destinations = [
  {
    href: "/graph",
    title: "Flow Graph",
    description: "Inspect observed component relationships when graph data is available.",
    state: "UNAVAILABLE",
  },
  {
    href: "/policies",
    title: "Policies",
    description: "Policy listing and lifecycle depend on the Member 3 policy API.",
    state: "UNAVAILABLE",
  },
  {
    href: "/decisions",
    title: "Decisions",
    description: "Decision history requires a read-only Member 3 history endpoint.",
    state: "UNAVAILABLE",
  },
  {
    href: "/approvals",
    title: "Approvals",
    description: "Approval records are not yet provided by the backend.",
    state: "UNAVAILABLE",
  },
  {
    href: "/operator",
    title: "AI Security Operator",
    description: "The chat route exists, but currently requires identity middleware and provider configuration that are not wired in this app.",
    state: "AUTH REQUIRED",
  },
];

export default function OverviewPage() {
  return (
    <div className="page-content">
      <PageHeader
        title="Runtime overview"
        description="AMGR is the governance control plane for observed multi-agent information flows."
      />

      <BackendHealth />

      <section className="content-section" aria-labelledby="modules-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Dashboard modules</p>
            <h2 id="modules-heading">Runtime and governance data</h2>
          </div>
        </div>
        <div className="module-grid">
          {destinations.map((destination) => (
            <Link className="module-card" href={destination.href} key={destination.href}>
              <div className="module-card-heading">
                <h3>{destination.title}</h3>
                <StatusBadge status={destination.state} />
              </div>
              <p>{destination.description}</p>
              <span className="module-card-link">View module <span aria-hidden="true">→</span></span>
            </Link>
          ))}
        </div>
      </section>

      <section className="content-section" aria-labelledby="integration-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Integration status</p>
            <h2 id="integration-heading">Available backend data</h2>
          </div>
        </div>
        <dl className="integration-list">
          <div className="integration-row">
            <dt>
              <span className="integration-name">API health</span>
              <code>GET /api/v1/health</code>
            </dt>
            <dd>Implemented. Confirms API responsiveness only; it does not imply that data or governance services are available.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Graph, policies, approvals</span>
              <code>GET /graph · GET /policies · GET /approvals</code>
            </dt>
            <dd>Routes are present but currently return HTTP 501. No counts or recent records can be derived.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Decision history</span>
              <code>No history endpoint</code>
            </dt>
            <dd>The authorization POST route currently returns HTTP 501 and is not a decision-history API.</dd>
          </div>
          <div className="integration-row">
            <dt>
              <span className="integration-name">Operator chat</span>
              <code>POST /operator/chat</code>
            </dt>
            <dd>Route contract exists, but this app does not register the required operator identity middleware. Provider configuration is also required.</dd>
          </div>
        </dl>
      </section>

      <section className="scope-note" aria-label="Data availability note">
        <p className="eyebrow">Data integrity</p>
        <p>
          No event counts, decisions, policies, approvals, or risk metrics are shown until
          authoritative backend data is available.
        </p>
      </section>
    </div>
  );
}