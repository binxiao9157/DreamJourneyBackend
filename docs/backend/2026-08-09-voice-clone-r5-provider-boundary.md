# Voice Clone R5 Provider Boundary

Date: 2026-08-09  
Status: `CODE_COMPLETE_NON_DEVICE_VERIFIED`

## Scope

`/config/runtime.voiceClone.operationMatrix` is the server authority for the
seven VoiceProfile operations:

- `train`
- `query`
- `preview`
- `accept`
- `synthesize`
- `pause`
- `delete`

Each operation reports whether the command is available, who owns execution,
whether a Provider completion receipt is possible, the completion mode, a
reason code, the app route and valid profile states. The response contains no
Provider credential, raw receipt or speaker-slot value.

## Safety boundary

- Training, query, preview and synthesis fail closed independently when their
  Provider or admission prerequisite is unavailable.
- Accept and pause remain server-authority operations.
- Delete always revokes application use first. The current VolcEngine adapter
  explicitly reports Provider deletion as `unsupported`; it never claims that
  upstream media was deleted.
- A future deletion adapter must declare reviewed support and have the deletion
  worker enabled before `providerCompletionAvailable` can become true.

## Client contract

iOS parses schema version 1 only and requires all seven operations. A malformed
or incomplete matrix fails closed. The hidden voice-clone shell gates each
button against the matching operation rather than treating a configured base
URL as capability proof.

## Verification

- `scripts/run-backend-voice-clone-r5-provider-boundary-gate.sh`
  - 45 focused lifecycle, deletion, role-binding and runtime tests
- `scripts/verify_backend.sh`
  - 1966 unit tests
  - FastAPI smoke
  - all existing backend gates
- iOS `Scripts/QA/product-v4/run-voice-clone-r5-provider-boundary-gate.sh`
- iOS Debug generic Simulator workspace build

No real voice sample, trial slot, Provider training/deletion call or device was
used by this gate.
