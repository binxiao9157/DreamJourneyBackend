"""Server-side assessment for private VoiceProfile training samples.

This is intentionally a small, inspectable first contract: it accepts PCM WAV
only.  Formats that cannot be decoded and measured by the server are rejected
instead of being forwarded to a voice-clone provider on the caller's claim.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import sys
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


VOICE_SAMPLE_ASSESSMENT_SCHEMA_VERSION = "voice-sample-assessment-v1"
VOICE_SAMPLE_VERSION = "voice-sample-v1"
VOICE_SAMPLE_ALLOWED_FORMAT = "wav"
VOICE_SAMPLE_MIN_DURATION_MILLISECONDS = 10_000
VOICE_SAMPLE_MAX_DURATION_MILLISECONDS = 30_000
VOICE_SAMPLE_MIN_SAMPLE_RATE_HZ = 16_000
VOICE_SAMPLE_MAX_SAMPLE_RATE_HZ = 48_000
VOICE_SAMPLE_MAX_BYTES = 10 * 1024 * 1024
VOICE_SAMPLE_MIN_ESTIMATED_SNR_DB = 12.0
VOICE_SAMPLE_MIN_RMS_DBFS = -42.0
VOICE_SAMPLE_MAX_PEAK_DBFS = -0.2


class VoiceSampleAssessmentError(ValueError):
    """A user-safe rejection reason.  It must never include media bytes."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VoiceSampleAssessment:
    sample_version: str
    sample_hash: str
    audio_format: str
    byte_count: int
    duration_milliseconds: int
    sample_rate_hz: int
    channel_count: int
    sample_width_bits: int
    estimated_snr_db: float
    rms_dbfs: float
    peak_dbfs: float
    assessed_at: str

    def persistence_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": VOICE_SAMPLE_ASSESSMENT_SCHEMA_VERSION,
            "sampleVersion": self.sample_version,
            "sampleHash": self.sample_hash,
            "format": self.audio_format,
            "byteCount": self.byte_count,
            "durationMilliseconds": self.duration_milliseconds,
            "sampleRateHz": self.sample_rate_hz,
            "channelCount": self.channel_count,
            "sampleWidthBits": self.sample_width_bits,
            "estimatedSnrDb": self.estimated_snr_db,
            "rmsDbfs": self.rms_dbfs,
            "peakDbfs": self.peak_dbfs,
            "assessmentState": "accepted",
            "assessedAt": self.assessed_at,
        }

    def public_projection(self) -> dict[str, Any]:
        return {
            "schemaVersion": VOICE_SAMPLE_ASSESSMENT_SCHEMA_VERSION,
            "sampleVersion": self.sample_version,
            "format": self.audio_format,
            "durationMilliseconds": self.duration_milliseconds,
            "sampleRateHz": self.sample_rate_hz,
            "channelCount": self.channel_count,
            "sampleWidthBits": self.sample_width_bits,
            "estimatedSnrDb": self.estimated_snr_db,
            "rmsDbfs": self.rms_dbfs,
            "peakDbfs": self.peak_dbfs,
            "assessmentState": "accepted",
            "assessedAt": self.assessed_at,
        }


def assess_voice_sample(
    *,
    audio_base64: str,
    audio_format: str,
    sample_version: str,
    now: datetime | None = None,
) -> VoiceSampleAssessment:
    """Decode and quality-gate a WAV sample before any provider call."""

    normalized_version = str(sample_version or "").strip()
    if normalized_version != VOICE_SAMPLE_VERSION:
        raise VoiceSampleAssessmentError("unsupportedSampleVersion")
    normalized_format = str(audio_format or "").strip().lower().lstrip(".")
    if normalized_format != VOICE_SAMPLE_ALLOWED_FORMAT:
        raise VoiceSampleAssessmentError("unsupportedSampleFormat")
    try:
        audio_bytes = base64.b64decode(str(audio_base64 or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise VoiceSampleAssessmentError("invalidSampleEncoding") from exc
    if not audio_bytes:
        raise VoiceSampleAssessmentError("emptySample")
    if len(audio_bytes) > VOICE_SAMPLE_MAX_BYTES:
        raise VoiceSampleAssessmentError("sampleTooLarge")

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise VoiceSampleAssessmentError("unsupportedSampleCompression")
            channel_count = reader.getnchannels()
            sample_width_bytes = reader.getsampwidth()
            sample_rate_hz = reader.getframerate()
            frame_count = reader.getnframes()
            frame_bytes = reader.readframes(frame_count)
    except VoiceSampleAssessmentError:
        raise
    except (EOFError, wave.Error) as exc:
        raise VoiceSampleAssessmentError("invalidWavSample") from exc

    if channel_count != 1:
        raise VoiceSampleAssessmentError("sampleMustBeMono")
    if sample_width_bytes != 2:
        raise VoiceSampleAssessmentError("sampleMustBePcm16")
    if not VOICE_SAMPLE_MIN_SAMPLE_RATE_HZ <= sample_rate_hz <= VOICE_SAMPLE_MAX_SAMPLE_RATE_HZ:
        raise VoiceSampleAssessmentError("unsupportedSampleRate")
    duration_milliseconds = round(frame_count * 1000 / sample_rate_hz)
    if duration_milliseconds < VOICE_SAMPLE_MIN_DURATION_MILLISECONDS:
        raise VoiceSampleAssessmentError("sampleTooShort")
    if duration_milliseconds > VOICE_SAMPLE_MAX_DURATION_MILLISECONDS:
        raise VoiceSampleAssessmentError("sampleTooLong")

    estimated_snr_db, rms_dbfs, peak_dbfs = _quality_metrics(
        frame_bytes,
        sample_rate_hz=sample_rate_hz,
    )
    if rms_dbfs < VOICE_SAMPLE_MIN_RMS_DBFS:
        raise VoiceSampleAssessmentError("sampleTooQuiet")
    if peak_dbfs > VOICE_SAMPLE_MAX_PEAK_DBFS:
        raise VoiceSampleAssessmentError("sampleClipped")
    if estimated_snr_db < VOICE_SAMPLE_MIN_ESTIMATED_SNR_DB:
        raise VoiceSampleAssessmentError("sampleNoiseTooHigh")

    assessed_at = _aware_iso(now or datetime.now(timezone.utc))
    return VoiceSampleAssessment(
        sample_version=normalized_version,
        sample_hash="sha256:" + hashlib.sha256(audio_bytes).hexdigest(),
        audio_format=normalized_format,
        byte_count=len(audio_bytes),
        duration_milliseconds=duration_milliseconds,
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        sample_width_bits=sample_width_bytes * 8,
        estimated_snr_db=estimated_snr_db,
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        assessed_at=assessed_at,
    )


def _quality_metrics(frame_bytes: bytes, *, sample_rate_hz: int) -> tuple[float, float, float]:
    samples = array("h")
    samples.frombytes(frame_bytes)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise VoiceSampleAssessmentError("emptySample")

    peak = max(abs(sample) for sample in samples)
    if peak == 0:
        raise VoiceSampleAssessmentError("sampleTooQuiet")
    total_energy = sum(sample * sample for sample in samples)
    overall_rms = math.sqrt(total_energy / len(samples))
    rms_dbfs = _dbfs(overall_rms)
    peak_dbfs = _dbfs(float(peak))

    # Twenty millisecond RMS windows provide a conservative speech/noise-floor
    # estimate without claiming to perform provider-grade denoising.
    window_size = max(1, sample_rate_hz // 50)
    window_rms = []
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        if window:
            window_rms.append(math.sqrt(sum(value * value for value in window) / len(window)))
    ordered = sorted(window_rms)
    noise_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.10)))
    signal_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.90)))
    noise_floor = max(1.0, ordered[noise_index])
    signal_level = max(noise_floor, ordered[signal_index])
    estimated_snr_db = round(min(99.0, 20 * math.log10(signal_level / noise_floor)), 2)
    return estimated_snr_db, round(rms_dbfs, 2), round(peak_dbfs, 2)


def _dbfs(value: float) -> float:
    return 20 * math.log10(max(value, 1.0) / 32768.0)


def _aware_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("sample assessment time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "VOICE_SAMPLE_ALLOWED_FORMAT",
    "VOICE_SAMPLE_ASSESSMENT_SCHEMA_VERSION",
    "VOICE_SAMPLE_MAX_DURATION_MILLISECONDS",
    "VOICE_SAMPLE_MIN_DURATION_MILLISECONDS",
    "VOICE_SAMPLE_VERSION",
    "VoiceSampleAssessment",
    "VoiceSampleAssessmentError",
    "assess_voice_sample",
]
