#!/usr/bin/env bash
# One-shot setup for macOS: virtualenv, dependencies, models, doctor.
# Uses uv (fast, fetches Python 3.12 itself) when available, plain python3 -m venv otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VERSION="${DAXTON_PYTHON:-3.12}"

if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "==> creating .venv with uv (Python ${PY_VERSION})"
    uv venv --python "${PY_VERSION}" .venv
  else
    echo "==> creating .venv with $(python3 --version)"
    python3 -m venv .venv
  fi
fi

echo "==> installing daxton-ai with all optional extras"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -e ".[all,dev]"
else
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -e ".[all,dev]"
fi

# macOS "hidden" file flags break editable installs: Python 3.12+ refuses to process a hidden .pth file,
# so the finder that makes `import daxton` work never loads. uv sets the flag on .venv, and folders linked
# to the Claude desktop app get every dot-folder re-flagged within seconds, so clearing it is not enough.
# A sitecustomize.py in the venv (which Python imports regardless of flags) adds the repo as a fallback.
DAXTON_REPO_DIR="$(pwd)" .venv/bin/python - <<'EOF'
import os, pathlib, sysconfig
repo = os.environ["DAXTON_REPO_DIR"]
target = pathlib.Path(sysconfig.get_paths()["purelib"]) / "sitecustomize.py"
target.write_text(
    "# Written by scripts/setup_mac.sh. If the editable install's .pth was skipped (macOS hidden flag,\n"
    "# Python 3.12+), fall back to importing daxton straight from the repository checkout.\n"
    "import sys\n"
    "try:\n"
    "    import daxton  # noqa: F401\n"
    "except ImportError:\n"
    f"    sys.path.append({repo!r})\n"
)
print(f"==> wrote {target}")
EOF
if [ "$(uname)" = "Darwin" ]; then
  chflags -R nohidden .venv 2>/dev/null || true
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> created .env from .env.example; add your keys there"
fi

echo "==> downloading models (Whisper base.en, plus a wake-word model when one is configured)"
.venv/bin/daxton download-models || echo "   (model download failed; they will be fetched on first run)"

echo "==> running the doctor"
.venv/bin/daxton doctor || true

cat <<'EOF'

Done. Next:
  source .venv/bin/activate
  daxton doctor --online     # verifies your keys with the APIs
  daxton chat                # text mode
  daxton                     # voice mode: say "Daxton, ..." in a sentence
EOF
