import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";
import type { GraphLineageResponse } from "@/lib/types";

export function LineagePanel({
  componentId,
  direction,
  error,
  lineage,
  loading = false,
}: {
  componentId?: string | null;
  direction: "upstream" | "downstream";
  error?: string | null;
  lineage?: GraphLineageResponse | null;
  loading?: boolean;
}) {
  if (!componentId) {
    return (
      <EmptyState
        description="Select a component from an available graph snapshot to query its recorded lineage."
        state="integration"
        title="No component selected"
      />
    );
  }

  if (loading) {
    return <LoadingState message={`Loading ${direction} recorded lineage…`} />;
  }

  if (error) {
    return <ErrorState message={error} />;
  }

  if (!lineage || lineage.events.length === 0) {
    return (
      <EmptyState
        description="No matching event records were returned for this component and authorized scope. This does not establish that no other interactions occurred."
        state="no-records"
        title={`No ${direction} event records`}
      />
    );
  }

  return (
    <section className="content-section" aria-label={`${direction} lineage details`}>
      <div className="section-heading" style={{ marginBottom: 8 }}>
        <div>
          <p className="eyebrow">{direction} recorded interactions</p>
          <h2>Event lineage</h2>
        </div>
        <StatusBadge status={lineage.completeness} />
      </div>
      {lineage.truncated ? (
        <p className="message-note">
          Results were truncated: {lineage.truncation_reasons.join(", ")}.
        </p>
      ) : null}
      {lineage.unresolved_parent_event_ids.length ? (
        <p className="message-note">
          Parent references were not resolved in this result:{" "}
          {lineage.unresolved_parent_event_ids.join(", ")}
        </p>
      ) : null}
      <p className="message-note">
        Observed component interactions indicate recorded endpoints, not proof of data propagation.
        Labels below are preserved as recorded; their input/output direction is not defined.
      </p>
      <p className="muted-text">
        Authorized scope: {lineage.scope.application_id} / {lineage.scope.environment}
      </p>
      <ul className="lineage-event-list">
        {lineage.events.map((event) => (
          <li key={event.event_id}>
            <div className="lineage-event-header">
              <strong>{event.action}</strong>
              <StatusBadge status={event.status} />
            </div>
            <p>
              {event.source.name || event.source.component_id} →{" "}
              {event.destination.name || event.destination.component_id}
            </p>
            <dl>
              <dt>Event</dt>
              <dd><code>{event.event_id}</code></dd>
              <dt>Observed</dt>
              <dd>{new Date(event.timestamp).toLocaleString()}</dd>
              <dt>Parent event</dt>
              <dd>{event.parent_event_id || "Not recorded"}</dd>
              <dt>Recorded labels</dt>
              <dd>{event.data_labels.length ? event.data_labels.join(", ") : "None recorded"}</dd>
              <dt>Flow / correlation</dt>
              <dd><code>{event.flow_id}</code> / {event.correlation_id || "Not recorded"}</dd>
            </dl>
            {Object.keys(event.metadata).length ? (
              <details>
                <summary>Raw recorded metadata</summary>
                <pre>{JSON.stringify(event.metadata, null, 2)}</pre>
              </details>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="lineage-edge-list">
        {lineage.edges.map((edge, index) => (
          <p key={`${edge.event_id}-${edge.relationship_type}-${index}`}>
            {edge.relationship_type.replaceAll("_", " ")} — evidence: {edge.evidence_type.replaceAll("_", " ")} (event {edge.event_id})
          </p>
        ))}
      </div>
    </section>
  );
}
