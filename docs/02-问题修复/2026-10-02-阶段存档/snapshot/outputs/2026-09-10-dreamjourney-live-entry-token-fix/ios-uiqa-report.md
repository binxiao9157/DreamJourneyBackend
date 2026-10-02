# Echo Continuous Turn UIQA Smoke

Run ID: 20260910-live-entry-fix-rerun

## Verification

- Simulator: 8C90FF12-82E3-41A6-A003-EE0BB26BEAA6
- Bundle ID: com.yxj.dreamjourney.app
- The provider-free scenario completed two local turns.
- The ordinary stop path returned Echo to idle.
- A stale reply after stop was rejected.
- Echo left and re-entered its tab.
- The process restarted and the same scenario completed again.

## Evidence

- First run result: 01-cold-start-result.json
- First run screenshot: 01-cold-start.png
- Restart run result: 02-process-restart-result.json
- Restart screenshot: 02-process-restart.png
- Build log: build.log
- OS log: oslog.log
