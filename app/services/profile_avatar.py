"""Bounded owner avatar normalization; never retain original photo metadata."""
import base64
import binascii
from hashlib import sha256
from io import BytesIO
from PIL import Image, UnidentifiedImageError

MAX_AVATAR_BYTES = 128 * 1024
AVATAR_SIDE = 256


def normalize_avatar(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_AVATAR_BYTES + 2) // 3):
        raise ValueError("avatarTooLargeOrMissing")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AVATAR_BYTES:
            raise ValueError("avatarTooLarge")
        with Image.open(BytesIO(raw), formats=["JPEG"]) as source:
            if source.size != (AVATAR_SIDE, AVATAR_SIDE) or getattr(source, "n_frames", 1) != 1:
                raise ValueError("avatarDimensionsInvalid")
            source.load()  # Fully decode; reject truncated files.
            # A fresh image drops EXIF/GPS, ICC, XMP and comments, not only orientation.
            clean = Image.new("RGB", source.size)
            clean.paste(source.convert("RGB"))
            out = BytesIO()
            clean.save(out, format="JPEG", quality=85, optimize=True)
        normalized = out.getvalue()
        if len(normalized) > MAX_AVATAR_BYTES:
            raise ValueError("avatarTooLarge")
    except (binascii.Error, UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("avatarImageInvalid") from exc
    return {"mimeType":"image/jpeg", "width":AVATAR_SIDE, "height":AVATAR_SIDE,
            "imageBase64":base64.b64encode(normalized).decode("ascii"), "sha256":sha256(normalized).hexdigest()}
