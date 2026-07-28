#!/bin/bash
# Двойной клик: активирует venv, поднимает сервер и открывает браузер.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Виртуальное окружение не найдено, создаю..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

source .venv/bin/activate
python3 hub.py init

( sleep 1.5 && open "http://127.0.0.1:8765/" ) &

python3 hub.py serve
