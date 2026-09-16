#!/usr/bin/env bash
# install.sh - Odex cross-server venv installer (Linux / macOS).
# Steps: [1] resolve system python (python3.x / python3 / python, 3.10+; apt/brew fallback)
#        [2] show which-selection, first path = default execution
#        [3] pip-freeze check, drop broken venv
#        [4] create venv + install + verify + wire app.config
# Safe to re-run anytime. Usage: ./install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
VPY="$ROOT/odex/venv/bin/python"
MIN_MINOR=10

echo "=== Odex venv installer ==="
echo "ROOT: $ROOT"
echo

# ---- STEP 1: resolve system python ----
echo "[1/5] resolving system python ..."
PY=""
PYVER=""
pick_candidate() {
  local c="$1"
  local v maj min
  if ! command -v "$c" >/dev/null 2>&1; then return 1; fi
  v="$("$c" --version 2>&1 | awk '{print $2}')"
  maj="${v%%.*}"; rest="${v#*.}"; min="${rest%%.*}"
  if [[ "$maj" == "3" && "$min" =~ ^[0-9]+$ ]] && (( min >= MIN_MINOR )); then
    PY="$c"; PYVER="$v"
    echo "    found $c: Python $v"
    return 0
  fi
  return 1
}

for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if pick_candidate "$c"; then break; fi
done

install_sys_python() {
  echo "[--] no Python 3.${MIN_MINOR}+ found - trying system package manager ..."
  local SUDO=""
  if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update
    # python3-venv is REQUIRED on Debian/Ubuntu (ensurepip is stripped)
    $SUDO apt-get install -y python3 python3-venv python3-pip build-essential unixodbc-dev 2>/dev/null \
      || $SUDO apt-get install -y python3 python3-venv python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip unixODBC-devel gcc
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip unixODBC-devel gcc
  elif command -v apk >/dev/null 2>&1; then
    $SUDO apk add --no-cache python3 py3-pip py3-virtualenv gcc musl-dev unixodbc-dev
  elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -Sy --noconfirm python python-pip unixodbc gcc
  elif command -v zypper >/dev/null 2>&1; then
    $SUDO zypper install -y python3 python3-pip python3-virtualenv unixODBC-devel gcc
  elif command -v brew >/dev/null 2>&1; then
    brew install python@3.13 || brew install python
  else
    return 1
  fi
}

if [[ -z "$PY" ]]; then
  if install_sys_python; then
    hash -r 2>/dev/null || true
    for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
      if pick_candidate "$c"; then break; fi
    done
  fi
fi
if [[ -z "$PY" ]]; then
  echo "[X] Python 3.${MIN_MINOR}+ not found and auto-install failed."
  echo "    Install manually, then re-run ./install.sh:"
  echo "      sudo apt install -y python3 python3-venv python3-pip   # Debian/Ubuntu"
  echo "      sudo dnf install -y python3 python3-pip                # RHEL/Fedora"
  exit 1
fi
echo "[ok] system python: $PY ($PYVER)"

# venv module must exist (Debian splits it into python3-venv)
if ! "$PY" -m venv --help >/dev/null 2>&1; then
  echo "[--] '$PY -m venv' unavailable - installing venv package ..."
  if install_sys_python; then
    hash -r 2>/dev/null || true
  fi
  if ! "$PY" -m venv --help >/dev/null 2>&1; then
    echo "[X] python venv module still missing."
    exit 1
  fi
fi

# ---- STEP 2: which-selection, first path wins ----
echo
echo "[2/5] interpreter locations (first path = default execution) ..."
# shellcheck disable=SC2086
which -a $PY python3 python 2>/dev/null | awk '!seen[$0]++' || true
PYFULL="$(which "$PY" 2>/dev/null || true)"
if [[ -n "$PYFULL" ]]; then
  echo "[*] default execution: $PYFULL"
  PY="$PYFULL"  # pin to first path so later PATH changes cannot swap it
else
  echo "[*] default execution: $PY (unresolved, PATH order applies)"
fi

# ---- STEP 3: pip-freeze check, drop broken venv ----
echo
echo "[3/5] checking existing venv ..."
if [[ ! -x "$VPY" ]]; then
  echo "[--] no venv yet at odex/venv."
else
  if ! "$VPY" --version >/dev/null 2>&1; then
    echo "[--] venv interpreter dead - removing odex/venv ..."
    rm -rf "$ROOT/odex/venv"
  elif ! "$VPY" -m pip freeze >/dev/null 2>&1; then
    echo "[--] venv pip broken (freeze failed) - removing odex/venv ..."
    rm -rf "$ROOT/odex/venv"
  else
    echo "[ok] existing venv runs - $("$VPY" -m pip freeze 2>/dev/null | wc -l) packages frozen."
  fi
fi

# ---- STEP 4: create venv if missing ----
echo
if [[ ! -x "$VPY" ]]; then
  echo "[4/5] creating venv with: $PY ..."
  if ! "$PY" -m venv "$ROOT/odex/venv"; then
    echo "[--] standard venv failed - retrying without pip + ensurepip ..."
    "$PY" -m venv --without-pip "$ROOT/odex/venv"
    "$VPY" -m ensurepip --upgrade
  fi
  echo "[ok] venv created."
else
  echo "[4/5] venv present - skipping creation."
fi

# ---- STEP 5: install + verify + wire ----
echo
echo "[5/5] upgrading pip ..."
"$VPY" -m pip install --upgrade pip

echo
echo "[5/5] installing requirements (takes a few minutes) ..."
REQS=()
for f in odex/requirements.txt rml_python/requirements.txt fmlk_engine/requirements.txt permissions_engine/requirements.txt; do
  [[ -f "$ROOT/$f" ]] && REQS+=(-r "$ROOT/$f")
done
if [[ ${#REQS[@]} -eq 0 ]]; then
  echo "[--] no requirements files found - installing runtime extras only."
fi
"$VPY" -m pip install "${REQS[@]}" cryptography openpyxl reportlab arabic-reshaper python-bidi

echo
echo "[5/5] verifying ..."
"$VPY" -c "import django, psycopg2, oracledb, pyodbc, fastapi, lxml, reportlab, openpyxl, cryptography; from zk import ZK; print('venv ok, django', django.__version__)"

echo
echo "[5/5] wiring app.config ..."
SEEDROOT="$ROOT" "$VPY" manage.py shell --settings=config.settings_local -c \
  "import pathlib,os; from config.dbconf import write_appconf; print('app.config:', write_appconf({'PYTHON': str(pathlib.Path(os.environ.get('SEEDROOT','')) / 'odex' / 'venv' / 'bin' / 'python')}))" \
  || echo "[--] app.config wiring skipped - non-fatal - set PYTHON manually."

echo
echo "========================================"
echo " DONE - venv ready: odex/venv"
echo " interpreter: $VPY"
echo " Run ./run.sh (or run.bat on Windows) to launch."
