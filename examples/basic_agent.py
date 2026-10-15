"""Basic Agent example demonstrating runtime capture and nested lineage."""

from amgr_sdk import (
    AMGRClient,
    ComponentRef,
    EventRecorder,
    FlowEvent,
    capture,
    flow_context,
)


class LocalCollector:
    """Simple in-memory transport to collect events for demonstration."""

    def __init__(self) -> None:
        self.events: list[FlowEvent] = []

    def send(self, event: FlowEvent) -> None:
        self.events.append(event)

    def send_batch(self, events: list[FlowEvent]) -> None:
        self.events.extend(events)


collector = LocalCollector()
client = AMGRClient(
    recorder=EventRecorder(
        collector,
        application_id="example-app",
        environment="development",
    )
)

# Define components
agent = ComponentRef.agent(component_id="agent-1", name="Research Agent")
tool = ComponentRef.tool(component_id="calculator-tool", name="Calculator Tool")


# Hop 2 (Nested): Agent -> Tool
@capture(action="tool.execute", destination=tool, data_labels=["computation"], client=client)
def calculate_metric(value: int) -> int:
    return value * 2


# Hop 1 (Root): Service -> Agent, which invokes Tool
@capture(
    action="agent.invoke",
    destination=agent,
    source=ComponentRef.service("core-api"),
    data_labels=["query"],
    client=client,
)
def run_agent_workflow(query: str) -> dict[str, int]:
    # Nested tool execution inherits flow_id and has parent_event_id set to this agent run
    result = calculate_metric(21)
    return {"query_len": len(query), "calculated": result}


if __name__ == "__main__":
    with flow_context():
        output = run_agent_workflow("Compute double 21")

    print(f"Workflow result: {output}\n")
    print(f"Captured {len(collector.events)} events with lineage:")
    for ev in collector.events:
        parent = str(ev.parent_event_id) if ev.parent_event_id else "(root / None)"
        print(
            f"  - Action: {ev.action:<14} | {ev.source.component_id} -> {ev.destination.component_id} | "
            f"Parent: {parent} | Duration: {ev.duration_ms}ms"
        )
