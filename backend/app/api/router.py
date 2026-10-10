from fastapi import APIRouter

from app.api.routes import approvals, authorization, flow_events, graph, health, policies

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(flow_events.router, prefix="/flow-events", tags=["flow-events"])
api_router.include_router(graph.router, prefix="/graph", tags=["graph"])
api_router.include_router(policies.router, prefix="/policies", tags=["policies"])
api_router.include_router(authorization.router, prefix="/authorization", tags=["authorization"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])