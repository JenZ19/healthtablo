#!/bin/bash
# Выкатить код на сервер. Данные не трогаются: они лежат отдельно, и
# --delete до них не достаёт.
#
#   HUB_HOST=root@сервер HUB_KEY=~/.ssh/ключ ./deploy/push-code.sh
set -euo pipefail

HOST=${HUB_HOST:?укажите HUB_HOST, например root@192.0.2.1}
KEY=${HUB_KEY:-$HOME/.ssh/id_ed25519}
DEST=${HUB_APP_DIR:-/opt/health-hub/app}
HERE=$(cd "$(dirname "$0")/.." && pwd)

rsync -az --delete -e "ssh -i $KEY" \
  --exclude=.venv --exclude=data --exclude=inbox --exclude=__pycache__ \
  --exclude=.pytest_cache --exclude='*.pyc' --exclude=.git \
  "$HERE/" "$HOST:$DEST/"

ssh -i "$KEY" "$HOST" "chown -R healthhub:healthhub $DEST && systemctl restart health-hub"
echo "выкачено, служба перезапущена"
