"""Gemini-Anbindung: 1 Request pro Video (bzw. pro großem Block).

Sparsamkeit ist Pflicht (kostenloser Tarif): Die lokale Schnitterkennung
liefert die Zeitstempel, Gemini beschreibt ALLE Shots in EINER Antwort.
Bei 429/RESOURCE_EXHAUSTED wird sofort sauber abgebrochen.
"""
import json
import time
from typing import List, Optional

from google import genai
from google.genai import errors, types

import config
from shot_detection import Unit


class QuotaExceededError(Exception):
    """Kostenloses Tageslimit erreicht -> sofort abbrechen, nicht weiterlaufen."""


class AnalysisError(Exception):
    """Analyse fehlgeschlagen (mit verständlicher Meldung für die UI)."""


VIDEO_SAMPLING_FPS = 2.0  # 2 Bilder/Sek. statt Standard 1: bessere Abdeckung schneller Schnitte


# ---------------------------------------------------------------------------
# Antwort-Schema (strukturiertes JSON)
# ---------------------------------------------------------------------------

_PRODUKT_PROPS = {
    "sichtbar": {"type": "string", "description": "ja / nein / teilweise, plus 1 Satz wo/wie."},
    "position_groesse": {"type": "string"},
    "winkel_detail": {"type": "string"},
    "kamerafuehrung": {"type": "string"},
    "licht_wirkung": {"type": "string"},
    "interaktion": {"type": "string"},
    "prominenz": {"type": "string"},
}

_UNIT_PROPS = {
    "uid": {"type": "string", "description": "Kennung der Einheit, exakt wie im Prompt angegeben (z. B. '3' oder '4.2')."},
    "bild_kamera": {"type": "string"},
    "subjekte_objekte": {"type": "string"},
    "produkt_inszenierung": {
        "type": "object",
        "properties": _PRODUKT_PROPS,
        "required": list(_PRODUKT_PROPS.keys()),
        "propertyOrdering": list(_PRODUKT_PROPS.keys()),
    },
    "aktion_bewegung": {"type": "string"},
    "licht_farbe": {"type": "string"},
    "umgebung": {"type": "string"},
    "effekte_uebergang": {"type": "string"},
    "onscreen_text": {"type": "string"},
    "plattform_ui": {"type": "string"},
}

_UNIT_REQUIRED = list(_UNIT_PROPS.keys())

_GLOBAL_PROPS = {
    "hauptprodukt": {"type": "string"},
    "produkt_strategie": {"type": "string"},
    "gesamtstil_vibe": {"type": "string"},
    "gesamttempo_schnittrhythmus": {"type": "string"},
    "wiederkehrende_elemente": {"type": "string"},
    "roter_faden": {"type": "string"},
}


def build_response_schema(include_global):
    props = {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _UNIT_PROPS,
                "required": _UNIT_REQUIRED,
                "propertyOrdering": _UNIT_REQUIRED,
            },
        }
    }
    required = ["shots"]
    if include_global:
        props["global_kopf"] = {
            "type": "object",
            "properties": _GLOBAL_PROPS,
            "required": list(_GLOBAL_PROPS.keys()),
        }
        required.append("global_kopf")
    return {"type": "object", "properties": props, "required": required}


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _fmt_ts(seconds):
    return "%.2fs" % seconds


def _unit_table(units):
    lines = []
    for u in units:
        window_note = ""
        if u.window_index is not None:
            window_note = " (Teilfenster %d von %d desselben ungeschnittenen Shots %d)" % (
                u.window_index, u.window_total, u.shot_number)
        lines.append("- uid=%s | %s | %s bis %s | Dauer %.2f s%s" % (
            u.uid, u.label, _fmt_ts(u.start), _fmt_ts(u.end), u.duration, window_note))
    return "\n".join(lines)


def build_prompt(units, include_global, keyframe_mode, is_final_block=True, all_units=None,
                 product_hint=""):
    quelle = ("Du bekommst pro Einheit mehrere Keyframes (Anfang / 25 % / 50 % / 75 % / Ende), "
              "jeweils mit Zeitstempel beschriftet."
              if keyframe_mode else
              "Du bekommst das Video selbst. Die Zeitstempel unten sind absolute Videozeiten.")
    if not is_final_block and not keyframe_mode:
        quelle += (" Der gezeigte Videoabschnitt reicht etwas über die letzte Einheit hinaus – "
                   "diese Überlappung dient NUR dazu, den Übergang zum nächsten Shot zu erkennen; "
                   "beschreibe sie nicht als eigene Einheit.")

    if product_hint.strip():
        produkt_satz = ('Das Hauptprodukt ist vom Nutzer vorgegeben: "%s". Konzentriere dich '
                        'exakt darauf.' % product_hint.strip())
    else:
        produkt_satz = ("Identifiziere zuerst selbst, welches Produkt das Hauptprodukt ist "
                        "(das Objekt, um das sich das Video erkennbar dreht, z. B. ein Schuh "
                        "oder eine Hose).")

    if is_final_block:
        letzte_regel = 'Bei der letzten Einheit: "Ende des Videos".'
    else:
        letzte_regel = ("Bei der letzten Einheit dieses Analyse-Abschnitts: Das Video geht danach "
                        "weiter. Beschreibe die Übergangsart, falls der Anschluss sichtbar ist; "
                        'sonst schreibe "Übergang außerhalb dieses Analyse-Abschnitts". '
                        'Schreibe hier NIEMALS "Ende des Videos".')

    global_block = ""
    if include_global:
        uebersicht = ""
        if all_units and len(all_units) > len(units):
            zeilen = "\n".join("  - %s: %s bis %s (%.2f s)" % (u.label, _fmt_ts(u.start),
                                                               _fmt_ts(u.end), u.duration)
                               for u in all_units)
            uebersicht = ("\nZur Einordnung die Zeitstruktur ALLER Shots des gesamten Videos "
                          "(NICHT einzeln beschreiben, nur für Tempo/Rhythmus nutzen):\n"
                          + zeilen + "\n")
        global_block = """
Fülle zusätzlich das Feld "global_kopf" mit:
- hauptprodukt: das Hauptprodukt des Videos exakt beschrieben (Produktart, Form, Farben präzise, Material, Oberfläche, erkennbare Details/Logos, Zustand). Falls wirklich kein Produkt erkennbar ist: schreibe das explizit.
- produkt_strategie: WIE wird das Produkt über das ganze Video in Szene gesetzt? Die Gesamtstrategie der Produktpräsentation (z. B. detailgetrieben über Nahaufnahmen, Lifestyle am Körper, dramatischer Hero-Reveal, schnelle Schnitt-Montage, Vorher/Nachher) + über welchen ungefähren Anteil der Laufzeit das Produkt sichtbar ist (aus den sichtbar-Feldern und Einheiten-Dauern ableitbar, z. B. "sichtbar in ~80 %% der Laufzeit").
- gesamtstil_vibe: Gesamtstil/Vibe des Videos (Ästhetik, Genre-Anmutung, Stimmung – rein visuell begründet).
- gesamttempo_schnittrhythmus: Gesamttempo und Schnittrhythmus (schnell/langsam, regelmäßig/aksentuiert, aus den Shot-Dauern ableitbar).
- wiederkehrende_elemente: konkret benannte wiederkehrende Personen, Objekte, Farben, Orte, Bildmotive.
- roter_faden: der sichtbare inhaltliche/visuelle Bogen über alle Shots (nur Beobachtbares, keine Spekulation).
%s""" % uebersicht

    return """Du bist ein forensisch genauer visueller Video-Analyst. %s

AUFGABE: Beschreibe JEDE unten gelistete Einheit (Shot bzw. 5-Sekunden-Teilfenster) einzeln und in EXTREMER Detailtiefe. Oberstes Ziel: Jemand, der das Video NIE gesehen hat, muss es allein aus deinem Text zu ~100 %% rekonstruieren können. Lieber zu ausführlich als zu knapp. Es wird NICHTS zusammengefasst, NICHTS weggelassen, NICHTS interpretiert – nur beschrieben, was sichtbar ist.

PRODUKT-FOKUS (Anker der gesamten Analyse): Das Video dreht sich um ein Hauptprodukt. %s
Behandle dieses Produkt in JEDER Einheit als Anker: Es geht nicht nur darum, WAS zu sehen ist, sondern präzise darum, WIE das Produkt gezeigt und in Szene gesetzt wird (damit die Inszenierung mit einem anderen Produkt nachgebaut werden kann). Auch wenn das Produkt in einer Einheit nur Nebenrolle ist oder fehlt, halte das explizit fest.

REGELN:
1. Rein visuell. Kein Audio, keine Vermutungen über Ton, Absicht oder Kontext außerhalb des Bildes.
2. NIEMALS vage. Nicht "eine Person", sondern exakt: geschätztes Alter, Statur, Hautton, Frisur/Haarfarbe/Haarlänge, Gesichtsausdruck, Pose; Kleidung Stück für Stück mit Schnitt, Farbe (so präzise wie möglich, z. B. "mattes Salbeigrün", "verwaschenes Hellblau"), Material und Zustand; Accessoires einzeln. Nicht "ein Objekt", sondern exakt: Form, Material, Farbe, Oberfläche/Finish, Zustand, Größe relativ zum Bild, Position.
3. Farben immer so genau wie möglich benennen (Nuance, Sättigung, hell/dunkel), auch bei Licht, Hintergrund und Grading.
4. Plattform-UI (TikTok/Instagram-Buttons, Nutzername, Likes, Untertitel-Leiste der Plattform, Fortschrittsbalken) gehört NICHT zur Bildbeschreibung: Liste sie nur knapp im Feld "plattform_ui" und ignoriere sie sonst vollständig. Eingebrannter On-Screen-Text des Videos selbst (Captions, Titel, Sticker-Texte) gehört dagegen mit exaktem Wortlaut, Position, Schriftstil und Farbe in "onscreen_text".
5. Zeitangaben innerhalb einer Einheit in Sekundenbruchteilen relativ zur absoluten Videozeit (z. B. "bei 3,4 s beginnt ...").
6. Wenn etwas wirklich nicht erkennbar ist (Unschärfe, Anschnitt), schreibe präzise, WAS nicht erkennbar ist und warum – niemals raten, niemals leer lassen.
7. Antworte auf Deutsch.

FELDER PRO EINHEIT (alle immer ausfüllen, jeweils mehrere Sätze granularer Fließtext):
- bild_kamera: Framing (Close-up/Medium/Wide, Winkel, Hoch-/Untersicht/Augenhöhe, Distanz zum Subjekt); Kamerabewegung exakt (statisch, Push-in/-out, Orbit, Handheld-Wackeln, Whip-Pan, Dolly, Zoom, Speed-Ramp – mit Richtung und Geschwindigkeit); Objektiv-Eindruck (Weitwinkel/Normal/Tele, Verzerrung); Schärfentiefe und Fokusverlagerungen; Komposition nach Drittelregel: was sitzt wo im Bild (oben/unten, links/mittig/rechts, Vorder-/Mittel-/Hintergrund).
- subjekte_objekte: alle Personen exakt nach Regel 2; alle relevanten Objekte/Produkte exakt; Anzahl und räumliche Anordnung im Bild.
- produkt_inszenierung (Objekt mit 7 Unterfeldern, IMMER alle ausfüllen – wenn das Produkt nicht im Bild ist: sichtbar="nein", prominenz="nicht sichtbar" und in den übrigen Feldern kurz, was stattdessen den Fokus trägt):
  * sichtbar: ja / nein / teilweise (teilweise = angeschnitten, unscharf, verdeckt) + 1 Satz wo im Bild.
  * position_groesse: Position (Vordergrund/Mitte/Hintergrund; links/mittig/rechts; oben/unten) und wie viel Bildfläche das Produkt ungefähr einnimmt (z. B. "füllt ~60 %% der Bildhöhe").
  * winkel_detail: aus welchem Winkel das Produkt gezeigt wird (frontal, Profil, 3/4, von oben/unten, Rückseite) und welcher Teil / welches Detail betont wird (z. B. Sohle, Naht, Bund, Logo, Textur).
  * kamerafuehrung: wie die Kamera das Produkt vorführt (push-in aufs Produkt, Orbit, Detail-Pan, Tracking beim Gehen, Rack-Fokus aufs Produkt, statisch präsentiert) mit Richtung/Tempo.
  * licht_wirkung: wie das Licht das Produkt hervorhebt (Glanzlichter, Kantenlicht, Schattenmodellierung, Texturbetonung, Spot vs. flächig).
  * interaktion: getragen / in der Hand / freistehend; jede Interaktion (Hände greifen, Zuppeln am Stoff, Laufen, Drehung, Wurf) mit Timing.
  * prominenz: Held des Shots / Nebenrolle / nur angedeutet / nicht sichtbar – mit kurzer Begründung.
- aktion_bewegung: was konkret passiert, mit Timing in Sekundenbruchteilen; Bewegung von Subjekten und Objekten (Tempo, Richtung, Bewegungsqualität).
- licht_farbe: Lichtrichtung(en), hart/weich, Farbtemperatur, Tageszeit-Look, Schattenwurf, Reflexe/Glanzlichter; Farbpalette und Grading (dominante Farben exakt benannt, Kontrast, Sättigung, Farbstich).
- umgebung: Set/Hintergrund/Location im Detail, Texturen, Materialien, Raumtiefe, kleine Detail-Elemente.
- effekte_uebergang: sichtbare VFX/Effekte, Partikel, Motion-Blur, Overlays, Geschwindigkeitseffekte; dann der Übergang zum NÄCHSTEN Shot (Hard Cut, Match Cut, Whip-Cut, Morph, Überblendung, o. Ä.) mit Timing. %s Bei Teilfenstern desselben Shots: "kein Schnitt, Shot läuft weiter".
- onscreen_text: eingebrannter Video-Text wörtlich mit Position/Stil/Farbe; sonst "keiner".
- plattform_ui: sichtbare Plattform-UI-Elemente knapp benannt; sonst "keine".

Gib für die uid exakt den unten angegebenen Wert zurück.
%s
ZU BESCHREIBENDE EINHEITEN:
%s""" % (quelle, produkt_satz, letzte_regel, global_block, _unit_table(units))


# ---------------------------------------------------------------------------
# Client / Requests
# ---------------------------------------------------------------------------

def make_client():
    api_key = config.get_api_key()
    if not api_key:
        raise AnalysisError(
            "Kein API-Key gefunden. Setze GEMINI_API_KEY als Umgebungsvariable "
            "oder lege eine .env-Datei mit GEMINI_API_KEY=... in den Projektordner.")
    return genai.Client(api_key=api_key)


def _raise_if_quota(e):
    text = str(e)
    code = getattr(e, "code", None)
    if code == 429 or "RESOURCE_EXHAUSTED" in text or "429" in text.split(" ")[0]:
        raise QuotaExceededError(
            "Kostenloses Tageslimit erreicht (429 RESOURCE_EXHAUSTED). "
            "Analyse abgebrochen. Versuche es morgen erneut oder nutze ein "
            "anderes Modell / einen anderen API-Key.\n\nOriginalmeldung: %s" % text[:500])


def upload_video(client, path, progress=None):
    """Video in die Gemini Files API laden und auf Verarbeitung warten."""
    try:
        uploaded = client.files.upload(file=str(path))
    except errors.APIError as e:
        _raise_if_quota(e)
        raise AnalysisError("Video-Upload fehlgeschlagen: %s" % str(e)[:800])

    waited = 0.0
    while uploaded.state and str(uploaded.state) in ("FileState.PROCESSING", "PROCESSING"):
        if waited >= config.UPLOAD_TIMEOUT_SECONDS:
            raise AnalysisError("Timeout: Gemini hat das Video nach %d s noch nicht verarbeitet."
                                % int(waited))
        if progress:
            progress("Gemini verarbeitet das hochgeladene Video ... (%d s)" % int(waited))
        time.sleep(config.UPLOAD_POLL_SECONDS)
        waited += config.UPLOAD_POLL_SECONDS
        uploaded = client.files.get(name=uploaded.name)

    if uploaded.state and str(uploaded.state) in ("FileState.FAILED", "FAILED"):
        raise AnalysisError("UPLOAD_PROCESSING_FAILED")
    return uploaded


def delete_uploaded(client, uploaded):
    try:
        if uploaded is not None:
            client.files.delete(name=uploaded.name)
    except Exception:
        pass  # Aufräumen ist Best-Effort; Files verfallen ohnehin nach 48 h


def _video_part(uploaded, block_units, whole_video, video_duration, is_final_block):
    file_data = types.FileData(file_uri=uploaded.uri, mime_type=uploaded.mime_type)
    if whole_video:
        meta = types.VideoMetadata(fps=VIDEO_SAMPLING_FPS)
    else:
        # Abschnitt etwas über das Blockende hinaus zeigen, damit der Übergang
        # an der Blockgrenze sichtbar ist (nur bei nicht-letzten Blöcken nötig)
        end = block_units[-1].end
        if not is_final_block:
            end = min(video_duration, end + config.BLOCK_OVERLAP_SECONDS)
        meta = types.VideoMetadata(
            start_offset="%.2fs" % block_units[0].start,
            end_offset="%.2fs" % end,
            fps=VIDEO_SAMPLING_FPS,
        )
    return types.Part(file_data=file_data, video_metadata=meta)


def _keyframe_parts(video_path, block_units):
    from video_utils import extract_keyframes
    parts = []
    for u in block_units:
        d = u.duration
        stamps = [u.start + d * f for f in (0.02, 0.25, 0.5, 0.75, 0.97)]
        frames = extract_keyframes(video_path, stamps,
                                   max_side=config.KEYFRAME_MAX_SIDE,
                                   jpeg_quality=config.KEYFRAME_JPEG_QUALITY)
        labels = ["Anfang", "25 %", "50 %", "75 %", "Ende"]
        for label, ts, data in zip(labels, stamps, frames):
            if data is None:
                continue
            parts.append(types.Part(text="[%s | Keyframe %s | t=%s]" % (u.label, label, _fmt_ts(ts))))
            parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    if not parts:
        raise AnalysisError("Keine Keyframes extrahierbar – Video defekt?")
    return parts


def analyze_block(client, block_units, include_global, keyframe_mode,
                  uploaded=None, video_path=None, whole_video=False,
                  is_final_block=True, all_units=None, video_duration=0.0,
                  product_hint=""):
    """Ein Block = EIN Gemini-Request für alle enthaltenen Einheiten."""
    prompt = build_prompt(block_units, include_global, keyframe_mode,
                          is_final_block=is_final_block, all_units=all_units,
                          product_hint=product_hint)
    if keyframe_mode:
        parts = _keyframe_parts(video_path, block_units)
    else:
        parts = [_video_part(uploaded, block_units, whole_video,
                             video_duration, is_final_block)]
    contents = parts + [types.Part(text=prompt)]

    config_obj = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=build_response_schema(include_global),
        max_output_tokens=config.MAX_OUTPUT_TOKENS,
        temperature=0.3,
    )
    try:
        response = client.models.generate_content(
            model=config.MODEL_MAIN, contents=contents, config=config_obj)
    except errors.APIError as e:
        _raise_if_quota(e)
        raise AnalysisError("Gemini-Request fehlgeschlagen: %s" % str(e)[:800])

    raw = response.text or ""
    finish = ""
    if response.candidates:
        finish = str(response.candidates[0].finish_reason or "")
    if "MAX_TOKENS" in finish:
        raise AnalysisError(
            "Die Antwort wurde am Token-Limit abgeschnitten (%d Einheiten in einem Block). "
            "Bitte BLOCK_MAX_UNITS in config.py verkleinern und erneut versuchen."
            % len(block_units))
    try:
        data = json.loads(raw)
    except ValueError:
        raise AnalysisError(
            "Gemini-Antwort war kein gültiges JSON (Finish-Reason: %s). Rohtext-Anfang:\n%s"
            % (finish or "unbekannt", raw[:1500]))
    if not isinstance(data.get("shots"), list) or not data["shots"]:
        raise AnalysisError("Gemini-Antwort enthält keine Shot-Beschreibungen. Rohtext-Anfang:\n%s"
                            % raw[:1500])
    return data


def summarize(client, markdown_text):
    """Optionale kurze Gesamtzusammenfassung (1 zusätzlicher Request, gemini-2.5-flash)."""
    prompt = ("Fasse die folgende Shot-für-Shot-Videoanalyse in 4-6 Sätzen auf Deutsch "
              "zusammen (Inhalt, Stil, Tempo und wie das Hauptprodukt in Szene gesetzt "
              "wird). Nur Fließtext.\n\n" + markdown_text[:60000])
    gen_config = {"max_output_tokens": config.SUMMARY_MAX_TOKENS}
    if config.MODEL_SUMMARY.startswith("gemini-2.5"):
        # Nur die 2.5-Familie kennt thinking_budget; ohne diese Bremse zählen
        # Thinking-Tokens gegen max_output_tokens und können die Antwort aufzehren.
        gen_config["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    try:
        response = client.models.generate_content(
            model=config.MODEL_SUMMARY, contents=prompt,
            config=types.GenerateContentConfig(**gen_config))
        return (response.text or "").strip()
    except errors.APIError as e:
        _raise_if_quota(e)
        return "(Zusammenfassung fehlgeschlagen: %s)" % str(e)[:300]
