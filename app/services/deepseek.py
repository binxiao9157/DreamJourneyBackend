import json
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import Settings
from app.observability.redaction import provider_dry_run_report
from app.services.knowledge_extraction import LEGACY_TRANSCRIPT, USER_EVIDENCE_ONLY


class ArchiveAnalysisStatus(str, Enum):
    pending = "pending"
    analyzing = "analyzing"
    analyzed = "analyzed"
    failed = "failed"
    retryable = "retryable"

    @classmethod
    def values(cls) -> list:
        return [status.value for status in cls]


class ArchiveImageAnalysisProviderAdapter:
    provider_id = "unknown"
    supports_vision = False
    fallback_mode = "retryableFailure"
    endpoint = "/archive/image-analysis"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return False

    def public_capability(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "provider": self.provider_id,
            "supportsVision": self.supports_vision,
            "fallbackMode": self.fallback_mode,
            "statuses": ArchiveAnalysisStatus.values(),
        }

    def request_analysis(self, image_base64: str) -> Dict[str, Any]:
        raise NotImplementedError

    def dry_run_report(self, image_base64: str) -> Dict[str, Any]:
        raise NotImplementedError

    # Compatibility alias for callers that used the former misleading name.
    # The return value is metadata-only and never an upstream request.
    def redacted_request(self, image_base64: str) -> Dict[str, Any]:
        return self.dry_run_report(image_base64)

    def response_contract(self) -> Dict[str, Any]:
        return DeepSeekImageAnalysisProxy.response_contract()

    def failure_contract(
        self,
        reason: str = "provider_unavailable",
        provider_message: str = "",
        provider_error_code: str = "providerUnavailable",
    ) -> Dict[str, Any]:
        return DeepSeekImageAnalysisProxy.failure_contract(
            reason=reason,
            provider_message=provider_message,
            provider_error_code=provider_error_code,
            provider=self.provider_id,
        )


class DeepSeekTextOnlyImageAnalysisAdapter(ArchiveImageAnalysisProviderAdapter):
    provider_id = "deepseek/text-only"
    supports_vision = False
    fallback_mode = "retryableFailure"

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_api_key)

    def request_analysis(self, image_base64: str) -> Dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        image_base64 = image_base64.strip()
        if not image_base64:
            raise ValueError("imageBase64 is required")
        return self.failure_contract(
            provider_message=(
                "provider deepseek/text-only does not support vision input; "
                "retry after archive image analysis provider is upgraded"
            )
        )

    def dry_run_report(self, image_base64: str) -> Dict[str, Any]:
        return DeepSeekImageAnalysisProxy(self.settings).dry_run_report(image_base64)


class ArchiveImageAnalysisProviderFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def make(self) -> ArchiveImageAnalysisProviderAdapter:
        return DeepSeekTextOnlyImageAnalysisAdapter(self.settings)


class DeepSeekImageAnalysisProxy:
    model = "DeepSeek-V4-Flash"

    def __init__(self, settings: Settings):
        self.settings = settings

    def build_request(self, image_base64: str) -> Dict[str, Any]:
        image_base64 = image_base64.strip()
        if not image_base64:
            raise ValueError("imageBase64 is required")

        analysis_prompt = (
            "描述这张照片的内容。关注：1. 场景（在哪里、什么场合）2. 人物（数量、年龄、推测关系）"
            "3. 活动（在做什么）4. 情绪氛围 5. 年代特征。"
            "请输出严格JSON："
            '{"description":"...","detectedPeople":["..."],"detectedLocations":["..."],'
            '"detectedScenes":["..."],"tags":["..."],"scene":"...","occasion":"...",'
            '"mood":"...","estimatedDecade":1970}'
        )
        messages = [
            {"role": "system", "content": "你是老照片分析专家。输出严格JSON，不要其他文字。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": analysis_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            },
        ]
        return {
            "url": self.settings.deepseek_base_url,
            "headers": {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.deepseek_api_key or ''}",
            },
            "json": {
                "model": self.model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 1024,
            },
        }

    def request_analysis(self, image_base64: str) -> Dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")

        request = self.build_request(image_base64)
        with httpx.Client(timeout=60) as client:
            response = client.post(
                request["url"],
                headers=request["headers"],
                json=request["json"],
            )
            response.raise_for_status()

        content = self._extract_content(response.json())
        parsed = self.parse_analysis(content)
        return parsed

    def dry_run_report(self, image_base64: str) -> Dict[str, Any]:
        normalized_image = image_base64.strip()
        if not normalized_image:
            raise ValueError("imageBase64 is required")
        return provider_dry_run_report(
            provider="deepseek/text-only",
            capability="archiveImageAnalysis",
            method="POST",
            configured=bool(self.settings.deepseek_api_key),
            input_summary={
                "encodedInputCharacterCount": len(normalized_image),
                "imageCount": 1,
                "providerSupportsVision": False,
            },
        )

    # Compatibility alias for internal callers during the dry-run contract
    # migration. It returns the metadata-only report above, not a request.
    def redacted_request(self, image_base64: str) -> Dict[str, Any]:
        return self.dry_run_report(image_base64)

    @classmethod
    def parse_analysis(cls, content: str) -> Dict[str, Any]:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        parsed = cls._loads_json(cleaned)
        if parsed is None:
            extracted = cls.extract_json_substring(cleaned)
            parsed = cls._loads_json(extracted) if extracted is not None else None
        if parsed is None:
            raise ValueError("DeepSeek image analysis returned non-JSON content")

        description = str(parsed.get("description") or "")
        detected_locations = cls._string_list(parsed.get("detectedLocations"))
        detected_scenes = cls._string_list(parsed.get("detectedScenes"))
        scene = str(parsed.get("scene") or "")
        occasion = str(parsed.get("occasion") or "")
        if scene and scene not in detected_locations:
            detected_locations.append(scene)
        if occasion and occasion not in detected_scenes:
            detected_scenes.append(occasion)

        return {
            "analysisStatus": "analyzed",
            "analysisSummary": description,
            "description": description,
            "detectedPeople": cls._string_list(parsed.get("detectedPeople")),
            "detectedLocations": detected_locations,
            "detectedScenes": detected_scenes,
            "tags": cls._string_list(parsed.get("tags")),
            "scene": str(parsed.get("scene") or ""),
            "occasion": str(parsed.get("occasion") or ""),
            "mood": str(parsed.get("mood") or ""),
            "estimatedDecade": cls._int_or_none(parsed.get("estimatedDecade")),
            "analysisFailureReason": "",
            "analysisRetryable": False,
        }

    @staticmethod
    def response_contract() -> Dict[str, Any]:
        return {
            "analysisStatus": "analyzed",
            "analysisSummary": "",
            "description": "",
            "detectedPeople": [],
            "detectedLocations": [],
            "detectedScenes": [],
            "tags": [],
            "scene": "",
            "occasion": "",
            "mood": "",
            "estimatedDecade": None,
            "analysisFailureReason": "",
            "analysisRetryable": True,
        }

    @staticmethod
    def failure_contract(
        reason: str = "provider_unavailable",
        provider_message: str = "",
        provider_error_code: str = "providerUnavailable",
        provider: str = "deepseek",
    ) -> Dict[str, Any]:
        payload = {
            "analysisStatus": ArchiveAnalysisStatus.failed.value,
            "analysisSummary": "",
            "description": "",
            "detectedPeople": [],
            "detectedLocations": [],
            "detectedScenes": [],
            "tags": [],
            "scene": "",
            "occasion": "",
            "mood": "",
            "estimatedDecade": None,
            "analysisFailureReason": reason,
            "analysisRetryable": True,
            "provider": provider,
            "providerErrorCode": provider_error_code,
        }
        return payload

    @staticmethod
    def extract_json_substring(text: str) -> Optional[str]:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start:end + 1]

    @staticmethod
    def _extract_content(payload: Dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("DeepSeek returned empty choices")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise ValueError("DeepSeek returned empty content")
        return content

    @staticmethod
    def _loads_json(text: str) -> Optional[Dict[str, Any]]:
        try:
            loaded = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _string_list(value: Any) -> list:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class DeepSeekKnowledgeExtractionProxy:
    model = "DeepSeek-V4-Flash"

    def __init__(self, settings: Settings):
        self.settings = settings

    def build_request(
        self,
        transcript: str = "",
        existing_summary: str = "",
        *,
        turns: Optional[List[Dict[str, Any]]] = None,
        source_policy: str = LEGACY_TRANSCRIPT,
    ) -> Dict[str, Any]:
        transcript = transcript.strip()
        if turns is None and not transcript:
            raise ValueError("transcript is required")
        if turns is not None:
            if not turns:
                raise ValueError("turns are required")
            if source_policy != USER_EVIDENCE_ONLY:
                raise ValueError("structured turns require sourcePolicy userEvidenceOnly")

        prompt = self.build_prompt(
            transcript=transcript,
            existing_summary=existing_summary or "（暂无已有知识）",
            turns=turns,
            source_policy=source_policy,
        )
        system_content = "You are a precise strict JSON extractor. 只输出严格JSON。"
        if turns is not None:
            system_content += " Only role=user turns are admissible evidence."
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]
        return {
            "url": self.settings.deepseek_base_url,
            "headers": {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.deepseek_api_key or ''}",
            },
            "json": {
                "model": self.model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 2048,
            },
        }

    def request_extraction(
        self,
        transcript: str = "",
        existing_summary: str = "",
        *,
        turns: Optional[List[Dict[str, Any]]] = None,
        source_policy: str = LEGACY_TRANSCRIPT,
    ) -> Dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")

        request = self.build_request(
            transcript=transcript,
            existing_summary=existing_summary,
            turns=turns,
            source_policy=source_policy,
        )
        with httpx.Client(timeout=60) as client:
            response = client.post(
                request["url"],
                headers=request["headers"],
                json=request["json"],
            )
            response.raise_for_status()

        content = DeepSeekImageAnalysisProxy._extract_content(response.json())
        return self.parse_extraction(content)

    def dry_run_report(
        self,
        transcript: str = "",
        existing_summary: str = "",
        *,
        turns: Optional[List[Dict[str, Any]]] = None,
        source_policy: str = LEGACY_TRANSCRIPT,
    ) -> Dict[str, Any]:
        normalized_transcript = transcript.strip()
        if turns is None and not normalized_transcript:
            raise ValueError("transcript is required")
        if turns is not None:
            if not turns:
                raise ValueError("turns are required")
            if source_policy != USER_EVIDENCE_ONLY:
                raise ValueError("structured turns require sourcePolicy userEvidenceOnly")

        normalized_turns = turns or []
        return provider_dry_run_report(
            provider="deepseek",
            capability="kbExtract",
            method="POST",
            configured=bool(self.settings.deepseek_api_key),
            input_summary={
                "assistantTurnCount": sum(
                    1
                    for turn in normalized_turns
                    if isinstance(turn, dict) and str(turn.get("role") or "") == "assistant"
                ),
                "existingSummaryPresent": bool(existing_summary.strip()),
                "inputMode": "structuredTurns" if turns is not None else "legacyTranscript",
                "sourcePolicy": source_policy,
                "transcriptCharacterCount": len(normalized_transcript),
                "turnCount": len(normalized_turns),
                "userTurnCount": sum(
                    1
                    for turn in normalized_turns
                    if isinstance(turn, dict) and str(turn.get("role") or "") == "user"
                ),
            },
        )

    # Compatibility alias for the previous method name. Do not return an
    # upstream request from a diagnostics surface.
    def redacted_request(
        self,
        transcript: str = "",
        existing_summary: str = "",
        *,
        turns: Optional[List[Dict[str, Any]]] = None,
        source_policy: str = LEGACY_TRANSCRIPT,
    ) -> Dict[str, Any]:
        return self.dry_run_report(
            transcript=transcript,
            existing_summary=existing_summary,
            turns=turns,
            source_policy=source_policy,
        )

    @staticmethod
    def build_prompt(
        transcript: str,
        existing_summary: str,
        *,
        turns: Optional[List[Dict[str, Any]]] = None,
        source_policy: str = LEGACY_TRANSCRIPT,
    ) -> str:
        if turns is None:
            conversation_heading = "【本轮对话】"
            conversation_content = transcript
            evidence_rules = ""
            source_indices_example = "[1]"
        else:
            conversation_heading = "【本轮结构化对话（JSON）】"
            conversation_content = json.dumps(turns, ensure_ascii=False, separators=(",", ":"))
            first_user_index = next(
                (
                    turn.get("index")
                    for turn in turns
                    if isinstance(turn, dict) and turn.get("role") == "user"
                ),
                None,
            )
            source_indices_example = (
                json.dumps([first_user_index]) if isinstance(first_user_index, int) else "[]"
            )
            evidence_rules = f"""
5. sourcePolicy={source_policy}：只允许 role=user 的 turn 作为事实证据。
6. 每个实体必须输出至少一个 sourceTurnIndices，且所有索引都必须指向输入中 role=user 的 turn。
7. role=assistant 的内容仅可帮助理解上下文，不得作为证据，也不得提取只由 assistant 陈述的信息。
8. 不得编造、改写或引用输入中不存在的 turn index。
9. 输入中没有 role=user 的 turn 时，必须输出四个空数组。"""

        return f"""你是一个家庭记忆提取器。从以下对话中提取本轮新出现的信息。

【已有知识】（避免重复提取，只提取新信息）
{existing_summary}

{conversation_heading}
{conversation_content}

请输出严格的 JSON，不要 markdown，不要解释：
{{
  "people": [
    {{"name":"姓名或称呼","aliases":[],"relation":"关系","traits":[],"briefBio":"简介","sourceTurnIndices":{source_indices_example}}}
  ],
  "places": [
    {{"name":"地点名","category":"hometown/lived/visited/worked","latitude":null,"longitude":null,"description":"描述","relatedPeople":[],"sourceTurnIndices":{source_indices_example}}}
  ],
  "events": [
    {{"title":"事件标题","description":"描述","year":null,"month":null,"location":"地点名","participants":[],"sourceTurnIndices":{source_indices_example}}}
  ],
  "facts": [
    {{"statement":"一句事实陈述","confidence":"high/medium/low","relatedPeople":[],"relatedPlaces":[],"relatedEvents":[],"sourceTurnIndices":{source_indices_example}}}
  ]
}}

规则：
1. 用户明确陈述为 high，推测为 medium，不确定为 low。
2. 本轮没有新信息时输出四个空数组。
3. 不要把“妈妈、爸爸、爷爷、奶奶”等泛称单独作为人物，除非同时出现具体姓名或可区分身份。
4. 不要输出任何 JSON 之外的文字。{evidence_rules}"""

    @classmethod
    def parse_extraction(cls, content: str) -> Dict[str, Any]:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        parsed = DeepSeekImageAnalysisProxy._loads_json(cleaned)
        if parsed is None:
            extracted = DeepSeekImageAnalysisProxy.extract_json_substring(cleaned)
            parsed = DeepSeekImageAnalysisProxy._loads_json(extracted) if extracted is not None else None
        if parsed is None:
            raise ValueError("DeepSeek knowledge extraction returned non-JSON content")

        return {
            "people": cls._object_list(parsed.get("people")),
            "places": cls._object_list(parsed.get("places")),
            "events": cls._object_list(parsed.get("events")),
            "facts": cls._object_list(parsed.get("facts")),
        }

    @staticmethod
    def _object_list(value: Any) -> list:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]
