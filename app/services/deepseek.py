import json
from typing import Any, Dict, Optional

import httpx

from app.core.config import Settings


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

    def redacted_request(self, image_base64: str) -> Dict[str, Any]:
        request = self.build_request(image_base64)
        request["headers"] = {
            "Content-Type": "application/json",
            "Authorization": "Bearer <server-side>",
        }
        return request

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
    def failure_contract(reason: str = "provider_unavailable", provider_message: str = "") -> Dict[str, Any]:
        payload = {
            "analysisStatus": "failed",
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
            "provider": "deepseek",
        }
        if provider_message:
            payload["providerMessage"] = provider_message
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

    def build_request(self, transcript: str, existing_summary: str = "") -> Dict[str, Any]:
        transcript = transcript.strip()
        if not transcript:
            raise ValueError("transcript is required")

        prompt = self.build_prompt(
            transcript=transcript,
            existing_summary=existing_summary or "（暂无已有知识）",
        )
        messages = [
            {"role": "system", "content": "You are a precise strict JSON extractor. 只输出严格JSON。"},
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

    def request_extraction(self, transcript: str, existing_summary: str = "") -> Dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")

        request = self.build_request(transcript=transcript, existing_summary=existing_summary)
        with httpx.Client(timeout=60) as client:
            response = client.post(
                request["url"],
                headers=request["headers"],
                json=request["json"],
            )
            response.raise_for_status()

        content = DeepSeekImageAnalysisProxy._extract_content(response.json())
        return self.parse_extraction(content)

    def redacted_request(self, transcript: str, existing_summary: str = "") -> Dict[str, Any]:
        request = self.build_request(transcript=transcript, existing_summary=existing_summary)
        request["headers"] = {
            "Content-Type": "application/json",
            "Authorization": "Bearer <server-side>",
        }
        return request

    @staticmethod
    def build_prompt(transcript: str, existing_summary: str) -> str:
        return f"""你是一个家庭记忆提取器。从以下对话中提取本轮新出现的信息。

【已有知识】（避免重复提取，只提取新信息）
{existing_summary}

【本轮对话】
{transcript}

请输出严格的 JSON，不要 markdown，不要解释：
{{
  "people": [
    {{"name":"姓名或称呼","aliases":[],"relation":"关系","traits":[],"briefBio":"简介","sourceTurnIndices":[1]}}
  ],
  "places": [
    {{"name":"地点名","category":"hometown/lived/visited/worked","latitude":null,"longitude":null,"description":"描述","relatedPeople":[],"sourceTurnIndices":[1]}}
  ],
  "events": [
    {{"title":"事件标题","description":"描述","year":null,"month":null,"location":"地点名","participants":[],"sourceTurnIndices":[1]}}
  ],
  "facts": [
    {{"statement":"一句事实陈述","confidence":"high/medium/low","relatedPeople":[],"relatedPlaces":[],"relatedEvents":[],"sourceTurnIndices":[1]}}
  ]
}}

规则：
1. 用户明确陈述为 high，推测为 medium，不确定为 low。
2. 本轮没有新信息时输出四个空数组。
3. 不要把“妈妈、爸爸、爷爷、奶奶”等泛称单独作为人物，除非同时出现具体姓名或可区分身份。
4. 不要输出任何 JSON 之外的文字。"""

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
