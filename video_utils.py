"""Video-Metadaten, Keyframe-Extraktion und ffmpeg-Konvertierung."""
import math
import subprocess
from pathlib import Path
from typing import List, Optional

import cv2

import config


class VideoMeta(object):
    def __init__(self, path, width, height, fps, frame_count, duration):
        self.path = path
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = frame_count
        self.duration = duration

    @property
    def aspect_ratio(self):
        if self.width <= 0 or self.height <= 0:
            return "unbekannt"
        g = math.gcd(self.width, self.height)
        w, h = self.width // g, self.height // g
        # Gängige Verhältnisse lesbar machen (z. B. 1080x1920 -> 9:16)
        if w > 50 or h > 50:
            ratio = self.width / self.height
            for name, value in [("9:16", 9 / 16), ("16:9", 16 / 9), ("4:5", 4 / 5),
                                ("3:4", 3 / 4), ("4:3", 4 / 3), ("1:1", 1.0), ("21:9", 21 / 9)]:
                if abs(ratio - value) < 0.03:
                    return name + " (ca.)"
            return "%.3f:1" % ratio
        return "%d:%d" % (w, h)

    @property
    def orientation(self):
        if self.height > self.width:
            return "Hochformat"
        if self.width > self.height:
            return "Querformat"
        return "quadratisch"


def read_metadata(path):
    """Metadaten per OpenCV lesen (kein ffprobe nötig)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Video konnte nicht geöffnet werden: %s" % path)
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0.0
        if duration <= 0:
            raise ValueError("Videolänge konnte nicht bestimmt werden (0 Sekunden?).")
        return VideoMeta(str(path), width, height, fps, frame_count, duration)
    finally:
        cap.release()


def extract_keyframes(path, timestamps, max_side=1024, jpeg_quality=85):
    """Frames an den gegebenen Zeitpunkten (Sekunden) als JPEG-Bytes extrahieren.

    Gibt eine Liste gleicher Länge zurück; nicht lesbare Frames sind None.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Video konnte nicht geöffnet werden: %s" % path)
    results = []  # type: List[Optional[bytes]]
    try:
        for ts in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ts) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                results.append(None)
                continue
            h, w = frame.shape[:2]
            scale = max_side / float(max(h, w))
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
            results.append(buf.tobytes() if ok else None)
        return results
    finally:
        cap.release()


def convert_to_mp4(path, target_dir):
    """Video mit ffmpeg nach H.264-MP4 konvertieren (Fallback, falls Gemini
    das Original-Format ablehnt). Gibt den Pfad der MP4-Datei zurück."""
    src = Path(path)
    out = Path(target_dir) / (src.stem + "_konvertiert.mp4")
    cmd = [
        config.get_ffmpeg_exe(), "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("ffmpeg-Konvertierung fehlgeschlagen:\n" + proc.stderr[-2000:])
    return str(out)
