# WI-S3-01-06 Visitor Answer Safety G0 Evidence

Date: 2026-07-29

## Scope

This is a pure default-deny contract for a future text-only Visitor answer surface.
It builds on ShareGrant and adult Visitor-session metadata but creates no answer, gateway, provider request, report record, session mutation, or iOS surface.
Inputs contain only opaque identifiers, SHA-256 hashes, timestamps, and controlled state.
Readable Visitor text, answer content, private Owner data, Persona state, voice/digital-human state, raw credentials, and provider responses are outside the contract.

## Admission Rules

- Future admission requires a direct adult-verified Visitor, active version-bound grant, matching short session, current published version, and public citations.
- Family membership never substitutes for a ShareGrant.
- Private Owner Memory, KBLite, Persona, Voice, and Digital Human sources are rejected.
- Missing public evidence is a no-evidence boundary, never a fallback to private data or a provider.
- AI disclosure is always required.
- Two hours of continuous use requires a reminder.
- UI, voice, and keyword exits request deterministic termination without waiting for model output.
- Crisis requires a neutral safety assistant; prompt injection, private extraction, unknown risk, rate-limit uncertainty, payment, and high-stakes decisions remain blocked.
- Reports require a future durable receipt writer and are not persisted by this G0 contract.

## Data Boundary

Migration `0054_publication_visitor_answer_safety.sql` adds only private hash-only future receipt metadata and session safety state.
It records timing, exit/safety enums, request/context/citation fingerprints, and policy/outcome hashes.
PostgreSQL `PUBLIC` has no permission and all Visitor release flags remain false.
The schema does not retain question or answer bodies, raw prompts/messages, source payload, object URL, or private-memory material.

## Explicitly Not Implemented

- Public or Visitor API route, deep link, UI, or release flag.
- Public Store query adapter or fallback to private data.
- LLM/provider call, model safety integration, or retention policy.
- Report writer, rate-limit implementation, session closer, emergency-contact workflow, crisis operations runbook, or public Voice/Digital Human behavior.

G1/G2/G3/G4 remain required for hidden ViewState, gateway/session enforcement, model/provider safety and retention, and crisis/regulatory operations.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-visitor-answer-safety-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate covers adult/family/grant/session denial, public-only context, private-context rejection, no-evidence behavior, two-hour reminder, three exit channels, crisis/injection/rate-limit/high-stakes blocking, report receipt requirement, no route/persistence/network dependency, and default-off migration flags.
