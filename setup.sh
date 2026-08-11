#!/bin/bash
# Einmaliges Setup: venv anlegen und alle Abhängigkeiten installieren.
# Aufruf:  bash setup.sh
set -e
cd "$(dirname "$0")"

echo "==> Erstelle virtuelle Umgebung (venv) ..."
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip

echo "==> Installiere Abhängigkeiten (Schritt 1/2) ..."
./venv/bin/pip install --quiet -r requirements.txt

# Schritt 2: google-genai braucht websockets>=13, Gradio 4 deklariert <13.
# Gradio 4 nutzt websockets zur Laufzeit nicht (Kommunikation läuft über SSE),
# daher ist websockets 13.x mit beiden funktional kompatibel. Die
# pip-Warnung zu "gradio-client requires websockets<13.0" ist erwartet und harmlos.
echo "==> Installiere google-genai (Schritt 2/2) ..."
echo "    (Hinweis: eine Warnung zu 'gradio-client ... websockets' ist hier normal.)"
./venv/bin/pip install --quiet "google-genai>=1.0" "websockets>=13,<14"

echo ""
echo "✅ Setup fertig."
echo "Nächste Schritte:"
echo "  1. API-Key setzen: Datei .env mit Inhalt  GEMINI_API_KEY=DEIN_KEY  anlegen"
echo "  2. Starten:        ./venv/bin/python app.py"
