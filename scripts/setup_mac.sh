#!/usr/bin/env bash
# One-shot setup for macOS: virtualenv, dependencies, models, doctor.
# Uses uv (fast, fetches Python 3.12 itself) when available, plain python3 -m venv otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VERSION="${JARVIS_PYTHON:-3.12}"

if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "==> creating .venv with uv (Python ${PY_VERSION})"
    uv venv --python "${PY_VERSION}" .venv
  else
    echo "==> creating .venv with $(python3 --version)"
    python3 -m venv .venv
  fi
fi

echo "==> installing jarvis with all optional extras"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -e ".[all,dev]"
else
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -e ".[all,dev]"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> created .env from .env.example; add your keys there"
fi

echo "==> downloading models (Whisper base.en and the hey_jarvis wake word)"
.venv/bin/jarvis download-models || echo "   (model download failed; they will be fetched on first run)"

echo "==> running the doctor"
.venv/bin/jarvis doctor || true

cat <<'EOF'

Done. Next:
  source .venv/bin/activate
  jarvis doctor --online     # verifies your keys with the APIs
  jarvis chat                # text mode
  jarvis                     # voice mode: say "hey Jarvis"
EOF
