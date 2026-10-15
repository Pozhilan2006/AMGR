export interface ComponentRef {
  component_id: string;
  component_type: string;
  name?: string | null;
}

export interface FlowEvent {
  event_id: string;
  application_id: string;
  environment: string;
  flow_id: string;
  correlation_id: string | null;
  parent_event_id: string | null;
  timestamp: string;
  source: ComponentRef;
  destination: ComponentRef;
  action: string;
  data_labels: string[];
  metadata: Record<string, unknown>;
  status: string;
  duration_ms: number | null;
}

export interface GraphNode {
  id: string;
  component: ComponentRef;
  label?: string | null;
  component_type?: string;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  action: string;
  relationship_type?: string | null;
  metadata?: Record<string, unknown>;
}

export interface GraphFilterState {
  componentType?: string;
  flowId?: string;
  start?: string;
  end?: string;
  status?: string;
  dataLabel?: string;
  sourceComponentId?: string;
  destinationComponentId?: string;
}

export interface GraphNodeRecord extends GraphNode {
  component_type?: string;
  display_name?: string | null;
  metadata?: Record<string, unknown>;
  last_seen_at?: string | null;
}

export interface GraphEdgeRecord extends GraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string | null;
  relationship_type?: string | null;
  metadata?: Record<string, unknown>;
}

export interface GraphSnapshotResponse {
  nodes: GraphNodeRecord[];
  edges: GraphEdgeRecord[];
  generated_at?: string | null;
  environment?: string | null;
  truncated?: boolean;
  unavailable?: boolean;
  message?: string | null;
}

export type FlowGraphResponse = GraphSnapshotResponse;

export interface GraphComponentDetail {
  component_id: string;
  component_type: string;
  display_name?: string | null;
  name?: string | null;
  metadata?: Record<string, unknown>;
  last_seen_at?: string | null;
  neighbors?: Array<Record<string, unknown>>;
  unavailable?: boolean;
  message?: string | null;
}

export interface GraphEventHistoryResponse {
  events: FlowEvent[];
  next_cursor?: string | null;
  total?: number;
  unavailable?: boolean;
  message?: string | null;
}

export interface LineageNode {
  node_type: "event" | "component";
  node_id: string;
  label?: string | null;
  component_type?: string | null;
}

export interface LineageEdge {
  source_type: "event" | "component";
  source_id: string;
  target_type: "event" | "component";
  target_id: string;
  relationship_type: "explicit_parent_event" | "observed_interaction";
  evidence_type: "recorded_parent_event_id" | "recorded_event_components";
  event_id: string;
}

export interface GraphLineageResponse {
  events: FlowEvent[];
  nodes: LineageNode[];
  edges: LineageEdge[];
  unresolved_parent_event_ids: string[];
  completeness: "unknown" | "incomplete";
  truncated: boolean;
  truncation_reasons: Array<"depth_limit" | "result_limit" | "page_limit">;
  traversal: {
    direction: "upstream" | "downstream" | "both";
    max_depth: number | null;
    limit: number;
    offset: number;
    next_offset: number | null;
  };
  scope: OperatorScope;
}

export interface Policy {
  policy_id: string;
  name: string;
  description: string | null;
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

export interface Approval {
  approval_id: string;
  decision_id: string;
  status: "pending" | "approved" | "rejected";
  requested_at: string;
}

export interface OperatorScope {
  application_id: string;
  environment: string;
}

export type OperatorReferenceType = "graph_event" | "policy" | "decision";
export type OperatorResponseType = "answer" | "clarification" | "proposal" | "error";

export interface EvidenceReference {
  reference_type: OperatorReferenceType;
  reference_id: string;
}

export type OperatorReference = EvidenceReference;

export interface ProposedAction {
  title: string;
  description: string;
  review_required: true;
}

export interface OperatorChatRequest {
  message: string;
  conversation_id?: string | null;
  scope?: OperatorScope | null;
}

export interface OperatorChatResponse {
  conversation_id: string;
  assistant_message: string;
  response_type: OperatorResponseType;
  references: EvidenceReference[] | null;
  proposed_actions: ProposedAction[] | null;
}

export interface ApiErrorResponse {
  detail: string;
}
