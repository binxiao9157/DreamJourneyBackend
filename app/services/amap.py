from typing import Optional
import json
import urllib.request
from urllib.parse import urlencode

from app.core.config import Settings
from app.observability.redaction import provider_dry_run_report


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

    def dry_run_report(
        self,
        keyword: str,
        subdistrict: int = 0,
        extensions: str = "all",
    ) -> dict:
        if not self.settings.amap_web_service_key:
            raise ValueError("AMapWebServiceKey is not configured")
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            raise ValueError("keyword is required")
        return provider_dry_run_report(
            provider="amap",
            capability="districtLookup",
            method="GET",
            configured=True,
            input_summary={
                "extensionsMode": "all" if extensions == "all" else "other",
                "queryCharacterCount": len(normalized_keyword),
                "subdistrictDepth": max(0, int(subdistrict)),
            },
        )

    @staticmethod
    def redact_url(url: str, key: Optional[str]) -> str:
        if not key:
            return url
        return url.replace(key, "<redacted>")
