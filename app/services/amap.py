from typing import Optional
import json
import urllib.request
from urllib.parse import urlencode

from app.core.config import Settings


class AMapDistrictProxy:
    endpoint = "https://restapi.amap.com/v3/config/district"

    def __init__(self, settings: Settings):
        self.settings = settings

    def build_url(self, keyword: str, subdistrict: int = 0, extensions: str = "all") -> str:
        api_key = self.settings.amap_web_service_key
        if not api_key:
            raise ValueError("AMapWebServiceKey is not configured")
        if not keyword.strip():
            raise ValueError("keyword is required")
        query = urlencode(
            {
                "key": api_key,
                "keywords": keyword,
                "subdistrict": str(subdistrict),
                "extensions": extensions,
                "output": "JSON",
            }
        )
        return f"{self.endpoint}?{query}"

    def request_district(self, keyword: str, subdistrict: int = 0, extensions: str = "all") -> dict:
        url = self.build_url(keyword=keyword, subdistrict=subdistrict, extensions=extensions)
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)

    @staticmethod
    def redact_url(url: str, key: Optional[str]) -> str:
        if not key:
            return url
        return url.replace(key, "<redacted>")
