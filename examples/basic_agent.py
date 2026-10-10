from uuid import uuid4

from amgr_sdk import ComponentRef, FlowContext, FlowEvent

context = FlowContext()
event = FlowEvent(
    flow_id=context.flow_id,
    correlation_id=context.correlation_id,
    source=ComponentRef(component_id="agent-1", component_type="agent"),
    destination=ComponentRef(component_id="model-1", component_type="model"),
    action="model.inference",
    metadata={"example": True},
)

print(event.model_dump_json(indent=2))