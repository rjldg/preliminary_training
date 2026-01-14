import json
import os
import sys
import time
from typing import Dict, Any, Optional

import requests
from requests import Response
from dotenv import load_dotenv

load_dotenv()


#!/usr/bin/env python3
"""
Fast Transcription API sample (Azure Speech)

Features:
- Inline audio upload OR public URL
- Key-based or Entra ID (Bearer) authentication
- Options: locales / language ID, diarization, multi-channel, phrase list, profanity filter
- Simple retry on 429
- Prints combined transcription + sample phrase details

Docs: Based on Microsoft Learn "Use the fast transcription API" (API version 2025-10-15).

Author: Your Name
"""

import json
import os
import sys
import time
from typing import Dict, Any, Optional

import requests
from requests import Response

# Configuration via environment variables

SPEECH_REGION = os.getenv("SPEECH_REGION", "<YourRegion>")

# Auth method
# 1) Subscription key (simplest to start)
SPEECH_KEY = os.getenv("SPEECH_KEY", "<YourSpeechResourceKey>")
# 2) Microsoft Entra ID token (recommended for production)
ENTRA_ACCESS_TOKEN = os.getenv("ENTRA_ACCESS_TOKEN", "")

# API version per docs
API_VERSION = "2025-10-15"

# Provide ONE audio source:
AUDIO_FILE_PATH = os.getenv("AUDIO_FILE_PATH", "")
AUDIO_URL = os.getenv("AUDIO_URL", "")

# Optional transcription settings:

LOCALES = json.loads(os.getenv("LOCALES_JSON", '["en-US"]'))

# Enable diarization (speaker separation on a single channel)
DIARIZATION_ENABLED = os.getenv("DIARIZATION_ENABLED", "false").lower() == "true"
DIARIZATION_MAX_SPEAKERS = int(os.getenv("DIARIZATION_MAX_SPEAKERS", "2"))

# Multi-channel (for stereo files).
CHANNELS = json.loads(os.getenv("CHANNELS_JSON", "[]"))  # [] lets the service merge channels by default

# Phrase list to boost domain-specific terms
PHRASE_LIST = json.loads(os.getenv("PHRASE_LIST_JSON", '[]'))

# Profanity filter
PROFANITY_FILTER_MODE = os.getenv("PROFANITY_FILTER_MODE", "Masked")

# Basic retry settings for 429 throttling
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "2.0"))

# Helper functions

def build_endpoint(region: str, api_version: str) -> str:
    return f"https://{region}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version={api_version}"

def build_headers() -> Dict[str, str]:
    """
    Returns headers for authentication.
    Prefers Entra ID token if provided, otherwise uses the subscription key.
    """
    if ENTRA_ACCESS_TOKEN:
        return {
            "Authorization": f"Bearer {ENTRA_ACCESS_TOKEN}",
        }
    elif SPEECH_KEY and SPEECH_KEY != "<YourSpeechResourceKey>":
        return {
            "Ocp-Apim-Subscription-Key": SPEECH_KEY,
        }
    else:
        raise ValueError("No authentication configured. Set ENTRA_ACCESS_TOKEN or SPEECH_KEY.")

def build_definition_payload() -> Dict[str, Any]:
    """
    Builds the 'definition' JSON object sent as a multipart field.
    Only includes properties when set, to keep the payload clean.
    """
    definition: Dict[str, Any] = {}

    # Locales behavior:
    # - One locale: service uses it directly (best latency/accuracy if known).
    # - Multiple locales: language identification among candidates.
    # - No locales (empty list): service uses latest multilingual model to ID and transcribe.
    if LOCALES:  # include only if user provided any locale(s)
        definition["locales"] = LOCALES

    # Diarization
    if DIARIZATION_ENABLED:
        definition["diarization"] = {
            "enabled": True,
            "maxSpeakers": DIARIZATION_MAX_SPEAKERS
        }

    # Multi-channel
    # Include only when explicitly specified; otherwise service merges channels.
    if CHANNELS:
        definition["channels"] = CHANNELS

    # Phrase list (API version 2025-10-15 per docs)
    if PHRASE_LIST:
        definition["phraseList"] = {
            "phrases": PHRASE_LIST
        }

    # Profanity filter
    if PROFANITY_FILTER_MODE:
        definition["profanityFilterMode"] = PROFANITY_FILTER_MODE

    return definition

def post_with_retries(url: str, headers: Dict[str, str], files: Dict[str, Any]) -> Response:
    """
    POST with basic retry logic for 429 Too Many Requests.
    """
    attempt = 0
    while True:
        response = requests.post(url, headers=headers, files=files, timeout=300)
        if response.status_code != 429:
            return response

        attempt += 1
        if attempt > MAX_RETRIES:
            return response

        # Honor Retry-After if provided; otherwise exponential backoff
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
        else:
            wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))

        print(f"[{response.status_code}] Throttled. Retrying in {wait:.1f}s (attempt {attempt}/{MAX_RETRIES})...")
        time.sleep(wait)

def pretty_print_response(resp: Response) -> None:
    try:
        data = resp.json()
    except Exception:
        print("Raw response text:\n", resp.text)
        return

    # Print top-level info
    dur_ms = data.get("durationMilliseconds")
    print("\n=== Transcription Summary ===")
    if dur_ms is not None:
        print(f"Audio duration (ms): {dur_ms}")

    # Combined text
    combined = data.get("combinedPhrases", [])
    if combined:
        full_text = " ".join(p.get("text", "") for p in combined).strip()
        print("\n--- Combined transcription ---")
        print(full_text)

    # First few phrases with timestamps
    phrases = data.get("phrases", [])
    if phrases:
        print("\n--- First 5 phrases (with timestamps) ---")
        for p in phrases[:5]:
            off = p.get("offsetMilliseconds")
            dur = p.get("durationMilliseconds")
            txt = p.get("text", "")
            loc = p.get("locale", "")
            conf = p.get("confidence")
            print(f"[offset={off}ms dur={dur}ms loc={loc} conf={conf}] {txt}")

def transcribe_inline_file() -> None:
    """Send local audio file inline as multipart/form-data."""
    if not AUDIO_FILE_PATH or not os.path.isfile(AUDIO_FILE_PATH):
        raise FileNotFoundError(f"AUDIO_FILE_PATH not found: {AUDIO_FILE_PATH}")

    url = build_endpoint(SPEECH_REGION, API_VERSION)
    headers = build_headers()

    definition = build_definition_payload()

    with open(AUDIO_FILE_PATH, "rb") as f:
        files = {
            # form field 'audio' is the file contents
            "audio": (os.path.basename(AUDIO_FILE_PATH), f, "application/octet-stream"),
            # form field 'definition' is a JSON string
            "definition": (None, json.dumps(definition), "application/json"),
        }

        print(f"POST {url}")
        resp = post_with_retries(url, headers, files)

    print(f"\nStatus: {resp.status_code}")
    if not resp.ok:
        print("Error body:\n", resp.text)
        resp.raise_for_status()

    pretty_print_response(resp)

def transcribe_from_url() -> None:
    """Point the service to a publicly accessible audio URL."""
    if not AUDIO_URL:
        raise ValueError("AUDIO_URL is empty. Provide a public URL to your audio file.")

    url = build_endpoint(SPEECH_REGION, API_VERSION)
    headers = build_headers()

    definition = build_definition_payload()
    # Add audioUrl into the definition for URL-based transcription
    definition["audioUrl"] = AUDIO_URL

    files = {
        "definition": (None, json.dumps(definition), "application/json"),
    }

    print(f"POST {url}")
    resp = post_with_retries(url, headers, files)

    print(f"\nStatus: {resp.status_code}")
    if not resp.ok:
        print("Error body:\n", resp.text)
        resp.raise_for_status()

    pretty_print_response(resp)

def main():
    if AUDIO_FILE_PATH and os.path.isfile(AUDIO_FILE_PATH):
        print("Using inline file upload...")
        transcribe_inline_file()
    elif AUDIO_URL:
        print("Using public URL...")
        transcribe_from_url()
    else:
        print("Please set AUDIO_FILE_PATH (existing file) or AUDIO_URL (public link).")
        sys.exit(1)

if __name__ == "__main__":
    main()

