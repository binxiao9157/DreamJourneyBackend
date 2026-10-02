# Xcode result summaries

## Pre-fix counterexamples

- Result: `Failed`
- Device: iPhone 17 Pro simulator, iOS 26.5, arm64
- Tests: 2
- Passed: 0
- Failed: 2
- Result bundle: `../../pre-fix/b4-raw-value-precision-counterexamples.xcresult`
- Failures:
  - confidence-only proposal had no visible confidence field change.
  - time-plus-confidence proposal did not contain both required visible paths.

## Post-fix focused precision verification

- Result: `Passed`
- Device: iPhone 17 Pro simulator, iOS 26.5, arm64
- Tests: 6
- Passed: 6
- Failed: 0
- Result bundle: `b4-precision-focused-final.xcresult`

The six tests cover the two backend-builder counterexamples, immutable displayed binding, UIKit detail, correction preview, and a non-first related-group member.

## Post-fix complete OwnerTruth contract regression

- Result: `Passed`
- Device: iPhone 17 Pro simulator, iOS 26.5, arm64
- Tests: 316
- Passed: 316
- Failed: 0
- Result bundle: `owner-truth-contracts-full.xcresult`

## Generic iOS device build

- Result: `succeeded`
- Destination: Any iOS Device
- Errors: 0
- Warnings: 70
- Signing: disabled; this is not an installable signed iPhone build.
- Result bundle: `generic-ios-device-build.xcresult`

The warnings are existing dependency/deprecation warnings, including Kingfisher Swift 6 whitespace and older API declarations. No new build error remains.
