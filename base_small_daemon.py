import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk

load_dotenv()

SPEECH_KEY   = os.getenv("SPEECH_KEY", "")
SPEECH_REGION= os.getenv("SPEECH_REGION", "")
CUSTOM_ENDPOINT_ID  = os.getenv("CUSTOM_ENDPOINT_ID", "")      # to follow: custom daemon endpoint id
LOCALE       = os.getenv("LOCALE", "en-US")
INPUT_DIR    = os.getenv("INPUT_DIR", "./incoming_audio")
USE_MIC      = os.getenv("USE_MIC", "false").lower() == "true"

def build_speech_config() -> speechsdk.SpeechConfig:
    if not SPEECH_KEY or not SPEECH_REGION:
        raise RuntimeError("Set SPEECH_KEY and SPEECH_REGION in .env")

    cfg = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    # source language
    cfg.speech_recognition_language = LOCALE

    # for the custom daemon's custom endpoint
    if CUSTOM_ENDPOINT_ID:
        cfg.endpoint_id = CUSTOM_ENDPOINT_ID 

    # optional tuning:
    cfg.set_profanity(speechsdk.ProfanityOption.Masked)
    cfg.enable_dictation()  # allows continuous-like punctuation

    return cfg

def transcribe_file(wav_path: Path) -> Optional[str]:
    cfg = build_speech_config()
    audio_input = speechsdk.AudioConfig(filename=str(wav_path))
    recognizer = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_input)

    print(f"[STT] Transcribing: {wav_path.name} (locale={LOCALE})")
    
    # recognize once per file (simple); longer files need chunking or batch/fast STT.
    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        print(f"[STT] Text: {result.text}")
        return result.text
    elif result.reason == speechsdk.ResultReason.NoMatch:
        print("[STT] No speech could be recognized.")
    else:
        print(f"[STT] Error: {result.reason} {result.cancellation_details.error_details}")
    return None

def transcribe_microphone():
    cfg = build_speech_config()
    audio_input = speechsdk.AudioConfig(use_default_microphone=True)
    recognizer = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_input)

    print(f"[STT] Listening on microphone (locale={LOCALE}). Press Ctrl+C to stop.")
    try:
        while True:
            print("[STT] Say something…")
            result = recognizer.recognize_once()  # simple utterance loop
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                print(f"[STT] You said: {result.text}")
            else:
                print(f"[STT] Result: {result.reason}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[STT] Stopped.")

def watch_folder():
    input_dir = Path(INPUT_DIR)
    input_dir.mkdir(parents=True, exist_ok=True)
    print(f"[Daemon] Watching folder: {input_dir.resolve()} (drop .wav/.mp3/.mp4 etc.)")

    seen = set()
    try:
        while True:
            # naive polling for scale, use watchdog/inotify, etc.
            for p in input_dir.iterdir():
                if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".mp4", ".m4a", ".flac"} and p not in seen:
                    seen.add(p)
                    transcribe_file(p)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Daemon] Stopped.")

if __name__ == "__main__":
    if USE_MIC:
        transcribe_microphone()
    else:
        watch_folder()
