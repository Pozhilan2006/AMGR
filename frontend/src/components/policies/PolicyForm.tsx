"use client";

import type { Policy } from "@/lib/types";

/** Read-only contract-aware form shell. Creation remains disabled until the backend exposes POST /policies and a schema. */
export function PolicyForm({ policy, onCancel }: { policy?: Policy; onCancel?: () => void }) {
  return <section className="content-section" aria-label={policy ? "Edit policy" : "Create policy"}>
    <h2>{policy ? "Edit policy" : "Create policy"}</h2>
    <p role="status">Policy creation and editing are unavailable: the backend currently exposes no policy mutation route or write schema.</p>
    {onCancel ? <button className="secondary-button" type="button" onClick={onCancel}>Close</button> : null}
  </section>;
}
