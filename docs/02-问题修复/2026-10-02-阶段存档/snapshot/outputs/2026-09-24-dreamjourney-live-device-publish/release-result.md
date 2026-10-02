# DreamJourney Live release result, 2026-09-24

## Status

- Backend: `DEPLOYED`, anonymous readiness `PASS`.
- iOS: signed device build `INSTALLED` and launch `PASS` after user trusted the developer profile; Live functional device acceptance is in progress.
- Real Provider, new short Live candidate/review/formal-memory chain, physical 20-minute and 65-minute scenes: `NOT_RUN` in this release.
- Historical seq5 initial trigger remains unresolved. No historical business task was replayed or altered.

## Fixed source and deployment

- Existing backend `origin`: `git@github.com:binxiao9157/DreamJourneyBackend.git`; existing `main` was fast-forwarded and pushed to `8141ff271228b78c35237f9947f4bf026332af43`. Remote and local refs matched; deployment checkout clean.
- New independent release directory: `/opt/services/dreamjourney/releases/live-unified-8141ff2-20260924`, created from that Git commit. `/opt/services/dreamjourney/current` now points there. Previous directory retained: `/opt/services/dreamjourney/releases/live-long-memory-run04-20260920-2230`.
- The authoritative deployment preflight passed before updating the checkout. The previous Docker API image ID was no longer taggable after the new build; a separate 0122 API rollback image was rebuilt from the retained previous release directory as `dreamjourney-rollback-api:pre-8141ff2-20260924`. This is a code artifact, not proof that old code is compatible with schema 0124.
- The existing production configuration was copied root:root mode 0600; no secret was copied into Git or this report. The same Compose project name `dreamjourneybackend` was retained.

## Migration, backup and health gates

- Migration-before encrypted backup: verified, schema head `0122`. Target-image dry-run found exactly `0123` and `0124` pending.
- Six old long-running workers stopped gracefully before migration. Apply and verify returned `status=ready`, applied head `0124`, zero pending migrations.
- The 0124 API became healthy; the registered six enabled workers were rebuilt and passed image/database/activation and stability gates. Two later samples showed API healthy and all six workers running; every restart count was zero.
- After switching the release pointer, the encrypted post-migration backup was verified with schema head `0124`.
- The iOS-configured public endpoint `https://www.mmdd10.tech/dreamjourney-api` passed the deployed read-only `/live`, `/ready`, `/health` contract: database, schema, authentication and incident components ready. Remaining disk space after release was about 9.2 GB.
- No production candidate, formal-memory, audit-history, Dead Letter or old failed Live task was read or modified for testing. No unknown business POST was replayed.

## iOS installation and first device gate

- Signed build: `DerivedData/Build/Products/Debug-iphoneos/DreamJourney.app`; executable SHA-256 `07c4031d661a9ff3bfca296212a88d25bc1718f07bcdab9e98620f304abd3daf`.
- Bundle ID `com.gaominge.dreamjourney.app`; embedded backend URL is the public endpoint above. `codesign --verify --deep --strict` passed. The development profile expires 2026-10-01, includes this iPhone UDID and matches the app/team identifier.
- CoreDevice installed the app on the paired iPhone 14 Pro Max without uninstalling its data. `devicectl device info apps` confirmed the installed bundle.
- The first `devicectl device process launch` failed with iOS `FBSOpenApplicationServiceErrorDomain RequestDenied / Security`. After the user explicitly trusted the developer profile, the same installed app launched successfully. A local device screenshot showed the Echo page, visible microphone and the pre-existing status "上次整理记录无法安全核对，原记录已保留". This old recovery status is not evidence about a new Live scene. Candidate baseline and new synthetic Live acceptance remain pending.

## Next device gate

1. Confirm the iPhone launches the newly installed app and remains signed in; if iOS still denies launch, capture the exact system prompt before any new build.
2. Refresh the candidate list and record the baseline count; do not review existing candidates as part of this release smoke.
3. Run a new, unique synthetic short Live scene with two turns where the second adds detail to the first. Confirm audible reply and stop handoff, then verify the same-scene candidate content, source identity and no duplicate.
4. Only after that short gate passes, user may explicitly review the new synthetic candidate and confirm the formal-memory readback and cold-start idempotency. Physical long scenes require a separate scheduled device run.

## Rollback boundary

Keep 0124 and both encrypted backups. The prior release directory and rebuilt 0122 API image are retained, but the 0123/0124 manifests require old-worker drain and forward-compatible handling. Do not run down migrations, overwrite production data, restart 0122 workers against 0124, or delete recovery coordinates. For a release failure, stop new admissions and assess a forward fix or compatible feature-off code path under the deployment runbook.
