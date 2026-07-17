# Outcome Repair Prompt

Use this only when the model response violates the injected
`<AUTHORITATIVE_OUTCOME>`.

The previous response contradicted the fixed outcome. Regenerate the answer.

Repair requirements:

- Preserve the exact Result from `<AUTHORITATIVE_OUTCOME>`.
- Remove any hidden success, hidden concession, or equivalent compensation that
  is not listed in Consequences.
- Remove any outcome that bypasses Forbidden reinterpretations.
- Keep established state unchanged unless a proposed state patch is later
  reviewed and applied.
- Do not apologize or discuss the repair process in-character.
- Return only corrected narrative prose.

The authoritative block is binding. If the block says the king does not
transfer command, the narration cannot grant command authority, a proxy command,
or another mechanically equivalent benefit.
