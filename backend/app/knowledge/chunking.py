"""Deterministic Unicode chunking with original text preserved."""
import re

MAX_CHARS = 800
OVERLAP = 80

def split_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + MAX_CHARS)
        if end < len(text):
            boundaries = list(re.finditer(r"[。！？；.!?;\n]", text[start:end]))
            if boundaries and boundaries[-1].end() > OVERLAP:
                end = start + boundaries[-1].end()
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(start + 1, end - OVERLAP)
    return chunks
