import Link from "next/link";

import type { OperatorReference } from "@/lib/types";

export function EvidenceReferences({ references }: { references: OperatorReference[] }) {
  return (
    <div className="info-card">
      <div className="card-header">
        <p className="eyebrow">Evidence references</p>
        <span className="card-count">{references.length}</span>
      </div>
      <ul className="reference-list">
        {references.map((reference, index) => {
          const href = getReferenceHref(reference.reference_type, reference.reference_id);

          return (
            <li key={`${reference.reference_type}-${reference.reference_id}-${index}`}>
              <div className="reference-summary">
                <span className="reference-type">{reference.reference_type}</span>
                <code>{reference.reference_id}</code>
              </div>
              {href ? (
                <Link className="inline-link" href={href}>
                  Open linked record
                </Link>
              ) : (
                <span className="muted-text">Unresolved in this frontend</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function getReferenceHref(referenceType: string, referenceId: string) {
  const normalized = referenceId.trim();
  if (!normalized) return null;

  if (referenceType === "policy") {
    return `/policies/${encodeURIComponent(normalized)}`;
  }

  return null;
}
