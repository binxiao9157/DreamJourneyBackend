# run-02 R03 production component probe

Read-only source review. This Swift file directly extracts current production event metadata, ASR parser/router, assistant stream and shared raw dispatcher. Supporting role and privacy-safe logger are minimal declarations; no Controller, real disk, HTTP, device or Provider was exercised.

Run: `swift -module-cache-path /tmp/live-cap-run02-review/module-cache /tmp/live-cap-run02-review/main.swift`

Probe 1: current generation response, older generation terminal with same provider reply ID, then current terminal. The older packet is rejected by router but mutates the assistant state first, leaving only the current interim output.

Probe 2: complete 40 replies normally using prefix delta, suffix delta, empty terminal. Repeat the exact original suffix delta and empty terminal packets for each reply. Eviction of completed IDs allows some historical replies to produce suffix-only complete events. Which IDs and exact count vary due to Set prefix truncation; existence is reproducible.

This proves component behavior. It does not claim a Controller/disk end-to-end test or occurrence in the original device session.
