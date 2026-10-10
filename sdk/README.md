# AMGR Python SDK

The SDK currently provides typed flow-event and authorization contracts, flow context, and recorder/transport interfaces. It does not send events, contact an AMGR server, capture framework calls, or enforce authorization decisions yet.

Install from the repository root with `python -m pip install -e ./sdk`. Applications can import the public package with `import amgr_sdk`. API and event contracts are documented in [`../docs/event-schema.md`](../docs/event-schema.md) and [`../docs/api-contracts.md`](../docs/api-contracts.md).

`AMGRConfig.from_env()` reads `AMGR_API_BASE_URL` and `AMGR_TIMEOUT_SECONDS`. The default URL is for local development; no credentials or AI-provider configuration are required.