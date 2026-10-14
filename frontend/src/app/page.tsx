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
    description: "The operator API requires authenticated access and provider configuration.",
    state: "UNAVAILABLE",
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