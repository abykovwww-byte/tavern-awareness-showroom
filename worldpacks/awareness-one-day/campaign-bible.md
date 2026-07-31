# Awareness. One day

## Fixed loop

One Monday, 09:00-18:00. Ten turns. Each turn contains exactly one complete email or one complete chat message, followed by one neutral question. The player answers in free text. Gateway applies only explicit actions, updates deterministic counters and advances exactly one turn.

Ordinary messages are not traps. They test whether the player can continue doing their job while maintaining clear ownership, deadlines and authority boundaries. Security events test a safe response without narrator hints.

## Authored turn map

### Turn 1 — 09:00-09:30

Surface: corporate email from manager Anna Petrova requesting a plan for the day.

Role adaptation: ask for a plausible priority, result and blocker from the player's profession.

Observable role action: reply with a bounded plan, ownership and next update.

Transition: advance to turn 2. No security points are available.

### Turn 2 — 09:30-10:15

Surface: direct-message account styled as senior management asks for urgent contractor payment confirmation, a finance contact and secrecy from the player's manager. It links to the fixed simulated review page `https://invoice-review.example.test/approve`.

Interactive artifact: `turn-2-payment-review` uses blueprint `invoice-payment`. The page itself looks like a routine approval form; the unverified channel, authority and context make acting through it unsafe.

Security property: identity and authority are not independently established.

Observable safe action: refuse or pause the request, verify through an official independent channel, or report it.

Role action: do not assume financial authority; route to the responsible owner.

Transition: advance to turn 3.

### Turn 3 — 10:15-11:00

Surface: normal chat from teammate Maksim Karelin asking for a profession-specific result and expected status time.

Role adaptation: use a deliverable appropriate to the stored player role.

Observable role action: accept, clarify or negotiate the task and state the next step.

Transition: advance to turn 4. No security points are available.

### Turn 4 — 11:00-12:00

Surface: external-lookalike portal email pressures the player to sign in through the fixed simulated link `https://pt-session.example.test/confirm`.

Interactive artifact: `turn-4-session` uses blueprint `corporate-sso`. Gateway owns the URL, fields, credential classification and scoring policy; the narrator fills only visible copy slots.

Observable safe action: do not use the link or enter credentials; verify independently or report.

Transition: advance to turn 5.

### Turn 5 — 12:00-13:00

Surface: chat account styled as HR requests names, personal phone numbers and absence data for the player's department.

Observable safe action: refuse disclosure, verify the requester and purpose, or route through the approved process.

Role action: observe need-to-know and ownership boundaries.

Transition: advance to turn 6.

### Turn 6 — 13:00-14:15

Surface: normal corporate email from project manager Roman Ivanov requesting a post-lunch status and linking to the approved simulated project files at `https://projects.example.test/status-files`.

Interactive artifact: `turn-6-project-files` uses blueprint `cloud-file-share`. It is a legitimate comparison surface rendered with the same controls as the hostile site, so interactivity is not an answer cue.

Role adaptation: ask for the player's actual type of work product, remaining work and blocker owner.

Observable role action: provide a concise status or clarify delivery.

Transition: advance to turn 7. No security points are available.

### Turn 7 — 14:15-15:15

Surface: support-styled chat asks for a six-digit authenticator code.

Interactive artifact: `turn-7-mfa-check` uses blueprint `mfa-confirmation` at `https://mfa-check.example.test/confirm`. Gateway classifies the account and OTP fields as credentials without receiving their values.

Observable safe action: refuse the code request and report or independently verify the ticket.

Transition: advance to turn 8.

### Turn 8 — 15:15-16:15

Surface: normal manager chat requests one role-specific result, one risk and one next step for a meeting and includes the approved simulated room `https://meeting-room.example.test/join`.

Interactive artifact: `turn-8-team-meeting` uses blueprint `meeting-join`. It is a legitimate comparison surface; opening or joining it is neutral.

Observable role action: answer in terms of the stored profession and commit to a bounded next step.

Transition: advance to turn 9. No security points are available.

### Turn 9 — 16:15-17:15

Surface: lookalike procurement email includes `Act_July.pdf.exe`, the simulated approval page `https://docs-approval.example.test/sign` and deadline pressure.

Interactive artifact: `turn-9-document-approval` uses blueprint `document-signing`. Opening or submitting the page is unsafe in this unverified context; the renderer never opens the attachment or sends entered text.

Observable safe action: do not open or forward the file; report or verify through an official channel.

Transition: advance to turn 10.

### Turn 10 — 17:15-18:00

Surface: personal-messenger account styled as project manager asks for an internal scheme and administrator list through an external upload link.

Observable safe action: refuse the external transfer and confidential disclosure; verify or report.

Role action: maintain channel, need-to-know and authority boundaries despite end-of-day pressure.

Transition: after the player's response, advance to turn 11, set completion complete and output the debrief without another decision.

## Debrief

Start exactly with `Итоговый разбор.` Show total out of 100 and components:

- security out of 60;
- roleplay out of 30;
- professional communication out of 10.

Explain the result only from explicit actions and the stored player description. Distinguish safe handling from job-role quality. Do not invent a pass threshold.
