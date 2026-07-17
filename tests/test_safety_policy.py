import unittest

from pydantic import ValidationError

from app.services.safety_policy import (
    AIDisclosureMetadata,
    CrisisReason,
    CrisisRiskClass,
    HighRiskCapability,
    SafetyAction,
    SafetyDecision,
    SafetyEffectDecision,
    SafetyPolicy,
    SafetyTextMode,
    SubjectEligibilityEvidence,
    SubjectEligibilityReason,
    evaluate_subject_eligibility,
)


class SafetyPolicyCrisisTests(unittest.TestCase):
    def setUp(self):
        self.policy = SafetyPolicy()

    def test_high_confidence_english_crisis_expressions_are_classified(self):
        cases = {
            "I am going to kill myself tonight.": CrisisRiskClass.SELF_HARM,
            "I plan to hurt someone tomorrow.": CrisisRiskClass.HARM_TO_OTHERS,
            "I want to join my deceased wife.": CrisisRiskClass.JOIN_DECEASED,
            "My daughter has gone missing and may be in danger.": CrisisRiskClass.IMMEDIATE_DANGER,
            "Someone is trying to kill me right now.": CrisisRiskClass.IMMEDIATE_DANGER,
        }

        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                decision = self.policy.evaluate(expression)
                self.assertEqual(decision.riskClass, expected)
                self.assertEqual(decision.action, SafetyAction.NEUTRAL_SAFETY_RESPONSE)

    def test_high_confidence_chinese_crisis_expressions_are_classified(self):
        cases = {
            "我准备今晚自杀。": CrisisRiskClass.SELF_HARM,
            "我要杀了他。": CrisisRiskClass.HARM_TO_OTHERS,
            "我想去陪去世的妈妈。": CrisisRiskClass.JOIN_DECEASED,
            "我的孩子失联了，可能有生命危险。": CrisisRiskClass.IMMEDIATE_DANGER,
            "救命，有人正在追杀我。": CrisisRiskClass.IMMEDIATE_DANGER,
            "我真的撑不住了。": CrisisRiskClass.HIGH_DISTRESS,
            "我快要崩溃了。": CrisisRiskClass.HIGH_DISTRESS,
        }

        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                decision = self.policy.evaluate(expression)
                self.assertEqual(decision.riskClass, expected)
                self.assertEqual(decision.action, SafetyAction.NEUTRAL_SAFETY_RESPONSE)

    def test_ordinary_grief_anxiety_and_negated_intent_do_not_over_trigger(self):
        expressions = (
            "I miss my late wife every day.",
            "I feel anxious about tomorrow's appointment.",
            "Grief has made this a very difficult month.",
            "I do not want to kill myself.",
            "我很想念去世的妈妈。",
            "最近工作让我很焦虑。",
            "我没有想过自杀。",
        )

        for expression in expressions:
            with self.subTest(expression=expression):
                decision = self.policy.evaluate(expression)
                self.assertEqual(decision.riskClass, CrisisRiskClass.NONE)
                self.assertEqual(decision.action, SafetyAction.CONTINUE)
                self.assertEqual(decision.effects.textMode, SafetyTextMode.STANDARD)

    def test_crisis_forces_neutral_text_and_independently_denies_all_effects(self):
        effects = self.policy.evaluate("I want to die.").effects

        self.assertEqual(effects.textMode, SafetyTextMode.NEUTRAL_SAFETY)
        self.assertFalse(effects.personaAllowed)
        self.assertFalse(effects.delayedReplyAllowed)
        self.assertFalse(effects.clonedVoiceAllowed)
        self.assertFalse(effects.digitalHumanAllowed)
        self.assertFalse(effects.providerEffectsAllowed)

    def test_decision_is_value_free_and_carries_persistent_ai_disclosure(self):
        raw_expression = "I am going to kill myself tonight."
        payload = self.policy.evaluate(raw_expression).model_dump(mode="json")

        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "policyVersion",
                "riskClass",
                "reason",
                "action",
                "disclosure",
                "effects",
                "neutralResponse",
            },
        )
        self.assertNotIn(raw_expression, str(payload))
        self.assertNotIn("text", payload)
        self.assertNotIn("expression", payload)
        self.assertTrue(payload["disclosure"]["required"])
        self.assertTrue(payload["disclosure"]["persistent"])
        self.assertTrue(payload["disclosure"]["explicitMark"])
        self.assertTrue(payload["disclosure"]["implicitMark"])
        self.assertEqual(payload["disclosure"]["presentation"], "continuous")
        self.assertEqual(payload["disclosure"]["visibleLabel"], "AI 生成")
        self.assertEqual(payload["disclosure"]["assistantLabel"], "AI 助手")
        self.assertTrue(payload["disclosure"]["spokenDisclosureRequired"])
        self.assertEqual(
            payload["disclosure"]["labelPolicyVersion"],
            SafetyPolicy.AI_LABEL_POLICY_VERSION,
        )
        self.assertFalse(payload["neutralResponse"]["diagnostic"])
        self.assertFalse(payload["neutralResponse"]["treatmentPromise"])
        self.assertIn("可信任的人", payload["neutralResponse"]["message"])
        self.assertIn("当地紧急服务", payload["neutralResponse"]["message"])

    def test_typed_contracts_are_frozen_and_forbid_unexpected_evidence(self):
        disclosure = AIDisclosureMetadata(
            labelPolicyVersion="ai-label-v-test",
        )

        with self.assertRaises(ValidationError):
            AIDisclosureMetadata.model_validate(
                {**disclosure.model_dump(), "rawText": "must not persist"}
            )
        with self.assertRaises(ValidationError):
            disclosure.required = False

    def test_typed_contract_rejects_a_crisis_decision_with_any_allowed_effect(self):
        with self.assertRaises(ValidationError):
            SafetyDecision(
                policyVersion="safety-policy-test",
                riskClass=CrisisRiskClass.SELF_HARM,
                reason=CrisisReason.EXPLICIT_SELF_HARM_INTENT,
                action=SafetyAction.NEUTRAL_SAFETY_RESPONSE,
                disclosure=AIDisclosureMetadata(
                    labelPolicyVersion="ai-label-v-test",
                ),
                effects=SafetyEffectDecision(
                    textMode=SafetyTextMode.NEUTRAL_SAFETY,
                    personaAllowed=False,
                    delayedReplyAllowed=True,
                    clonedVoiceAllowed=False,
                    digitalHumanAllowed=False,
                    providerEffectsAllowed=False,
                ),
            )


class SubjectEligibilityTests(unittest.TestCase):
    @staticmethod
    def eligible_evidence(**overrides):
        values = {
            "capability": HighRiskCapability.CLONED_VOICE,
            "subjectKind": "self",
            "ageStatus": "adult",
            "livingStatus": "living",
            "ageVerified": True,
            "livenessVerified": True,
            "subjectMatchesActor": True,
            "consentVerified": True,
            "consentPurpose": HighRiskCapability.CLONED_VOICE,
        }
        values.update(overrides)
        return SubjectEligibilityEvidence(**values)

    def test_only_verified_living_adult_self_is_allowed_for_each_capability(self):
        for capability in HighRiskCapability:
            with self.subTest(capability=capability):
                evidence = self.eligible_evidence(
                    capability=capability,
                    consentPurpose=capability,
                )
                decision = evaluate_subject_eligibility(evidence)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.decision, "allow")
                self.assertEqual(
                    decision.reason,
                    SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
                )

    def test_ineligible_subject_evidence_returns_stable_hard_deny_reasons(self):
        cases = (
            (
                {"ageStatus": "minor", "ageVerified": True},
                SubjectEligibilityReason.MINOR,
            ),
            ({"ageStatus": "unknown"}, SubjectEligibilityReason.AGE_UNKNOWN),
            ({"ageVerified": False}, SubjectEligibilityReason.AGE_VERIFICATION_MISSING),
            (
                {"livingStatus": "unknown"},
                SubjectEligibilityReason.LIVING_STATUS_UNKNOWN,
            ),
            (
                {"subjectKind": "deceased", "livingStatus": "deceased"},
                SubjectEligibilityReason.DECEASED_SUBJECT,
            ),
            ({"subjectKind": "family"}, SubjectEligibilityReason.FAMILY_SUBJECT),
            ({"subjectKind": "thirdParty"}, SubjectEligibilityReason.SUBJECT_MISMATCH),
            ({"subjectKind": "unknown"}, SubjectEligibilityReason.SUBJECT_MISMATCH),
            ({"subjectMatchesActor": False}, SubjectEligibilityReason.SUBJECT_MISMATCH),
            ({"livenessVerified": False}, SubjectEligibilityReason.LIVENESS_MISSING),
            ({"consentVerified": False}, SubjectEligibilityReason.PURPOSE_CONSENT_MISSING),
            ({"consentPurpose": None}, SubjectEligibilityReason.PURPOSE_CONSENT_MISSING),
            (
                {"consentPurpose": HighRiskCapability.DIGITAL_HUMAN},
                SubjectEligibilityReason.PURPOSE_CONSENT_MISMATCH,
            ),
        )

        for overrides, expected_reason in cases:
            with self.subTest(overrides=overrides):
                decision = evaluate_subject_eligibility(
                    self.eligible_evidence(**overrides)
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.decision, "hardDeny")
                self.assertEqual(decision.reason, expected_reason)

    def test_subject_decision_contains_no_input_evidence(self):
        decision = evaluate_subject_eligibility(
            self.eligible_evidence(subjectKind="family")
        )

        self.assertEqual(
            set(decision.model_dump()),
            {"schemaVersion", "capability", "allowed", "decision", "reason"},
        )


if __name__ == "__main__":
    unittest.main()
