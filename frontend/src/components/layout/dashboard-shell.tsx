"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

const navigation = [
  { label: "Overview", href: "/", glyph: "OV" },
  { label: "Flow Graph", href: "/graph", glyph: "FG", aliases: ["/flow-graph"] },
  { label: "Policies", href: "/policies", glyph: "PO" },
  { label: "Decisions", href: "/decisions", glyph: "DE" },
  { label: "Approvals", href: "/approvals", glyph: "AP" },
  {
    label: "AI Security Operator",
    href: "/operator",
    glyph: "AI",
    aliases: ["/security-operator"],
  },
];

export function DashboardShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className={`sidebar${menuOpen ? " sidebar-open" : ""}`}>
        <Link className="brand" href="/" aria-label="AMGR overview">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>
            <span className="brand-name">AMGR</span>
            <span className="brand-caption">Runtime governance</span>
          </span>
        </Link>

        <p className="nav-label">Control plane</p>
        <nav className="primary-nav" id="primary-navigation" aria-label="Primary navigation">
          {navigation.map((item) => {
            const active =
              pathname === item.href ||
              pathname.startsWith(`${item.href}/`) ||
              (item.aliases?.some(
                (alias) => pathname === alias || pathname.startsWith(`${alias}/`),
              ) ??
                false);
            return (
              <Link
                key={item.href}
                className="nav-link"
                href={item.href}
                aria-current={active ? "page" : undefined}
                onClick={() => setMenuOpen(false)}
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
          <button
            aria-controls="primary-navigation"
            aria-expanded={menuOpen}
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            className="menu-toggle"
            onClick={() => setMenuOpen((open) => !open)}
            type="button"
          >
            <span aria-hidden="true">{menuOpen ? "×" : "☰"}</span>
          </button>
          <span className="breadcrumb">Autonomous Multi-Agent Governance Runtime</span>
          <span className="topbar-label">Development</span>
        </header>
        <main id="main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}