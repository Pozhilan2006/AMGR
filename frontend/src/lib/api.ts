import type {
  Approval,
  AuthorizationDecision,
  FlowGraphResponse,
  GraphComponentDetail,
  GraphEventHistoryResponse,
  GraphFilterState,
  GraphLineageResponse,
  OperatorChatRequest,
  OperatorChatResponse,
  Policy,
} from "@/lib/types";

const REQUEST_TIMEOUT_MS = 10_000;

export type ApiErrorKind =
  | "configuration"
  | "unavailable"
  | "network"
  | "timeout"
  | "http"
  | "response";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get userMessage(): string {
    if (this.kind === "configuration") {
      return "Set NEXT_PUBLIC_AMGR_API_URL to the AMGR API base URL to connect the dashboard.";
    }
    if (this.kind === "unavailable") {
      return "The AMGR backend does not expose this read-only data interface yet.";
    }
    if (this.kind === "timeout") {
      return "The AMGR API did not respond in time. Check the connection and try again.";
    }
    if (this.kind === "network") {
      return "The AMGR API could not be reached. Check the configured API URL and network connection.";
    }
    if (this.kind === "http" && this.status === 501) {
      return "This AMGR endpoint is not implemented yet.";
    }
    if (this.kind === "http" && this.status === 401) {
      return "Authentication is required to access this AMGR endpoint.";
    }
    if (this.kind === "http" && this.status === 403) {
      return "You are not authorized to access this AMGR resource.";
    }
    if (this.kind === "http") {
      return `The AMGR API returned an HTTP ${this.status ?? "error"} response.`;
    }
    return "The AMGR API returned a response the dashboard could not read.";
  }
}

function getBaseUrl(): URL {
  const configuredUrl =
    process.env.NEXT_PUBLIC_AMGR_API_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!configuredUrl?.trim()) {
    throw new ApiError("AMGR API URL is not configured.", "configuration");
  }

  let url: URL;
  try {
    url = new URL(configuredUrl.trim());
  } catch {
    throw new ApiError("AMGR API URL is invalid.", "configuration");
  }

  if (
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new ApiError("AMGR API URL is invalid.", "configuration");
  }

  url.pathname = `${url.pathname.replace(/\/+$/, "")}/`;
  return url;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const baseUrl = getBaseUrl();
  const normalizedPath = path.replace(/^\/+/, "");
  const url = new URL(normalizedPath, baseUrl);
  const controller = new AbortController();
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  const abortFromCaller = () => controller.abort();

  if (init.signal?.aborted) {
    controller.abort();
  } else {
    init.signal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers,
    });

    if (!response.ok) {
      throw new ApiError(`AMGR API request failed with HTTP ${response.status}.`, "http", response.status);
    }
    if (response.status === 204) return undefined as T;

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().includes("application/json")) {
      throw new ApiError("AMGR API returned a non-JSON response.", "response");
    }

    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError("AMGR API returned invalid JSON.", "response");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) throw new ApiError("AMGR API request timed out.", "timeout");
    if (init.signal?.aborted) throw error;
    throw new ApiError("AMGR API request could not reach the server.", "network");
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export function getHealth(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>("/health");
}

export function getFlowGraph(): Promise<FlowGraphResponse> {
  return apiFetch<FlowGraphResponse>("/graph");
}

export function getGraphNeighborhood(
  componentId: string,
  params: Partial<GraphFilterState> = {},
): Promise<GraphComponentDetail> {
  const query = new URLSearchParams();
  if (componentId) query.set("component_id", componentId);
  if (params.componentType) query.set("component_type", params.componentType);
  if (params.flowId) query.set("flow_id", params.flowId);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);

  throw new ApiError(
    "AMGR graph neighborhood is not implemented yet by Member 2.",
    "unavailable",
  );
}

export function getGraphEvents(
  params: Partial<GraphFilterState> & { edge_id?: string } = {},
): Promise<GraphEventHistoryResponse> {
  if (params.edge_id) {
    void params;
  }
  throw new ApiError(
    "AMGR relationship event-history queries are not exposed yet.",
    "unavailable",
  );
}

export function getLineage(
  params: {
    component_id: string;
    direction: "upstream" | "downstream" | "both";
    limit?: number;
    offset?: number;
    application_id?: string;
    environment?: string;
  },
): Promise<GraphLineageResponse> {
  const query = new URLSearchParams();
  query.set("direction", params.direction);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.application_id) query.set("application_id", params.application_id);
  if (params.environment) query.set("environment", params.environment);
  return apiFetch<GraphLineageResponse>(
    `/lineage/components/${encodeURIComponent(params.component_id)}?${query.toString()}`,
  );
}

export function getEventLineage(params: {
  event_id: string;
  direction?: "upstream" | "downstream" | "both";
  max_depth?: number;
  limit?: number;
  application_id?: string;
  environment?: string;
}): Promise<GraphLineageResponse> {
  const query = new URLSearchParams();
  if (params.direction) query.set("direction", params.direction);
  if (params.max_depth !== undefined) query.set("max_depth", String(params.max_depth));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.application_id) query.set("application_id", params.application_id);
  if (params.environment) query.set("environment", params.environment);
  return apiFetch<GraphLineageResponse>(
    `/lineage/events/${encodeURIComponent(params.event_id)}?${query.toString()}`,
  );
}

export function getFlowLineage(params: {
  flow_id: string;
  limit?: number;
  offset?: number;
  application_id?: string;
  environment?: string;
}): Promise<GraphLineageResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.application_id) query.set("application_id", params.application_id);
  if (params.environment) query.set("environment", params.environment);
  return apiFetch<GraphLineageResponse>(
    `/lineage/flows/${encodeURIComponent(params.flow_id)}?${query.toString()}`,
  );
}

export function getPolicies(): Promise<Policy[]> {
  return apiFetch<Policy[]>("/policies");
}

export function getApprovals(): Promise<Approval[]> {
  return apiFetch<Approval[]>("/approvals");
}

export async function getApproval(approvalId: string): Promise<Approval> {
  void approvalId;
  throw new ApiError("AMGR approval detail route is not implemented by Member 3.", "unavailable");
}

export async function getDecisionHistory(): Promise<AuthorizationDecision[]> {
  throw new ApiError(
    "AMGR decision history has no read-only API route.",
    "unavailable",
  );
}

export async function getPolicy(policyId: string): Promise<Policy> {
  void policyId;
  throw new ApiError("AMGR policy detail route is not implemented by Member 3.", "unavailable");
}

export function sendOperatorMessage(
  request: OperatorChatRequest,
): Promise<OperatorChatResponse> {
  return apiFetch<OperatorChatResponse>("/operator/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
