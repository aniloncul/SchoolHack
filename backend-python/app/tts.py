"""TTS service using Microsoft Azure / Edge Neural Voices (Florian, Ahmet, Andrew).

Supports:
1. Microsoft Edge TTS (100% free, no API key required, identical Azure neural voices).
2. Azure Cognitive Services Speech (if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set).
3. Speech normalization for mathematical notation (German & Turkish) and markdown stripping.
4. Fast on-disk caching using SHA256 of the normalized text and voice.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

try:
    import edge_tts
except ImportError:
    edge_tts = None  # type: ignore

CACHE_DIR = Path("/tmp/schoolhack_tts_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

VOICES = {
    "de": "de-DE-FlorianMultilingualNeural",
    "tr": "tr-TR-AhmetNeural",
    "en": "en-US-AndrewMultilingualNeural",
}


def normalize_for_speech(text: str, lang: str = "de") -> str:
    """Transform markdown and LaTeX formulas into clean, speakable text."""
    if not text:
        return ""

    # Strip code blocks and inline code
    t = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    # Strip markdown bold / italic
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    # Strip headers, blockquotes, horizontal rules
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*>\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*_]{3,}\s*$", "", t, flags=re.MULTILINE)
    # Strip markdown links [label](url) -> label
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)

    if lang.startswith("de"):
        # German math speech normalization
        t = t.replace("{,}", ",")
        t = re.sub(r"f\s*\(\s*x\s*\)", "f von x", t)
        t = re.sub(r"g\s*\(\s*x\s*\)", "g von x", t)
        t = re.sub(r"h\s*\(\s*x\s*\)", "h von x", t)
        t = re.sub(r"x\s*\^\s*2", "x Quadrat", t)
        t = re.sub(r"x\s*\^\s*3", "x hoch drei", t)
        t = re.sub(r"x\s*\^\s*(\d+)", r"x hoch \1", t)
        t = re.sub(r"\\cdot|\*", " mal ", t)
        t = re.sub(r"\\frac\s*\{([^}]+)\}\s*\{([^}]+)\}", r"\1 geteilt durch \2", t)
        t = re.sub(r"\\sqrt\s*\{([^}]+)\}", r"Wurzel aus \1", t)
        t = re.sub(r"\\pm", " plus minus ", t)
        t = re.sub(r"\\le(?:q)?|<=|≤", " kleiner gleich ", t)
        t = re.sub(r"\\ge(?:q)?|>=|≥", " größer gleich ", t)
        t = re.sub(r"\\neq|!=|≠", " ungleich ", t)
        t = re.sub(r"P_([1-9])", r"P \1", t)
        t = re.sub(r"x_([1-9])", r"x \1", t)
    elif lang.startswith("tr"):
        # Turkish math speech normalization
        t = t.replace("{,}", ",")
        t = re.sub(r"f\s*\(\s*x\s*\)", "f x", t)
        t = re.sub(r"g\s*\(\s*x\s*\)", "g x", t)
        t = re.sub(r"x\s*\^\s*2", "x kare", t)
        t = re.sub(r"x\s*\^\s*3", "x küp", t)
        t = re.sub(r"x\s*\^\s*(\d+)", r"x üzeri \1", t)
        t = re.sub(r"\\cdot|\*", " çarpı ", t)
        t = re.sub(r"\\frac\s*\{([^}]+)\}\s*\{([^}]+)\}", r"\1 bölü \2", t)
        t = re.sub(r"\\sqrt\s*\{([^}]+)\}", r"karekök \1", t)
        t = re.sub(r"\\pm", " artı eksi ", t)
        t = re.sub(r"\\le(?:q)?|<=|≤", " küçük eşittir ", t)
        t = re.sub(r"\\ge(?:q)?|>=|≥", " büyük eşittir ", t)
        t = re.sub(r"\\neq|!=|≠", " eşit değildir ", t)
    else:
        # English math speech normalization
        t = re.sub(r"f\s*\(\s*x\s*\)", "f of x", t)
        t = re.sub(r"g\s*\(\s*x\s*\)", "g of x", t)
        t = re.sub(r"x\s*\^\s*2", "x squared", t)
        t = re.sub(r"\\cdot|\*", " times ", t)
        t = re.sub(r"\\le(?:q)?|<=|≤", " less than or equal to ", t)
        t = re.sub(r"\\ge(?:q)?|>=|≥", " greater than or equal to ", t)

    # Clean up LaTeX delims and leftovers
    t = t.replace("$", " ")
    t = re.sub(r"\\[a-zA-Z]+", " ", t)  # strip unknown \command
    t = re.sub(r"[{}\\]", " ", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


async def synthesize_speech(
    text: str,
    lang: str = "de",
    voice: Optional[str] = None,
    rate: str = "+0%",
) -> bytes:
    """Generate MP3 audio bytes using Azure Neural Voice (via edge-tts or Azure API)."""
    norm_text = normalize_for_speech(text, lang=lang)
    if not norm_text:
        return b""

    chosen_voice = voice or VOICES.get(lang[:2], VOICES["de"])

    # Check cache
    cache_key = hashlib.sha256(f"{chosen_voice}:{rate}:{norm_text}".encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"{cache_key}.mp3"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path.read_bytes()

    # Try edge-tts first (100% free, Azure Neural voice farm)
    if edge_tts is not None:
        try:
            communicate = edge_tts.Communicate(norm_text, chosen_voice, rate=rate)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            audio_bytes = b"".join(chunks)
            if audio_bytes:
                # Save cache atomically
                tmp_path = cache_path.with_suffix(".tmp")
                tmp_path.write_bytes(audio_bytes)
                tmp_path.replace(cache_path)
                return audio_bytes
        except Exception:
            # Fall back to Azure Speech if configured
            pass

    # Optional: Official Azure Speech Services REST API if credentials exist
    azure_key = os.getenv("AZURE_SPEECH_KEY")
    azure_region = os.getenv("AZURE_SPEECH_REGION", "eastus")
    if azure_key:
        try:
            import urllib.request
            url = f"https://{azure_region}.tts.speech.microsoft.com/cognitiveservices/v1"
            ssml = (
                f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
                f"xml:lang='{lang}'>"
                f"<voice name='{chosen_voice}'>{norm_text}</voice></speak>"
            )
            req = urllib.request.Request(
                url,
                data=ssml.encode("utf-8"),
                headers={
                    "Ocp-Apim-Subscription-Key": azure_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                    "User-Agent": "SchoolHackTTS",
                },
            )
            audio_bytes = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=10).read())
            if audio_bytes:
                cache_path.write_bytes(audio_bytes)
                return audio_bytes
        except Exception:
            pass

    return b""
