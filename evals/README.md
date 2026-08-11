# RP Stack evals

The eval gate has three deliberately separate layers.

## 1. Offline

Runs deterministic schemas, WorldPack runtime validation, workflow scripts, the full Gateway pytest suite, browser-client syntax checks, JavaScript tests, and semantic acceptance against saved service responses. It never starts an application server or calls a model provider.

From the repository root:

```powershell
powershell.exe -File scripts/run-rp-stack-evals.ps1 -Mode Offline
powershell.exe -File scripts/run-rp-stack-evals.ps1 -Mode SemanticAcceptance
```

The frozen user-owned oracle is `acceptance/manifest.yml` plus
`acceptance/corpus/**`. The evaluator reads every threshold from the manifest,
keeps all six metrics and per-event-class results separate, fails an all-empty
answer on recall, and rejects generated expectations.

## 2. Provider canary

Calls the existing authenticated admin autotest API. Gateway checkpoints the selected source party, creates an isolated `autotest` branch, and runs a bounded number of turns. The runner hashes source history and state before and after the run and fails if either changes.

Provide authentication only through an environment variable; do not put it in a command, file, or report:

```powershell
$env:RP_STACK_SESSION_COOKIE = "rp_gateway_session=<server-issued value>"
powershell.exe -File scripts/run-rp-stack-evals.ps1 `
  -Mode ProviderCanary `
  -BaseUrl http://192.168.1.88:8010 `
  -SourcePartyId <party-id> `
  -PlayerModelProfileId <profile-id> `
  -PlayerPrompt "Take the next in-world action only." `
  -TurnCount 1 `
  -SemanticResponsesFile <provider-canary-saved-responses.json> `
  -ConfirmProviderRun
```

The canary requires the explicit `-ConfirmProviderRun` switch, allows at most five turns from this runner, never records the cookie, and requests stop if its bounded poll timeout expires.
Each `-SemanticResponsesFile` must identify `source.producer` as
`provider-canary`; pass the parameter more than once for repeated runs. The
report includes every separate metric and its min/max spread across repeats.

## 3. Production endurance

Use a long deployed RP party and the read-only `causal_probe` to follow a
registered expectation through authoritative storage, the next-turn prompt,
and repeated later scene consequences. This is the only layer that can reach
`держится`; healthy containers, a provider canary, and green CI cannot.

## Browser smoke

Use the Codex Browser skill against the deployed Light GUI and complete the plugin checklist at `plugins/rp-stack-devkit/assets/browser-smoke-checklist.md`. HTTP-only checks do not count as browser verification.

A machine-readable evidence file can be validated with:

```powershell
powershell.exe -File scripts/run-rp-stack-evals.ps1 -Mode BrowserReport -EvidenceFile <path-to-json>
```

Eval reports are written under `artifacts/evals/`, which is ignored by Git.
