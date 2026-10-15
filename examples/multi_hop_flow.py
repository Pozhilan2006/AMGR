"""End-to-end 8-Hop Data-Flow Capture Example for AMGR Phase 1.

Demonstrates runtime instrumentation and causal parent-chain tracking across:
User -> Frontend -> Backend -> Database -> AI Agent -> Child Agent -> Grandchild Agent -> External Tools
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from amgr_sdk import (
    AMGRClient,
    ComponentRef,
    EventRecorder,
    FlowContext,
    FlowEvent,
    Transport,
    capture,
    capture_span,
    flow_context,
    set_default_client,
)


class InMemoryTransport(Transport):
    """Local transport for capturing and inspecting telemetry events in-memory."""

    def __init__(self) -> None:
        self.events: list[FlowEvent] = []

    def send(self, event: FlowEvent) -> None:
        self.events.append(event)

    def send_batch(self, events: Any) -> None:
        self.events.extend(events)


def run_multi_hop_flow() -> list[FlowEvent]:
    # 1. Setup in-memory client for telemetry verification
    transport = InMemoryTransport()
    client = AMGRClient(
        recorder=EventRecorder(
            transport,
            application_id="example-app",
            environment="development",
        )
    )
    set_default_client(client)

    # 2. Define component descriptors for the 8 tiers
    user = ComponentRef(component_id="user-101", component_type="user", name="End User")
    frontend = ComponentRef(component_id="web-dashboard", component_type="frontend", name="Web Dashboard")
    backend = ComponentRef(component_id="core-backend-api", component_type="service", name="FastAPI Backend")
    database = ComponentRef(component_id="user-db", component_type="db", name="Postgres Storage")
    ai_agent = ComponentRef(component_id="supervisor-agent", component_type="agent", name="Supervisor Agent")
    child_agent = ComponentRef(component_id="planner-agent", component_type="agent", name="Planner Agent")
    grandchild_agent = ComponentRef(component_id="worker-agent", component_type="agent", name="Worker Agent")
    external_tool = ComponentRef(component_id="search-api-tool", component_type="tool", name="Web Search Tool")

    # 3. Define instrumented execution functions
    @capture(
        action="tool.execute",
        destination=external_tool,
        data_labels=["query_param", "external_api"],
        client=client,
    )
    def call_external_tool(query: str) -> dict[str, Any]:
        """Hop 8: Grandchild Agent -> External Tools."""
        return {"status": "success", "results": [f"Result for {query}"]}

    @capture(
        action="agent.delegate",
        destination=grandchild_agent,
        data_labels=["task_spec"],
        client=client,
    )
    def run_grandchild_agent(subtask: str) -> dict[str, Any]:
        """Hop 7: Child Agent -> Grandchild Agent."""
        return call_external_tool(subtask)

    @capture(
        action="agent.delegate",
        destination=child_agent,
        data_labels=["agent_plan"],
        client=client,
    )
    def run_child_agent(plan_prompt: str) -> dict[str, Any]:
        """Hop 6: AI Agent -> Child Agent."""
        return run_grandchild_agent(plan_prompt)

    @capture(
        action="agent.invoke",
        destination=ai_agent,
        data_labels=["user_intent", "context"],
        client=client,
    )
    def run_ai_agent(prompt: str) -> dict[str, Any]:
        """Hop 5: Backend -> AI Agent."""
        return run_child_agent(prompt)

    # 4. Initiate the root transaction flow
    with flow_context(component=user) as root_ctx:
        # Hop 1: User -> Frontend
        with capture_span(
            action="user.submit",
            source=user,
            destination=frontend,
            data_labels=["user_input", "untrusted"],
            client=client,
        ):
            # Hop 2: Frontend -> Backend
            with capture_span(
                action="http.request",
                source=frontend,
                destination=backend,
                data_labels=["http_body"],
                client=client,
            ):
                # Hop 3: Backend -> Database
                with capture_span(
                    action="db.query",
                    source=backend,
                    destination=database,
                    data_labels=["sql_query", "session_cache"],
                    client=client,
                ):
                    pass  # Database read/write complete

                # Hop 4: Backend -> AI Agent (and recursive child delegation)
                run_ai_agent("Analyze data and fetch weather")

    return transport.events


if __name__ == "__main__":
    captured = run_multi_hop_flow()
    print(f"\nSuccessfully captured {len(captured)} multi-hop flow events:\n")
    print("-" * 105)
    print(f"{'#':<3} | {'Action':<16} | {'Source -> Destination':<38} | {'Parent Event ID':<36}")
    print("-" * 105)
    for idx, ev in enumerate(captured, 1):
        parent = str(ev.parent_event_id) if ev.parent_event_id else "(root / None)"
        flow = f"{ev.source.component_id} -> {ev.destination.component_id}"
        print(f"{idx:<3} | {ev.action:<16} | {flow:<38} | {parent:<36}")
    print("-" * 105)
    print(f"\nAll events share common Flow ID: {captured[0].flow_id}")
