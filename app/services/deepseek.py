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
            '{"description":"...","detectedPeople":["..."],"scene":"...","occasion":"...",'
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

        return {
            "description": str(parsed.get("description") or ""),
            "detectedPeople": cls._string_list(parsed.get("detectedPeople")),
            "scene": str(parsed.get("scene") or ""),
            "occasion": str(parsed.get("occasion") or ""),
            "mood": str(parsed.get("mood") or ""),
            "estimatedDecade": cls._int_or_none(parsed.get("estimatedDecade")),
        }

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
