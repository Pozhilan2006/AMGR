# AI Security Operator

## Configuration

The backend uses an OpenAI-compatible Chat Completions provider. Configure these environment variables for the backend process:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_OPERATOR_PROVIDER` | `openai` | Provider adapter name. |
| `AI_OPERATOR_MODEL` | `gpt-4o-mini` | Provider model identifier. |
| `AI_OPERATOR_BASE_URL` | Provider default | Optional OpenAI-compatible API endpoint. |
| `AI_OPERATOR_API_KEY` | Unset | Required credential; keep it in a secret store or local environment, never source control. |
| `AI_OPERATOR_TIMEOUT_SECONDS` | `30` | Provider request timeout, from 0 to 300 seconds. |
| `AI_OPERATOR_MAX_RESPONSE_TOKENS` | `1200` | Maximum generated response tokens, from 1 to 8192. |
| `AI_OPERATOR_TEMPERATURE` | `0.2` | Generation temperature, from 0 to 2. |

The provider client is created only when a chat request is made. The rest of the API remains importable without a key. A missing key or unsupported provider returns a typed error response rather than exposing provider details.

## API

`POST /api/v1/operator/chat`

Request:

```json
{
  "message": "Why was this tool call denied?",
  "conversation_id": null,
  "scope": {
    "application_id": "billing-agent",
    "environment": "production"
  }
}
```

`scope` may be omitted only when the authenticated identity has exactly one authorized scope. The request is rejected if its scope is not listed in the trusted identity. A successful response has a `conversation_id`, `assistant_message`, and `response_type` (`answer`, `clarification`, or `proposal`), with optional structured `references` and `proposed_actions`. All proposed actions require further review. Provider configuration and provider-output errors use `response_type: "error"` with HTTP 503 or 502 respectively.

## Authentication and scope

This starter backend does not currently include authentication middleware. The operator route therefore returns HTTP 401 unless trusted authentication middleware sets `request.state.operator_identity` to an object with this shape:

```json
{
  "subject": "authenticated-principal",
  "authorized_scopes": [
    { "application_id": "billing-agent", "environment": "production" }
  ]
}
```

The request body is never used as the source of authority. Middleware must validate the caller and populate this state from its verified identity/claims, not from untrusted headers or request fields.

## Security and conversation boundaries

- The operator can explain and propose; it cannot authorize operations or activate policies.
- The provider receives only the current user message and the validated application/environment scope. The current implementation does not retrieve graph, policy, or decision data, so it must not claim those as facts or invent references.
- No conversation messages are persisted. `conversation_id` is a correlation ID only and does not grant access or restore history.
- Provider credentials stay in environment configuration and are not sent to the model or included in API errors.
- There are no tools for executing Python, SQL, shell commands, or URLs. Graph queries, policy mutations, and frontend integration remain out of scope.

## Extension points

`AIProvider` isolates model transport from `OperatorService`. `ConversationManager` currently provides stateless IDs; a future storage implementation must bind records to validated scope, apply retention limits, and avoid storing secrets or full sensitive payloads.