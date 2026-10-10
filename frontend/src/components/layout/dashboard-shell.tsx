"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const navigation = [
  { label: "Overview", href: "/", glyph: "OV" },
  { label: "Flow Graph", href: "/flow-graph", glyph: "FG" },
  { label: "Policies", href: "/policies", glyph: "PO" },
  { label: "Decisions", href: "/decisions", glyph: "DE" },
  { label: "Approvals", href: "/approvals", glyph: "AP" },
  { label: "AI Security Operator", href: "/security-operator", glyph: "AI" },
];

export function DashboardShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="AMGR overview">
          <span className="brand-mark">A</span>
          <span>
            <span className="brand-name">AMGR</span>
            <span className="brand-caption">Runtime governance</span>
          </span>
        </Link>

        <p className="nav-label">Control plane</p>
        <nav className="primary-nav" aria-label="Primary navigation">
          {navigation.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                className="nav-link"
                href={item.href}
                aria-current={active ? "page" : undefined}
              >
                <span className="nav-glyph" aria-hidden="true">{item.glyph}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div>AMGR control plane</div>
          <div>Development scaffold · v0.1</div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <span className="breadcrumb">Autonomous Multi-Agent Governance Runtime</span>
          <span className="topbar-label">Development</span>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}