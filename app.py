"""Gradio-Web-UI für den Video-Shot-Analyzer (lokal, Drag & Drop).

Ablauf (spart Gemini-Requests):
  1) "Shots lokal erkennen" – komplett kostenlos, zeigt Shot-Liste und wie
     viele Gemini-Requests die Analyse kosten würde.
  2) "Mit Gemini analysieren" – führt die eigentliche Analyse aus
     (normalerweise genau 1 Request pro Video).
"""
import json
import os
import shutil
import traceback

import gradio as gr

import config
import pipeline
from gemini_analysis import AnalysisError, QuotaExceededError


# Läuft die App gehostet in der Cloud (Hugging Face Spaces setzt SPACE_ID,
# Render.com setzt RENDER)? Dann: von außen erreichbar sein, Server-Key als
# "Demo-Key" behandeln und kein Browserfenster öffnen.
ON_HOSTED = bool(os.environ.get("SPACE_ID") or os.environ.get("RENDER"))


def startup_status():
    parts = []
    if config.get_api_key():
        if ON_HOSTED:
            parts.append("✅ Demo-Key aktiv (fair bleiben: für viele Analysen bitte "
                         "eigenen Gemini-Key ins Feld unten eintragen – kostenlos auf "
                         "[aistudio.google.com/apikey](https://aistudio.google.com/apikey))")
        else:
            parts.append("✅ GEMINI_API_KEY gefunden")
    elif ON_HOSTED:
        parts.append("⚠️ Kein Server-Key hinterlegt – bitte eigenen Gemini-API-Key ins "
                     "Feld unten eintragen (kostenlos auf "
                     "[aistudio.google.com/apikey](https://aistudio.google.com/apikey)).")
    else:
        parts.append("❌ **GEMINI_API_KEY fehlt** – setze ihn per `export GEMINI_API_KEY=...` "
                     "oder lege eine `.env`-Datei mit `GEMINI_API_KEY=...` in den Projektordner "
                     "und starte die App neu.")
    if shutil.which("ffmpeg"):
        parts.append("✅ ffmpeg (System)")
    else:
        try:
            config.get_ffmpeg_exe()
            parts.append("✅ ffmpeg (mitgeliefert über imageio-ffmpeg, kein Homebrew nötig)")
        except Exception:
            parts.append("⚠️ Kein ffmpeg gefunden – Format-Konvertierung als Fallback nicht verfügbar "
                         "(Analyse normaler MP4s funktioniert trotzdem).")
    parts.append("Modell: `%s` (Zusammenfassung: `%s`)" % (config.MODEL_MAIN, config.MODEL_SUMMARY))
    return " · ".join(parts)


def do_scan(video_path, threshold):
    if not video_path:
        return "Bitte zuerst ein Video hochladen (Drag & Drop).", None
    try:
        scan = pipeline.local_scan(video_path, threshold)
        report = pipeline.scan_report(scan, with_summary=False)
        return report, scan
    except Exception as e:
        traceback.print_exc()
        return "❌ Lokale Analyse fehlgeschlagen: %s" % e, None


def do_analyze(video_path, threshold, with_summary, allow_many, product_hint,
               user_api_key, scan, progress=gr.Progress()):
    empty = ("", "", "", None)
    user_api_key = (user_api_key or "").strip()
    if not video_path:
        return ("Bitte zuerst ein Video hochladen.",) + empty
    if not user_api_key and not config.get_api_key():
        if ON_HOSTED:
            return ("❌ Kein API-Key verfügbar. Bitte eigenen Gemini-API-Key ins Feld "
                    "eintragen (kostenlos auf aistudio.google.com/apikey).",) + empty
        return ("❌ Kein GEMINI_API_KEY gesetzt. Siehe Hinweis oben, dann App neu starten.",) + empty

    try:
        # Scan übernehmen oder (kostenlos) neu erstellen, falls Video/Schwelle geändert
        if scan is None or scan.video_path != video_path or scan.threshold != threshold:
            progress(0.02, desc="Erkenne Shots lokal ...")
            scan = pipeline.local_scan(video_path, threshold)

        n_req = scan.planned_requests(with_summary)
        if n_req >= config.WARN_REQUEST_COUNT and not allow_many:
            return ("⚠️ Diese Analyse würde %d Gemini-Requests kosten (Warn-Schwelle: %d). "
                    "Wenn dein Tageskontingent das hergibt, setze den Haken "
                    "\"Viele Requests trotzdem ausführen\" und starte erneut." %
                    (n_req, config.WARN_REQUEST_COUNT),) + empty

        state = {"step": 0}
        total_steps = len(scan.blocks) + 2 + (1 if with_summary else 0)

        def report(msg):
            state["step"] = min(state["step"] + 1, total_steps)
            progress(state["step"] / float(total_steps), desc=msg)

        markdown, json_dict, md_path, json_path = pipeline.run_analysis(
            scan, with_summary=with_summary, product_hint=(product_hint or ""),
            api_key_override=user_api_key, progress=report)
        status = ("✅ Fertig – %d Einheiten beschrieben, %d Gemini-Request(s) verbraucht.\n"
                  "Gespeichert unter:\n%s\n%s" % (len(scan.units), n_req, md_path, json_path))
        json_text = json.dumps(json_dict, ensure_ascii=False, indent=2)
        return status, markdown, markdown, json_text, [md_path, json_path]
    except QuotaExceededError as e:
        return ("🛑 %s" % e,) + empty
    except AnalysisError as e:
        return ("❌ %s" % e,) + empty
    except Exception as e:
        traceback.print_exc()
        return ("❌ Unerwarteter Fehler: %s" % e,) + empty


def build_ui():
    with gr.Blocks(title="Video-Shot-Analyzer") as demo:
        gr.Markdown("# 🎬 Video-Shot-Analyzer\n"
                    "Shot-für-Shot-Analyse für den Nachbau als KI-Clips (max. 5 s). "
                    "Rein visuell, extrem detailliert, kein Audio, keine Prompts.")
        gr.Markdown(startup_status())

        with gr.Row():
            with gr.Column(scale=1):
                video = gr.Video(label="Video (Drag & Drop)", sources=["upload"])
                threshold = gr.Slider(
                    10, 50, value=config.DETECT_THRESHOLD_DEFAULT, step=0.5,
                    label="Schnitt-Empfindlichkeit",
                    info="Niedriger = mehr Cuts erkannt. Standard 27 passt meistens.")
                product_hint = gr.Textbox(
                    label="Hauptprodukt (optional)",
                    placeholder="z. B. beige Cargohose – leer lassen für automatische Erkennung",
                    info="Die Analyse beschreibt pro Shot, WIE dieses Produkt in Szene gesetzt wird.")
                user_api_key = gr.Textbox(
                    label="Eigener Gemini-API-Key (optional)", type="password",
                    placeholder="AQ.… – leer lassen, um den Server-Key zu nutzen",
                    info="Kostenlos auf aistudio.google.com/apikey. Wird nur für deine "
                         "Requests genutzt, nicht gespeichert.")
                with_summary = gr.Checkbox(
                    label="Kurze Gesamtzusammenfassung (kostet 1 Extra-Request, %s)"
                          % config.MODEL_SUMMARY, value=False)
                scan_btn = gr.Button("1) Shots lokal erkennen (kostenlos)")
                scan_box = gr.Textbox(label="Shot-Übersicht & Request-Schätzung",
                                      lines=14, interactive=False)
                allow_many = gr.Checkbox(
                    label="Viele Requests trotzdem ausführen (nur nötig bei Warnung)",
                    value=False)
                analyze_btn = gr.Button("2) Mit Gemini analysieren", variant="primary")
                status = gr.Textbox(label="Status", lines=4, interactive=False)
            with gr.Column(scale=2):
                with gr.Tabs():
                    with gr.Tab("Analyse (formatiert)"):
                        md_view = gr.Markdown("*Noch keine Analyse.*")
                    with gr.Tab("Markdown (Rohtext, kopierbar)"):
                        md_raw = gr.Textbox(lines=32, show_copy_button=True,
                                            label="Markdown", interactive=False)
                    with gr.Tab("JSON (kopierbar)"):
                        json_raw = gr.Textbox(lines=32, show_copy_button=True,
                                              label="JSON", interactive=False)
                files = gr.File(label="Download (.md / .json)", file_count="multiple",
                                interactive=False)

        scan_state = gr.State(None)
        scan_btn.click(do_scan, inputs=[video, threshold],
                       outputs=[scan_box, scan_state])
        analyze_btn.click(do_analyze,
                          inputs=[video, threshold, with_summary, allow_many,
                                  product_hint, user_api_key, scan_state],
                          outputs=[status, md_view, md_raw, json_raw, files])
    return demo


if __name__ == "__main__":
    demo = build_ui()
    port = int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", "7860")))
    # Auf Hugging Face Spaces von außen erreichbar sein; lokal nur auf dem eigenen Rechner.
    # Immer nur 1 Analyse gleichzeitig, damit das API-Kontingent nicht parallel leerläuft.
    demo.queue(default_concurrency_limit=1).launch(
        server_name=("0.0.0.0" if ON_HOSTED else "127.0.0.1"), server_port=port,
        inbrowser=(not ON_HOSTED and os.environ.get("VSA_NO_BROWSER", "") == ""),
        show_error=True)
