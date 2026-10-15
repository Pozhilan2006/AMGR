# AMGR frontend

The frontend is a Next.js 15 dashboard. It uses the centralized request client in
`src/lib/api.ts`; it does not contain mock backend records or security decisions.

## Installation and startup

Use npm, as indicated by `package-lock.json`:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

The development server runs at `http://localhost:3000`. The backend's development
port is `8000`.

## Environment setup

Set `NEXT_PUBLIC_AMGR_API_URL` to the backend API base URL, including its API
prefix. For the repository's local backend defaults, `frontend/.env.example`
provides:

```dotenv
NEXT_PUBLIC_AMGR_API_URL=http://localhost:8000/api/v1
```

This is a development-only URL, not a production fallback. Set the public API
URL to the deployed backend or same-origin API proxy before building the
frontend for production. Next.js embeds `NEXT_PUBLIC_` values in browser code,
so configure the variable in the environment used for `npm run build`. Never
put credentials, provider keys, or other secrets in this variable.

`NEXT_PUBLIC_API_BASE_URL` remains a compatibility fallback for existing
deployments, but `NEXT_PUBLIC_AMGR_API_URL` takes precedence. No frontend token
or cookie scheme is defined by this repository. The operator route expects a
trusted identity in backend request state; the current backend application
does not register middleware that provides that identity. The backend also
does not configure CORS. For cross-origin deployments, configure the supported
authentication and CORS behavior at the backend or use an appropriately
configured same-origin proxy; neither is implemented by this frontend.
The current FastAPI app does not install identity middleware, so scoped event
ingestion and lineage requests return 401 until trusted middleware supplies an
authorized identity. When an identity has multiple authorized scopes, the
graph page currently has no scope picker and the backend requires explicit
scope parameters.

Before using event ingestion or lineage in a deployment, apply the database
migration from `backend` with `alembic upgrade head`.

## Pages

| Page | Current behavior |
| --- | --- |
| `/` | Checks the backend health endpoint and identifies backend module availability. No fabricated counts or recent activity are shown. |
| `/graph` | Graph snapshot and neighborhood remain unavailable. Selected graph nodes query the implemented, scoped lineage API when a snapshot provides stable component IDs. |
| `/policies` | Requests the policy list; the backend currently returns 501. Policy detail route is also unavailable. |
| `/decisions` | Read-only decisions view is prepared, but there is no history endpoint. The authorization POST route currently returns 501. |
| `/approvals` | Requests the approval queue; the backend currently returns 501. Detail and review operations are not available. |
| `/operator` | Sends messages to the backend operator chat route and renders its defined response schema. The route cannot currently authenticate because identity middleware is not registered. Provider configuration is required as well. |

The `/flow-graph` and `/security-operator` compatibility URLs redirect to
`/graph` and `/operator`.

## Backend endpoint inventory

The backend mounts these routes under its configured API prefix (default
`/api/v1`):

| Method and path | Status | Frontend use and limitation |
| --- | --- | --- |
| `GET /health` | Implemented; `{ "status": "ok" }` | Overview connectivity check only; this is not a data-service health aggregate. |
| `GET /graph` | Returns 501 | Graph page retains an unavailable state. No filtering, paging, or related query endpoints are defined. |
| `POST /flow-events` | Persists scoped events; requires authenticated identity scope and migrated database | Used by instrumenters, not directly by dashboard pages. |
| `GET /lineage/events/{event_id}` | Implemented; bounded explicit parent/child traversal; requires authenticated scope | Available API; no event detail UI exists. |
| `GET /lineage/flows/{flow_id}` | Implemented; offset-paginated event records; requires authenticated scope | Available API; no flow-history page exists. |
| `GET /lineage/components/{component_id}` | Implemented; one-hop observed interactions; requires authenticated scope | Called by graph lineage panels after selecting a backend graph node. |
| `GET /policies` | Returns 501 | Policy queue remains unavailable. No detail, create, update, or version-history routes are implemented. |
| `POST /authorization/decisions` | Returns 501 | Authorization request route, not read-only decision history. |
| `GET /approvals` | Returns 501 | Approval queue remains unavailable. No detail, pagination, filtering, or reviewer action routes exist. |
| `POST /operator/chat` | Route/schema implemented; currently requires unavailable identity middleware and configured AI provider | The operator page sends typed request/response data through the shared API client. The backend provides no history retrieval or streaming endpoint. |

`GET /api/v1/operator/chat` is not a supported route. Policy detail, approval
detail, decision history, component details, and generic relationship-history
queries remain unavailable. The corresponding frontend detail pages show
integration-pending states or leave references unresolved.

## Cross-module navigation and limits

- Backend-provided policy identifiers link to `/policies/[id]`; the page
  transparently reports that the backend detail route is missing.
- Decision policy references can link to policy detail. The frontend does not
  create a decision detail URL because no such page or retrieval API exists.
- Graph event and decision evidence references are displayed but not linked:
  there is no event-detail route or decision detail route. The separate lineage
  API can query an event's context when given its ID.
- Approval identifiers can navigate to the existing approval detail page, but
  that page reports the missing backend detail contract.
- No relationships are inferred from names, assistant prose, or display order.

The backend currently provides no aggregate metrics, graph snapshot, generic
filters, durable operator conversation history, approval lifecycle state,
policy activation state, or operator impact-analysis fields. Lineage supports
bounded event traversal and flow/component pagination. The dashboard does not
estimate or synthesize unsupported values.

## Build and deployment

For a production build, set `NEXT_PUBLIC_AMGR_API_URL` in the build environment
and run:

```powershell
npm run build
npm run start
```

`npm run start` serves the generated build on port 3000 by default. Production
deployment, API proxying, authentication, CORS, and backend availability must
be configured separately; this repository does not claim deployment has
occurred. The current `frontend/Dockerfile` starts the Next.js development
server and is not a production image.
