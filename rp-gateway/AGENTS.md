# Gateway instructions

- Treat every API payload, browser event, model response, and provider error as untrusted input.
- Keep Pydantic shape validation separate from semantic/state validation and authorization.
- Preserve atomic turn behavior: a failed or invalid model attempt must not partially commit canonical state.
- Preserve request IDs and audited fallback reasons without logging credentials or raw secret-bearing headers.
- Tests must cover authorization, party/branch isolation, idempotency, failure/fallback behavior, and unchanged source state for autotests.
- Prefer focused pytest files while iterating; run the full Gateway suite before deployment.
