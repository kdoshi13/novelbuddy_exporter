import os
import sys
import json
import wave
from pathlib import Path
from urllib.request import urlopen

# Import existing CLI logic from scrape_io
import scrape_io

# TTS Setup
TTS_AVAILABLE = False
try:
    from piper import PiperVoice
    from piper.download_voices import download_voice
    TTS_AVAILABLE = True
except ImportError:
    PiperVoice = None
    download_voice = None

VOICES_DIR = Path(__file__).resolve().parent / "piper_voices"
VOICES_DIR.mkdir(exist_ok=True)


def _list_downloaded_voices():
    return sorted(
        p.stem for p in VOICES_DIR.glob("*.onnx")
        if (VOICES_DIR / f"{p.stem}.onnx.json").exists()
    )


def _fetch_catalog():
    from piper.download_voices import VOICES_JSON
    with urlopen(VOICES_JSON) as resp:
        return json.load(resp)


def _model_path(voice_id: str) -> Path:
    return VOICES_DIR / f"{voice_id}.onnx"


def _ensure_voice(voice_id: str) -> Path:
    model = _model_path(voice_id)
    if not model.exists():
        print(f"Downloading voice {voice_id}...")
        download_voice(voice_id, VOICES_DIR)
    return model


def synthesize_text():
    if not TTS_AVAILABLE:
        print("TTS not available. Please install piper-tts to use this feature.")
        return
        
    voices = _list_downloaded_voices()
    if not voices:
        print("No voices downloaded. Please download a voice first (Option 5).")
        return
        
    print("\nAvailable voices:")
    for i, v in enumerate(voices):
        print(f"{i+1}. {v}")
    
    v_choice = input("Select a voice number: ").strip()
    if not v_choice.isdigit() or not (1 <= int(v_choice) <= len(voices)):
        print("Invalid choice.")
        return
    
    voice_id = voices[int(v_choice) - 1]
    
    txt_path = input("Enter path to a .txt file to read: ").strip().strip('"')
    if not os.path.exists(txt_path):
        print("File not found.")
        return
        
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
        
    if not text:
        print("File is empty.")
        return
        
    out_wav = input("Output WAV filename [output.wav]: ").strip() or "output.wav"
    if not out_wav.endswith(".wav"):
        out_wav += ".wav"
        
    print(f"Loading voice {voice_id}...")
    model = _ensure_voice(voice_id)
    voice = PiperVoice.load(str(model))
    
    print(f"Synthesizing audio to {out_wav}...")
    with wave.open(out_wav, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    print("Done!")


def download_voice_cli():
    if not TTS_AVAILABLE:
        print("TTS not available. Please install piper-tts.")
        return
        
    print("Fetching catalog...")
    try:
        catalog = _fetch_catalog()
    except Exception as e:
        print(f"Failed to fetch catalog: {e}")
        return
        
    en_voices = [vid for vid in sorted(catalog) if vid.startswith("en_")]
    print("\nAvailable English voices:")
    for i, vid in enumerate(en_voices):
        downloaded = " (Downloaded)" if _model_path(vid).exists() else ""
        print(f"{i+1}. {vid}{downloaded}")
        
    v_choice = input("\nSelect a voice number to download: ").strip()
    if not v_choice.isdigit() or not (1 <= int(v_choice) <= len(en_voices)):
        print("Invalid choice.")
        return
        
    voice_id = en_voices[int(v_choice) - 1]
    _ensure_voice(voice_id)
    print(f"Voice {voice_id} is downloaded and ready.")


def main():
    while True:
        print("\n" + "="*34)
        print("NovelBuddy & TTS CLI")
        print("="*34)
        print("1. Download chapters")
        print("2. Combine/export existing chapter txt files")
        print("3. Download chapters, then combine/export")
        print("4. TTS: List downloaded voices")
        print("5. TTS: Download a new voice")
        print("6. TTS: Synthesize text to WAV")
        print("0. Exit")
        
        choice = input("\nChoose an option: ").strip()
        
        if choice in {"1", "2", "3"}:
            scrape_io.main()
        elif choice == "4":
            if not TTS_AVAILABLE:
                print("TTS not available. Install piper-tts.")
                continue
            voices = _list_downloaded_voices()
            if not voices:
                print("No voices downloaded yet.")
            else:
                print("\nDownloaded voices:")
                for v in voices:
                    print(f"- {v}")
        elif choice == "5":
            download_voice_cli()
        elif choice == "6":
            synthesize_text()
        elif choice == "0":
            print("Goodbye!")
            sys.exit(0)
        else:
            print("Unknown option. Please try again.")


if __name__ == "__main__":
    main()