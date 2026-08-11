"""Zentrale Konfiguration für den Video-Shot-Analyzer."""
import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"

# --- Modelle ---
# Hauptmodell: Flash-Lite-Klasse = höchstes kostenloses Tageslimit.
# Hinweis: gemini-2.5-flash-lite ist für neue API-Keys gesperrt (404),
# gemini-3.5-flash-lite ist der aktuelle Nachfolger.
MODEL_MAIN = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Nur für die optionale kurze Gesamtzusammenfassung.
MODEL_SUMMARY = os.environ.get("GEMINI_MODEL_SUMMARY", "gemini-3.6-flash")

# --- Sparsamkeit / Request-Planung ---
# Ein Video wird nur dann in mehrere Requests geteilt, wenn es diese
# Grenzen überschreitet. Ziel: 1 Request pro Video.
BLOCK_MAX_SECONDS = 60.0      # max. Videolänge pro Request (Block)
BLOCK_MAX_UNITS = 25          # max. Analyse-Einheiten (Shots/Fenster) pro Request,
                              # damit die Antwort nicht am Output-Token-Limit abreißt
WARN_REQUEST_COUNT = 10       # ab so vielen geplanten Requests deutlich warnen

# Ab dieser Videolänge werden statt des Videos Keyframes geschickt
# (Keyframes brauchen deutlich weniger Tokens pro Sekunde Video).
KEYFRAME_MODE_ABOVE_SECONDS = 240.0

# Keyframe-Modus: kleinere Blöcke und kompaktere JPEGs, damit ein Request
# sicher unter dem 20-MB-Limit für Inline-Daten bleibt
# (max. 12 Einheiten × 5 Frames × ~50 KB ≈ 3 MB pro Request).
KEYFRAME_BLOCK_MAX_UNITS = 12
KEYFRAME_MAX_SIDE = 768
KEYFRAME_JPEG_QUALITY = 80

# Bei Mehrblock-Videos: Video-Abschnitt um diesen Wert über das Blockende
# hinaus zeigen, damit Gemini den Übergang an der Blockgrenze sehen kann.
BLOCK_OVERLAP_SECONDS = 0.75

# --- Schnitterkennung ---
DETECT_THRESHOLD_DEFAULT = 27.0   # ContentDetector-Standard, guter Allrounder
DETECT_MIN_SCENE_LEN_FRAMES = 12  # ~0,4 s bei 30 fps: verhindert Mikro-Schnipsel
SUBSHOT_WINDOW_SECONDS = 5.0      # Shots > 5 s werden in gleich große Fenster <= 5 s geteilt

# --- Gemini ---
MAX_OUTPUT_TOKENS = 65535
# Großzügig, weil bei Gemini-3-Modellen auch Thinking-Tokens hier hineinzählen
SUMMARY_MAX_TOKENS = 8192
UPLOAD_POLL_SECONDS = 2.0
UPLOAD_TIMEOUT_SECONDS = 300.0


def get_api_key():
    """GEMINI_API_KEY aus Umgebung oder .env-Datei im Projektordner.

    Keine Format-Validierung: neue Google-Keys beginnen mit "AQ.",
    ältere mit "AIza" – beide sind gültig.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY"):
                _, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return ""


def get_ffmpeg_exe():
    """Pfad zu ffmpeg: System-Installation, sonst das per pip
    mitgelieferte Binary aus imageio-ffmpeg (kein Homebrew nötig)."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()
