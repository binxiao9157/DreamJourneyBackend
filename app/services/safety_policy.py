from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Literal, Optional, Pattern, Tuple

from pydantic import BaseModel, ConfigDict, model_validator


class CrisisRiskClass(str, Enum):
    NONE = "none"
    HIGH_DISTRESS = "highDistress"
    SELF_HARM = "selfHarm"
    HARM_TO_OTHERS = "harmToOthers"
    JOIN_DECEASED = "joinDeceased"
    IMMEDIATE_DANGER = "missingPersonOrImmediateDanger"


class CrisisReason(str, Enum):
    NO_HIGH_CONFIDENCE_CRISIS = "noHighConfidenceCrisis"
    EXPLICIT_HIGH_DISTRESS = "explicitHighDistress"
    EXPLICIT_SELF_HARM_INTENT = "explicitSelfHarmIntent"
    EXPLICIT_HARM_TO_OTHERS_INTENT = "explicitHarmToOthersIntent"
    EXPLICIT_JOIN_DECEASED_INTENT = "explicitJoinDeceasedIntent"
    MISSING_PERSON_OR_IMMEDIATE_DANGER = "missingPersonOrImmediateDanger"


class SafetyAction(str, Enum):
    CONTINUE = "continueWithPolicy"
    NEUTRAL_SAFETY_RESPONSE = "respondWithNeutralSafetyText"


class SafetyTextMode(str, Enum):
    STANDARD = "standard"
    NEUTRAL_SAFETY = "neutralSafetyText"


class HighRiskCapability(str, Enum):
    CLONED_VOICE = "clonedVoice"
    DIGITAL_HUMAN = "digitalHuman"


class SubjectEligibilityReason(str, Enum):
    ELIGIBLE_LIVING_ADULT_SELF = "eligibleLivingAdultSelf"
    MINOR = "minorHardDeny"
    AGE_UNKNOWN = "ageUnknownHardDeny"
    AGE_VERIFICATION_MISSING = "ageVerificationMissingHardDeny"
    LIVING_STATUS_UNKNOWN = "livingStatusUnknownHardDeny"
    DECEASED_SUBJECT = "deceasedSubjectHardDeny"
    FAMILY_SUBJECT = "familySubjectHardDeny"
    SUBJECT_MISMATCH = "subjectActorMismatchHardDeny"
    LIVENESS_MISSING = "livenessMissingHardDeny"
    PURPOSE_CONSENT_MISSING = "purposeConsentMissingHardDeny"
    PURPOSE_CONSENT_MISMATCH = "purposeConsentMismatchHardDeny"


class AIDisclosureMetadata(BaseModel):
    """Metadata that must travel with every generated decision and output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: Literal[True] = True
    persistent: Literal[True] = True
    presentation: Literal["continuous"] = "continuous"
    labelPolicyVersion: str
    visibleLabel: Literal["AI 生成"] = "AI 生成"
    assistantLabel: Literal["AI 助手"] = "AI 助手"
    spokenDisclosureRequired: Literal[True] = True
    explicitMark: Literal[True] = True
    implicitMark: Literal[True] = True


class NeutralSafetyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locale: Literal["zh-CN"] = "zh-CN"
    message: Literal[
        "我注意到你可能正处在危险中。请立即联系身边可信任的人；如有紧迫危险，请联系当地紧急服务。"
    ] = "我注意到你可能正处在危险中。请立即联系身边可信任的人；如有紧迫危险，请联系当地紧急服务。"
    diagnostic: Literal[False] = False
    treatmentPromise: Literal[False] = False


class SafetyEffectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    textMode: SafetyTextMode
    personaAllowed: bool
    delayedReplyAllowed: bool
    clonedVoiceAllowed: bool
    digitalHumanAllowed: bool
    providerEffectsAllowed: bool

    def all_non_text_effects_denied(self) -> bool:
        return not any(
            (
                self.personaAllowed,
                self.delayedReplyAllowed,
                self.clonedVoiceAllowed,
                self.digitalHumanAllowed,
                self.providerEffectsAllowed,
            )
        )


class CrisisClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    riskClass: CrisisRiskClass
    reason: CrisisReason


class SafetyDecision(BaseModel):
    """Value-free decision contract; raw expression text is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    policyVersion: str
    riskClass: CrisisRiskClass
    reason: CrisisReason
    action: SafetyAction
    disclosure: AIDisclosureMetadata
    effects: SafetyEffectDecision
    neutralResponse: Optional[NeutralSafetyResponse] = None

    @model_validator(mode="after")
    def enforce_crisis_effect_invariants(self) -> "SafetyDecision":
        if self.riskClass == CrisisRiskClass.NONE:
            if self.action != SafetyAction.CONTINUE:
                raise ValueError("non-crisis decisions must use the continue action")
            if self.neutralResponse is not None:
                raise ValueError("non-crisis decisions must not carry a neutral response")
            return self

        if self.action != SafetyAction.NEUTRAL_SAFETY_RESPONSE:
            raise ValueError("crisis decisions must use the neutral safety action")
        if self.effects.textMode != SafetyTextMode.NEUTRAL_SAFETY:
            raise ValueError("crisis decisions must force neutral safety text")
        if not self.effects.all_non_text_effects_denied():
            raise ValueError("crisis decisions must deny every non-text effect")
        if self.neutralResponse is None:
            raise ValueError("crisis decisions must carry the neutral safety response")
        return self


class SubjectEligibilityEvidence(BaseModel):
    """Categorical evidence only; no identity values or source content are retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: HighRiskCapability
    subjectKind: Literal["self", "family", "thirdParty", "deceased", "unknown"]
    ageStatus: Literal["adult", "minor", "unknown"]
    livingStatus: Literal["living", "deceased", "unknown"]
    ageVerified: bool
    livenessVerified: bool
    subjectMatchesActor: bool
    consentVerified: bool
    consentPurpose: Optional[HighRiskCapability] = None


class SubjectEligibilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    capability: HighRiskCapability
    allowed: bool
    decision: Literal["allow", "hardDeny"]
    reason: SubjectEligibilityReason

    @model_validator(mode="after")
    def enforce_decision_invariants(self) -> "SubjectEligibilityDecision":
        eligible = (
            self.reason == SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF
        )
        if self.allowed != eligible:
            raise ValueError("subject eligibility reason conflicts with allowed state")
        if self.decision != ("allow" if eligible else "hardDeny"):
            raise ValueError("subject eligibility reason conflicts with decision")
        return self


_SELF_HARM_NEGATIONS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"\b(?:do not|don't|did not|never|no longer)\s+"
        r"(?:really\s+)?(?:want|plan|intend|mean|going)\b.{0,32}"
        r"\b(?:kill myself|hurt myself|die|commit suicide)\b"
    ),
    re.compile(r"\b(?:i am|i'm)\s+not\s+suicidal\b"),
    re.compile(r"我(?:不|没|没有|从没)(?:想|想过|打算|准备|要).{0,10}(?:自杀|死|伤害自己)"),
)

_SELF_HARM_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"\b(?:i|i'm|i am)\s+(?:really\s+)?"
        r"(?:want|plan|intend|mean|am going|am ready|am about)\s+to\s+"
        r"(?:kill|hurt)\s+myself\b"
    ),
    re.compile(r"\bi\s+(?:want|plan|intend)\s+to\s+die\b"),
    re.compile(r"\b(?:i'm|i am)\s+(?:going\s+to\s+)?commit suicide\b"),
    re.compile(r"\b(?:i\s+want\s+to|i'm going to|i am going to)\s+(?:end my life|take my own life)\b"),
    re.compile(r"\b(?:i am|i'm)\s+suicidal\b"),
    re.compile(r"我(?:现在)?(?:想|要|准备|打算)(?:在?今晚|现在)?(?:去)?自杀"),
    re.compile(r"我(?:真的)?不想活了"),
    re.compile(r"我(?:想|要|准备|打算)(?:去)?死(?:了|掉|[。！？,.!?]|$)"),
    re.compile(r"我(?:想|要|准备|打算).{0,8}(?:结束自己的生命|结束我的生命|伤害我自己)"),
)

_HARM_TO_OTHERS_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"\bi\s+(?:want|plan|intend|mean|am going|am ready|am about)\s+to\s+"
        r"(?:kill|hurt|attack)\s+(?:you|him|her|them|someone|somebody|people)\b"
    ),
    re.compile(r"\bi(?:'m| am)\s+going\s+to\s+(?:kill|hurt|attack)\s+(?:you|him|her|them|someone|somebody|people)\b"),
    re.compile(r"我(?:要|想|准备|打算)(?:在?今晚|现在)?(?:去)?(?:杀死|杀了|伤害|攻击)(?:你|他|她|他们|她们|别人|某人|所有人)"),
    re.compile(r"我(?:要|想|准备|打算)(?:去)?杀人"),
)

_JOIN_DECEASED_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"\bi\s+(?:want|plan|intend)\s+to\s+"
        r"(?:join|follow|be with)\s+(?:my\s+)?"
        r"(?:dead|deceased|late|departed|passed away)\b"
    ),
    re.compile(
        r"\bi\s+(?:want|plan|intend)\s+to\s+(?:join|follow|be with)\s+"
        r".{0,24}\b(?:in death|in the afterlife)\b"
    ),
    re.compile(r"我(?:想|要|准备|打算)去陪(?:已经)?(?:去世|离世|过世|死去)的"),
    re.compile(r"我(?:想|要|准备|打算)(?:去)?(?:另一个世界|下面)陪"),
    re.compile(r"我(?:想|要|准备|打算)随(?:他|她|他们|她们)而去"),
)

_IMMEDIATE_DANGER_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"\b(?:my|our)\s+(?:child|daughter|son|wife|husband|mother|father|friend|\w+)\s+"
        r"(?:is|has gone|has been)\s+(?:missing|gone missing)\b"
    ),
    re.compile(r"\b(?:my life is in danger|i am in immediate danger|i'm in immediate danger)\b"),
    re.compile(r"\bsomeone\s+is\s+(?:trying to kill|trying to hurt|attacking|chasing)\s+me\b"),
    re.compile(r"\bhelp[,.! ]+.{0,24}\b(?:trying to kill me|attacking me|in danger)\b"),
    re.compile(r"我(?:的)?(?:孩子|女儿|儿子|家人|朋友|父亲|母亲).{0,8}(?:失联|走失|失踪)"),
    re.compile(r"我(?:现在|目前)?(?:有生命危险|处于危险中)"),
    re.compile(r"有人(?:正在|要|想)?(?:追杀|杀|伤害|攻击)我"),
    re.compile(r"救命.{0,20}(?:追杀|杀我|伤害我|攻击我|生命危险|危险)"),
)

_HIGH_DISTRESS_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"\b(?:i\s+)?(?:cannot|can't)\s+(?:go on|take it anymore|cope anymore)\b"),
    re.compile(r"\b(?:i'm|i am)\s+(?:at|near)\s+(?:my\s+)?breaking point\b"),
    re.compile(r"我(?:真的|已经|快|实在)?(?:撑不住|扛不住|坚持不下去)(?:了|啦|啊|[。！？,.!?]|$)"),
    re.compile(r"我(?:真的|已经|快|实在)?(?:要|快要)?崩溃(?:了|啦|啊|[。！？,.!?]|$)"),
)


def _normalize_expression(expression: str) -> str:
    normalized = unicodedata.normalize("NFKC", expression).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def _matches_any(expression: str, patterns: Tuple[Pattern[str], ...]) -> bool:
    return any(pattern.search(expression) is not None for pattern in patterns)


def classify_crisis(expression: str) -> CrisisClassification:
    """Classify only explicit, high-confidence expressions in Chinese or English."""

    normalized = _normalize_expression(expression)
    self_harm_negated = _matches_any(normalized, _SELF_HARM_NEGATIONS)

    if not self_harm_negated and _matches_any(normalized, _SELF_HARM_PATTERNS):
        return CrisisClassification(
            riskClass=CrisisRiskClass.SELF_HARM,
            reason=CrisisReason.EXPLICIT_SELF_HARM_INTENT,
        )
    if _matches_any(normalized, _HARM_TO_OTHERS_PATTERNS):
        return CrisisClassification(
            riskClass=CrisisRiskClass.HARM_TO_OTHERS,
            reason=CrisisReason.EXPLICIT_HARM_TO_OTHERS_INTENT,
        )
    if _matches_any(normalized, _JOIN_DECEASED_PATTERNS):
        return CrisisClassification(
            riskClass=CrisisRiskClass.JOIN_DECEASED,
            reason=CrisisReason.EXPLICIT_JOIN_DECEASED_INTENT,
        )
    if _matches_any(normalized, _IMMEDIATE_DANGER_PATTERNS):
        return CrisisClassification(
            riskClass=CrisisRiskClass.IMMEDIATE_DANGER,
            reason=CrisisReason.MISSING_PERSON_OR_IMMEDIATE_DANGER,
        )
    if _matches_any(normalized, _HIGH_DISTRESS_PATTERNS):
        return CrisisClassification(
            riskClass=CrisisRiskClass.HIGH_DISTRESS,
            reason=CrisisReason.EXPLICIT_HIGH_DISTRESS,
        )
    return CrisisClassification(
        riskClass=CrisisRiskClass.NONE,
        reason=CrisisReason.NO_HIGH_CONFIDENCE_CRISIS,
    )


class SafetyPolicy:
    SCHEMA_VERSION = 1
    POLICY_VERSION = "safety-policy-v1"
    AI_LABEL_POLICY_VERSION = "ai-identity-label-v1"

    def classify(self, expression: str) -> CrisisClassification:
        return classify_crisis(expression)

    def evaluate(self, expression: str) -> SafetyDecision:
        classification = self.classify(expression)
        crisis = classification.riskClass != CrisisRiskClass.NONE
        effects = SafetyEffectDecision(
            textMode=(
                SafetyTextMode.NEUTRAL_SAFETY if crisis else SafetyTextMode.STANDARD
            ),
            personaAllowed=not crisis,
            delayedReplyAllowed=not crisis,
            clonedVoiceAllowed=not crisis,
            digitalHumanAllowed=not crisis,
            providerEffectsAllowed=not crisis,
        )
        return SafetyDecision(
            policyVersion=self.POLICY_VERSION,
            riskClass=classification.riskClass,
            reason=classification.reason,
            action=(
                SafetyAction.NEUTRAL_SAFETY_RESPONSE
                if crisis
                else SafetyAction.CONTINUE
            ),
            disclosure=AIDisclosureMetadata(
                labelPolicyVersion=self.AI_LABEL_POLICY_VERSION,
            ),
            effects=effects,
            neutralResponse=NeutralSafetyResponse() if crisis else None,
        )


class SubjectEligibilityEvaluator:
    """Fail-closed evaluator for cloned Voice and digital-human subjects."""

    @staticmethod
    def evaluate(
        evidence: SubjectEligibilityEvidence,
    ) -> SubjectEligibilityDecision:
        reason = SubjectEligibilityEvaluator._reason(evidence)
        allowed = reason == SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF
        return SubjectEligibilityDecision(
            capability=evidence.capability,
            allowed=allowed,
            decision="allow" if allowed else "hardDeny",
            reason=reason,
        )

    @staticmethod
    def _reason(
        evidence: SubjectEligibilityEvidence,
    ) -> SubjectEligibilityReason:
        if evidence.ageStatus == "minor":
            return SubjectEligibilityReason.MINOR
        if evidence.ageStatus == "unknown":
            return SubjectEligibilityReason.AGE_UNKNOWN
        if not evidence.ageVerified:
            return SubjectEligibilityReason.AGE_VERIFICATION_MISSING
        if evidence.subjectKind == "deceased" or evidence.livingStatus == "deceased":
            return SubjectEligibilityReason.DECEASED_SUBJECT
        if evidence.livingStatus == "unknown":
            return SubjectEligibilityReason.LIVING_STATUS_UNKNOWN
        if evidence.subjectKind == "family":
            return SubjectEligibilityReason.FAMILY_SUBJECT
        if evidence.subjectKind != "self" or not evidence.subjectMatchesActor:
            return SubjectEligibilityReason.SUBJECT_MISMATCH
        if not evidence.livenessVerified:
            return SubjectEligibilityReason.LIVENESS_MISSING
        if not evidence.consentVerified or evidence.consentPurpose is None:
            return SubjectEligibilityReason.PURPOSE_CONSENT_MISSING
        if evidence.consentPurpose != evidence.capability:
            return SubjectEligibilityReason.PURPOSE_CONSENT_MISMATCH
        return SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF


def evaluate_subject_eligibility(
    evidence: SubjectEligibilityEvidence,
) -> SubjectEligibilityDecision:
    return SubjectEligibilityEvaluator.evaluate(evidence)
