"""Orchestrierung: Metadaten -> Schnitterkennung -> Blockplanung -> Gemini -> Ergebnis."""
import datetime
import json
import re
import time
from pathlib import Path

import config
import render_md
import shot_detection
import video_utils
from gemini_analysis import (AnalysisError, QuotaExceededError, analyze_block,
                             delete_uploaded, make_client, summarize, upload_video)


class LocalScan(object):
    """Ergebnis der (kostenlosen) lokalen Vorstufe inkl. Request-Schätzung."""

    def __init__(self, video_path, meta, shots, units, blocks, keyframe_mode, threshold):
        self.video_path = video_path
        self.meta = meta
        self.shots = shots
        self.units = units
        self.blocks = blocks
        self.keyframe_mode = keyframe_mode
        self.threshold = threshold

    def planned_requests(self, with_summary):
        return len(self.blocks) + (1 if with_summary else 0)

    @property
    def mode_label(self):
        base = ("Keyframes (%d Einheiten × 5 Frames)" % len(self.units)
                if self.keyframe_mode else
                "Video-Upload (Gemini sieht das Video selbst, 2 Frames/s)")
        return "%s, %d Request-Block/Blöcke, Modell %s" % (base, len(self.blocks), config.MODEL_MAIN)


def local_scan(video_path, threshold):
    """Schritt 1 (komplett lokal, kostenlos): Metadaten, Cuts, Blockplanung."""
    meta = video_utils.read_metadata(video_path)
    shots = shot_detection.detect_shots(video_path, meta.duration, threshold=threshold)
    units = shot_detection.build_units(shots)
    keyframe_mode = meta.duration > config.KEYFRAME_MODE_ABOVE_SECONDS
    blocks = shot_detection.plan_blocks(units, meta.duration, keyframe_mode=keyframe_mode)
    return LocalScan(video_path, meta, shots, units, blocks, keyframe_mode, threshold)


def scan_report(scan, with_summary):
    """Menschlich lesbarer Bericht über die lokale Vorstufe + Request-Schätzung."""
    lines = []
    m = scan.meta
    lines.append("Video: %.2f s, %d×%d px (%s, %s), %.2f fps"
                 % (m.duration, m.width, m.height, m.aspect_ratio, m.orientation, m.fps))
    lines.append("Erkannte Shots: %d | Analyse-Einheiten (inkl. 5-Sek-Fenster): %d"
                 % (len(scan.shots), len(scan.units)))
    lines.append("")
    for shot in scan.shots:
        n_windows = sum(1 for u in scan.units if u.shot_number == shot.number and u.window_index)
        extra = "  -> wird in %d Fenster à <=5 s geteilt" % n_windows if n_windows else ""
        lines.append("  Shot %d: %6.2f s – %6.2f s  (%.2f s)%s"
                     % (shot.number, shot.start, shot.end, shot.duration, extra))
    lines.append("")
    n_req = scan.planned_requests(with_summary)
    lines.append("Geplante Gemini-Requests: %d  (%d Analyse-Block/Blöcke%s)"
                 % (n_req, len(scan.blocks),
                    " + 1 Zusammenfassung" if with_summary else ""))
    lines.append("Modus: %s" % scan.mode_label)
    if n_req >= config.WARN_REQUEST_COUNT:
        lines.append("")
        lines.append("⚠️ WARNUNG: %d Requests sind für den kostenlosen Tarif viel. "
                     "Falls dein Tageskontingent knapp ist, brich ab oder teste "
                     "zuerst mit einem kürzeeren Video." % n_req)
    return "\n".join(lines)


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "video"


def run_analysis(scan, with_summary=False, product_hint="", api_key_override="",
                 progress=None):
    """Schritt 2: Gemini-Analyse. Gibt (markdown, json_dict, md_pfad, json_pfad) zurück."""

    def report(msg):
        if progress:
            progress(msg)

    client = make_client(api_key_override)
    uploaded = None
    converted_path = None
    video_for_frames = scan.video_path
    try:
        if not scan.keyframe_mode:
            report("Lade Video zu Gemini hoch ...")
            try:
                uploaded = upload_video(client, scan.video_path, progress=report)
            except AnalysisError as e:
                if "UPLOAD_PROCESSING_FAILED" not in str(e):
                    raise
                # Format wurde abgelehnt -> einmalig nach H.264-MP4 konvertieren
                report("Gemini konnte das Format nicht verarbeiten – konvertiere zu MP4 (ffmpeg) ...")
                config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                converted_path = video_utils.convert_to_mp4(scan.video_path, config.OUTPUT_DIR)
                uploaded = upload_video(client, converted_path, progress=report)

        block_results = []
        whole_video = (len(scan.blocks) == 1)
        n_blocks = len(scan.blocks)
        for i, block in enumerate(scan.blocks):
            report("Analysiere Block %d/%d (%d Einheiten) mit %s ..."
                   % (i + 1, n_blocks, len(block), config.MODEL_MAIN))
            is_final = (i == n_blocks - 1)
            # Der globale Kopf wird im LETZTEN Block angefordert: er bekommt die
            # Zeitstruktur aller Shots mit und sieht das echte Videoende.
            result = analyze_block(
                client, block, include_global=is_final,
                keyframe_mode=scan.keyframe_mode, uploaded=uploaded,
                video_path=video_for_frames, whole_video=whole_video,
                is_final_block=is_final, all_units=scan.units,
                video_duration=scan.meta.duration, product_hint=product_hint)
            block_results.append(result)
            if i + 1 < n_blocks:
                time.sleep(3.0)  # Requests pro Minute im Free-Tier schonen

        global_head, descriptions = render_md.merge_results(scan.blocks, block_results)

        summary = None
        if with_summary:
            report("Erstelle kurze Gesamtzusammenfassung (%s) ..." % config.MODEL_SUMMARY)
            preliminary = render_md.build_markdown(
                Path(scan.video_path).name, scan.meta, scan.shots, scan.units,
                descriptions, global_head, mode_label=scan.mode_label)
            try:
                summary = summarize(client, preliminary)
            except QuotaExceededError:
                # Die teure Analyse liegt komplett vor – nur die optionale
                # Zusammenfassung entfällt, Ergebnis wird trotzdem gespeichert.
                summary = ("(Kurzzusammenfassung entfallen: Tageslimit des "
                           "Zusammenfassungs-Modells erreicht. Die Analyse selbst "
                           "ist vollständig.)")

        video_name = Path(scan.video_path).name
        markdown = render_md.build_markdown(video_name, scan.meta, scan.shots, scan.units,
                                            descriptions, global_head, summary=summary,
                                            mode_label=scan.mode_label)
        json_dict = render_md.build_json(video_name, scan.meta, scan.shots, scan.units,
                                         descriptions, global_head, summary=summary,
                                         mode_label=scan.mode_label)

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = config.OUTPUT_DIR / ("%s_%s" % (_safe_name(Path(video_name).stem), stamp))
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "analyse.md"
        json_path = out_dir / "analyse.json"
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(json.dumps(json_dict, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        report("Fertig.")
        return markdown, json_dict, str(md_path), str(json_path)
    finally:
        delete_uploaded(client, uploaded)
        if converted_path:
            try:
                Path(converted_path).unlink()
            except OSError:
                pass
