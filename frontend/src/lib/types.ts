export interface ComponentRef {
  component_id: string;
  component_type: string;
  name?: string | null;
}

export interface FlowEvent {
  event_id: string;
  flow_id: string;
  correlation_id?: string | null;
  parent_event_id?: string | null;
  timestamp: string;
  source: ComponentRef;
  destination: ComponentRef;
  action: string;
  data_labels: string[];
  metadata: Record<string, unknown>;
  status: string;
  duration_ms?: number | null;
}

export interface GraphNode {
  id: string;
  component: ComponentRef;
  label?: string | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  action: string;
}

export interface Policy {
  policy_id: string;
  name: string;
  description?: string | null;
  enabled: boolean;
  effect: "allow" | "deny" | "require_approval";
  conditions: Record<string, unknown>;
}

export interface AuthorizationDecision {
  decision_id: string;
  request_id: string;
  outcome: "allow" | "deny" | "review";
  reason: string;
  policy_ids: string[];
  evaluated_at: string;
}