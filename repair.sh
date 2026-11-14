#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ $(uname -s) == Linux ]] || { echo '仅支持 Linux VPS。'; exit 1; }
[[ -f .env ]] || { echo '未找到 .env。'; exit 1; }
if [[ $EUID == 0 ]]; then SUDO=(); else SUDO=(sudo); fi
"${SUDO[@]}" docker compose config --quiet
"${SUDO[@]}" docker compose stop app
echo '修复服务数据目录权限；如失败，服务保持停止，请检查上方错误。'
"${SUDO[@]}" python3 scripts/repair_permissions.py
"${SUDO[@]}" docker compose up -d app
echo 'Hentai-Telegram 已重新启动。请检查 docker compose logs --tail 100 app。'
