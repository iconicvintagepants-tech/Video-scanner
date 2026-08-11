"""Zusammenbau des Ergebnisses: Gemini-JSON + lokale Daten -> Markdown und JSON."""
from typing import Dict, List, Optional


def _fmt_clock(seconds):
    m = int(seconds // 60)
    s = seconds - m * 60
    return "%d:%05.2f" % (m, s)


def merge_results(unit_blocks, block_results):
    """Antworten aller Blöcke den lokalen Einheiten zuordnen.

    Zuordnung primär per uid. Der Positions-Fallback gilt nur INNERHALB eines
    Blocks und nur, wenn der Block exakt so viele Einträge geliefert hat wie
    er Einheiten hatte – sonst bleibt die Einheit leer und wird im Markdown
    als fehlend markiert (besser als eine stillschweigend falsche Zuordnung).
    Gibt (global_kopf, beschreibungen_pro_unit) zurück.
    """
    global_head = {}
    merged = []
    for block_units, result in zip(unit_blocks, block_results):
        result = result or {}
        if isinstance(result.get("global_kopf"), dict) and result["global_kopf"]:
            global_head = result["global_kopf"]
        entries = [e for e in result.get("shots", []) if isinstance(e, dict)]
        by_uid = {}
        for entry in entries:
            uid = str(entry.get("uid", "")).strip()
            if uid:
                by_uid[uid] = entry
        counts_match = len(entries) == len(block_units)
        for j, unit in enumerate(block_units):
            entry = by_uid.get(unit.uid)
            if entry is None and counts_match:
                entry = entries[j]  # Fallback: Position innerhalb des Blocks
            merged.append(entry or {})
    return global_head, merged


_FIELDS = [
    ("bild_kamera", "Bild & Kamera"),
    ("subjekte_objekte", "Subjekt(e) & Objekte"),
    ("produkt_inszenierung", "Produkt-Inszenierung"),
    ("aktion_bewegung", "Aktion & Bewegung"),
    ("licht_farbe", "Licht & Farbe"),
    ("umgebung", "Umgebung"),
    ("effekte_uebergang", "Effekte & Übergang"),
    ("onscreen_text", "On-Screen-Text (Video)"),
    ("plattform_ui", "Plattform-UI (ignoriert)"),
]

_PRODUKT_SUBFELDER = [
    ("sichtbar", "Sichtbar"),
    ("position_groesse", "Position & Größe im Bild"),
    ("winkel_detail", "Winkel & betontes Detail"),
    ("kamerafuehrung", "Kameraführung aufs Produkt"),
    ("licht_wirkung", "Licht auf dem Produkt"),
    ("interaktion", "Getragen/gehalten & Interaktion"),
    ("prominenz", "Prominenz im Shot"),
]


def _produkt_dict(desc):
    """Produkt-Block einer Einheit als Dict mit allen Unterfeldern (ggf. leer)."""
    raw = (desc or {}).get("produkt_inszenierung")
    raw = raw if isinstance(raw, dict) else {}
    return {key: str(raw.get(key, "")).strip() for key, _ in _PRODUKT_SUBFELDER}


def build_markdown(video_name, meta, shots, units, descriptions, global_head,
                   summary=None, mode_label=""):
    lines = []
    lines.append("# Shot-für-Shot-Analyse: %s" % video_name)
    lines.append("")
    lines.append("## Globaler Kopf")
    lines.append("")
    lines.append("- **Gesamtdauer:** %.2f s (%s)" % (meta.duration, _fmt_clock(meta.duration)))
    lines.append("- **Anzahl Shots:** %d (davon %d Analyse-Einheiten inkl. 5-Sek-Fenster)"
                 % (len(shots), len(units)))
    lines.append("- **Auflösung:** %d×%d px, %s, %s" % (meta.width, meta.height,
                                                        meta.aspect_ratio, meta.orientation))
    lines.append("- **FPS:** %.2f" % meta.fps)
    if mode_label:
        lines.append("- **Analyse-Modus:** %s" % mode_label)
    for key, label in [("hauptprodukt", "Hauptprodukt"),
                       ("produkt_strategie", "Wie wird das Produkt in Szene gesetzt?"),
                       ("gesamtstil_vibe", "Gesamtstil/Vibe"),
                       ("gesamttempo_schnittrhythmus", "Gesamttempo/Schnittrhythmus"),
                       ("wiederkehrende_elemente", "Wiederkehrende Elemente"),
                       ("roter_faden", "Roter Faden")]:
        value = str((global_head or {}).get(key, "")).strip()
        sep = "" if label.endswith("?") else ":"
        lines.append("- **%s%s** %s" % (label, sep, value or "_nicht geliefert_"))
    if summary:
        lines.append("")
        lines.append("## Kurzzusammenfassung")
        lines.append("")
        lines.append(summary)
    lines.append("")
    lines.append("---")

    for unit, desc in zip(units, descriptions):
        lines.append("")
        lines.append("## %s" % unit.label)
        lines.append("")
        lines.append("**Zeit:** %s – %s (%s – %s) | **Dauer:** %.2f s" % (
            _fmt_clock(unit.start), _fmt_clock(unit.end),
            "%.2fs" % unit.start, "%.2fs" % unit.end, unit.duration))
        lines.append("")
        if not desc:
            lines.append("_⚠️ Für diese Einheit kam keine Beschreibung von Gemini zurück._")
            continue
        for key, label in _FIELDS:
            lines.append("**[%s]**" % label)
            if key == "produkt_inszenierung":
                produkt = _produkt_dict(desc)
                if any(produkt.values()):
                    for sub_key, sub_label in _PRODUKT_SUBFELDER:
                        lines.append("- **%s:** %s" % (sub_label,
                                                       produkt[sub_key] or "_nicht geliefert_"))
                else:
                    lines.append("_nicht geliefert_")
            else:
                value = str(desc.get(key, "")).strip()
                lines.append(value or "_nicht geliefert_")
            lines.append("")
    return "\n".join(lines)


def build_json(video_name, meta, shots, units, descriptions, global_head,
               summary=None, mode_label=""):
    unit_objs = []
    for unit, desc in zip(units, descriptions):
        obj = {
            "uid": unit.uid,
            "label": unit.label,
            "shot_nr": unit.shot_number,
            "teilfenster": ({"index": unit.window_index, "gesamt": unit.window_total}
                            if unit.window_index is not None else None),
            "start_s": round(unit.start, 3),
            "ende_s": round(unit.end, 3),
            "dauer_s": round(unit.duration, 3),
        }
        for key, _ in _FIELDS:
            if key == "produkt_inszenierung":
                obj[key] = _produkt_dict(desc)
            else:
                obj[key] = str((desc or {}).get(key, "")).strip()
        unit_objs.append(obj)
    return {
        "video": video_name,
        "global_kopf": {
            "gesamtdauer_s": round(meta.duration, 3),
            "anzahl_shots": len(shots),
            "anzahl_einheiten": len(units),
            "aufloesung": "%dx%d" % (meta.width, meta.height),
            "seitenverhaeltnis": meta.aspect_ratio,
            "orientierung": meta.orientation,
            "fps": round(meta.fps, 3),
            "analyse_modus": mode_label,
            "hauptprodukt": (global_head or {}).get("hauptprodukt", ""),
            "produkt_strategie": (global_head or {}).get("produkt_strategie", ""),
            "gesamtstil_vibe": (global_head or {}).get("gesamtstil_vibe", ""),
            "gesamttempo_schnittrhythmus": (global_head or {}).get("gesamttempo_schnittrhythmus", ""),
            "wiederkehrende_elemente": (global_head or {}).get("wiederkehrende_elemente", ""),
            "roter_faden": (global_head or {}).get("roter_faden", ""),
            "kurzzusammenfassung": summary or "",
        },
        "shots": unit_objs,
    }
