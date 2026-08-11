# 🎬 Video-Shot-Analyzer

Lokales macOS-Tool: analysiert ein Video shot-für-shot **rein visuell** und extrem
detailliert – als Grundlage, um die Clips später in Higgsfield (max. 5 s) nachzubauen.
Erstellt **keine Prompts**, nur die Analyse (Markdown + JSON).

- Schnitterkennung lokal mit PySceneDetect (kostenlos, beliebig oft)
- Bildanalyse mit Gemini (`gemini-3.5-flash-lite`) – **1 Request pro Video**, damit das
  kostenlose Tageslimit reicht
- **Produkt-Fokus:** identifiziert das Hauptprodukt (oder du gibst es im Feld
  „Hauptprodukt" vor, z. B. „beige Cargohose") und beschreibt pro Shot in 7 Punkten,
  WIE es in Szene gesetzt wird (Sichtbarkeit, Position/Größe, Winkel/Detail,
  Kameraführung, Licht, Interaktion, Prominenz) + die Gesamtstrategie im Kopf
- Web-Oberfläche (Gradio) mit Drag & Drop, Kopier-Buttons und Download als `.md`/`.json`

## Setup (einmalig)

Voraussetzung: macOS mit Python 3.9+ (das vorinstallierte System-Python reicht).
Kein Homebrew nötig, ffmpeg wird über pip mitgeliefert. Im Terminal im Projektordner:

```bash
bash setup.sh
```

(Das Skript legt die venv an und installiert alles in der richtigen Reihenfolge –
eine pip-Warnung zu `websockets` während der Installation ist erwartet und harmlos.)

### API-Key setzen

Key aus [Google AI Studio](https://aistudio.google.com/apikey) holen (neue Keys beginnen
mit `AQ.` – das ist korrekt so). Dann **eine** der beiden Varianten:

**Variante A (empfohlen, dauerhaft):** Datei `.env` im Projektordner anlegen mit dem Inhalt:

```
GEMINI_API_KEY=DEIN_KEY_HIER
```

**Variante B (pro Terminal-Sitzung):**

```bash
export GEMINI_API_KEY="DEIN_KEY_HIER"
```

## Starten

Im Projektordner:

```bash
./venv/bin/python app.py
```

Der Browser öffnet sich automatisch (sonst: <http://127.0.0.1:7860>).

## Bedienung

1. Video per Drag & Drop hochladen (Hochkant-TikToks/Reels, auch abgefilmt).
2. **„1) Shots lokal erkennen"** – kostenlos. Zeigt alle erkannten Shots, die
   5-Sekunden-Fenster und **wie viele Gemini-Requests** die Analyse kosten würde
   (normal: 1). Bei zu vielen/zu wenigen Cuts: Schnitt-Empfindlichkeit anpassen
   und erneut klicken.
3. **„2) Mit Gemini analysieren"** – führt die Analyse aus. Ergebnis erscheint als
   formatiertes Markdown, als kopierbarer Rohtext und als JSON; beides liegt auch
   als Datei im Ordner `outputs/` und ist über die Download-Box ladbar.

## Wie das Tool Requests spart

- Das ganze Video geht in **einem** Gemini-Request zur Analyse (die lokale
  Schnitterkennung liefert die Zeitstempel mit, Gemini beschreibt jeden Shot einzeln
  in voller Tiefe).
- Nur Videos über ~60 s oder mit sehr vielen Shots werden in wenige große Blöcke
  geteilt – nie ein Request pro Shot. Die Schätzung siehst du vor dem Start.
- Videos über 4 Minuten laufen automatisch im Keyframe-Modus (5 Frames pro Einheit
  statt Video-Upload).
- Bei `429 / RESOURCE_EXHAUSTED` (Tageslimit erreicht) bricht das Tool **sofort**
  sauber ab und sagt dir das klar – nichts läuft ewig weiter.

## Projektstruktur

| Datei | Zweck |
|---|---|
| `app.py` | Gradio-Oberfläche, Start der App |
| `pipeline.py` | Ablauf: Metadaten → Cuts → Blockplanung → Gemini → Dateien |
| `shot_detection.py` | PySceneDetect-Schnitterkennung, 5-Sek-Fenster, Request-Blöcke |
| `gemini_analysis.py` | Gemini-Client, Prompt, Antwort-Schema, 429-Abbruch |
| `video_utils.py` | Metadaten, Keyframes, ffmpeg-Konvertierung (Fallback) |
| `render_md.py` | Markdown-/JSON-Ausgabe |
| `config.py` | Modelle, Schwellen, Limits |

## Troubleshooting

- **„GEMINI_API_KEY fehlt"** → `.env` anlegen oder `export`, dann App neu starten.
- **Antwort am Token-Limit abgeschnitten** → in `config.py` `BLOCK_MAX_UNITS`
  verkleinern (z. B. auf 15).
- **Video-Format wird abgelehnt** → das Tool konvertiert automatisch einmalig mit
  ffmpeg nach MP4; klappt auch das nicht, Video vorher manuell als MP4 exportieren.
