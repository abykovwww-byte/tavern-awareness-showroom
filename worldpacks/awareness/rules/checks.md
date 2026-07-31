# Resolution Rules: Awareness

Awareness is authored for the `training` scenario type. It has no D20 rolls, skill values, difficulty classes, random success, or `/check` commands. The Gateway evaluates only the actions explicitly stated by the player and advances exactly one scheduled half-day after each response.

## Deterministic Scoring

Positive state updates:

- `safe-escalations` +1 for a correct report through the company process.
- `reporting-quality` +1 for a factual report containing sender, channel, time, subject, attachment or link, and request details without unnecessary redistribution.
- `awareness-score` +1..+3 for a safe response under realistic pressure.

Negative state updates:

- `unsafe-actions` +1 for an unsafe choice.
- `unnecessary-forwarding` +1 for forwarding suspicious content to colleagues, personal contacts, or group chats.
- `suspicious-artifacts-opened` +1 for opening, downloading, or running a suspicious file.
- `credential-exposure` +1 for disclosing a password, verification code, account, token, or MFA value.
- `confidential-disclosures` +1 for revealing company structure, systems, clients, administrators, or internal procedures.
- `awareness-score` -2..-5 for unsafe actions, with the largest penalty for credential disclosure and suspicious attachments.

## Hard Outcome Rules

- An explicitly disclosed password, code, or account is unsafe; no random result may reinterpret it as safe.
- Opening a suspicious file is an error even if the player reacts correctly afterward.
- Opening a malicious attachment produces no immediate visible reaction; the hidden consequence is recorded only in state.
- Forwarding suspicious content to a shared or personal chat records the additional distribution risk.
- Refusing an unsafe action and reporting through the company process is safe even if the message later proves benign.
- Sharing only safe, generalized information and pointing to an official channel is not a confidential disclosure.
- Do not reveal scoring, rule names, attack labels, or the correctness of a choice during turns 1-10.
- After the player answers turn 10, a separate debrief response is the only place for the final assessment, evidence-based score, mistakes, strengths, and recommendations.

## Interactive Site Evidence

- Authored site surfaces exist only on turns 1, 3, 5, 7, 8, and 9; other turns must not gain an ad-hoc site.
- Turns 1 (`cloud-file-share`) and 3 (`hr-survey`) are legitimate neutral surfaces. Opening or submitting them is not an unsafe action.
- Turns 5 (`corporate-sso`), 7 (`mfa-confirmation`), 8 (`support-download`), and 9 (`document-signing`) are risky surfaces. Their server-authored policy, not narrator prose, determines fail/pass/neutral evidence.
- Typed UI evidence wins over contradictory free text and is consumed atomically by the next player turn.
- Each `score_rule_id` applies at most once. Duplicate client events are idempotent.
- The browser sends only artifact identity, revision, event type, and filled field identifiers; field values never cross the event API and are never persisted.
- Do not expose the interaction policy, `score_rule_id`, result, correctness, or remediation before the final debrief.
