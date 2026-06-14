from __future__ import annotations


OFFSET_BASIS = 1_469_598_103_934_665_603
FNV_PRIME = 1_099_511_628_211
FNV_MASK = (1 << 64) - 1


def normalized_phone_digits(phone: str) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def stable_user_id(phone: str) -> str:
    normalized = normalized_phone_digits(phone)
    source = normalized or str(phone or "").strip()
    value = OFFSET_BASIS
    for byte in source.encode("utf-8"):
        value ^= byte
        value = (value * FNV_PRIME) & FNV_MASK
    return f"user_{value:016x}"
