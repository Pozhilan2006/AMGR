"use client";

import type { Approval } from "@/lib/types";

export function ApprovalActions({ approval }: { approval: Approval }) {
  return <section className="content-section" aria-label="Reviewer actions">
    <h2>Reviewer decision</h2>
    <p role="status">Approve and reject actions are unavailable because the backend exposes no decision endpoints. Current state: {approval.status}.</p>
    <button className="primary-button" type="button" disabled>Approve</button>{" "}
    <button className="secondary-button" type="button" disabled>Reject</button>
  </section>;
}
