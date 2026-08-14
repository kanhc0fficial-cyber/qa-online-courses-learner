"""Preload the multilingual faster-whisper small model without Hugging Face Xet."""

import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from faster_whisper import WhisperModel


WhisperModel("small", device="cpu", compute_type="int8")
print("READY")
