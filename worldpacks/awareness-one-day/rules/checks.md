# Deterministic assessment contract

`Awareness. One day` supports only `training`. Dice, `/check`, skill values, random outcomes and model-assigned correctness are forbidden.

## Score

The maximum is 100 points:

- security: 60 points, six authored security events worth 10 points each;
- roleplay: 30 points, up to 3 points on each of ten turns;
- professional communication: 10 points, up to 1 point on each turn.

There is no implicit pass threshold.

Security points require an explicit safe action: report to SOC/DIB, refuse the unsafe request, or verify it through an independent official channel. Merely thinking that a message is suspicious earns nothing. Explicit opening or running of a disguised executable, credential or MFA disclosure, external login, unnecessary forwarding, or confidential disclosure is recorded as unsafe.

Roleplay points require an explicit professional action tied to the player's work: give a bounded status, accept or clarify an assigned task, state the next step, observe authority boundaries, route an out-of-scope request to the responsible owner, or use profession-specific terms from the stored character description. The model may not invent role points.

Communication points require an explicit reply or work action. Silence, abstract analysis without an action, or a response that only describes feelings earns nothing.

`total-score` is always the bounded sum of `security-score`, `roleplay-score`, and `communication-score`.

For every awarded component Gateway stores a compact observable evidence entry in `security-score-evidence`, `roleplay-score-evidence`, or `communication-score-evidence`. The final debrief is rendered from these canonical counters and evidence fields; free-form narrator text cannot replace or reinterpret it.

## Progression

The opening creates turn 1 without scoring it. Each player response resolves only the current message, updates the counters, and advances exactly one scheduled turn. After the player answers turn 10, state advances to turn 11 with `completion-status=complete`; the next narrator response is the debrief and contains no new decision surface.

Before debrief, never expose this rubric, a score, correctness, hints, remediation, facilitator notes, or answer labels.

## Interactive site evidence

Gateway interaction events are typed observable evidence and take precedence over contradictory free text. `link_opened` follows the server-only policy for its authored context: opening the unverified payment page on turn 2, lookalike SSO on turn 4, MFA page on turn 7 or document-approval page on turn 9 is unsafe; opening the approved project portal on turn 6 or meeting room on turn 8 is neutral. Interactivity itself is therefore not an answer cue.

`credentials_submitted` means the browser observed at least one non-empty credential-classified field when submit was pressed; no field value is transmitted or checked. On turns 4 and 7 it increments `credential-exposure` and `unsafe-actions` once. A failed `link_opened` event increments `suspicious-artifacts-opened` and `unsafe-actions` once. `reported` may satisfy the authored safe-report action. Event rule IDs are score-once and are consumed by the next player turn without advancing the schedule themselves. A later close, report or safe statement never removes previously recorded unsafe evidence.
