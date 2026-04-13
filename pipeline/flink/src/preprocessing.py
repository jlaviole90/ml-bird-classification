"""Frame preprocessing utilities for the Flink inference pipeline."""

from __future__ import annotations

import imagehash
import numpy as np
from PIL import Image
import io


def compute_phash(frame_bytes: bytes, hash_size: int = 8) -> str:
    """Compute a perceptual hash from JPEG bytes for deduplication."""
    img = Image.open(io.BytesIO(frame_bytes))
    return str(imagehash.phash(img, hash_size=hash_size))


def is_near_duplicate(hash_a: str, hash_b: str, max_distance: int = 8) -> bool:
    """Return True if two perceptual hashes are within Hamming distance threshold."""
    h1 = imagehash.hex_to_hash(hash_a)
    h2 = imagehash.hex_to_hash(hash_b)
    return (h1 - h2) <= max_distance


def decode_frame_for_display(frame_bytes: bytes) -> np.ndarray:
    """Decode JPEG bytes to a numpy BGR array (for any debugging / visualization)."""
    buf = np.frombuffer(frame_bytes, dtype=np.uint8)
    import cv2
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
