#!/usr/bin/env bash
# Run from a checked-out release: bash install.sh
set -Eeuo pipefail
trap 'echo "安装未完成（行 $LINENO）。已有配置和数据已保留，修复后可重新运行。" >&2' ERR
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ $(uname -s) == Linux ]] || { echo '仅支持 Linux VPS。' >&2; exit 1; }
[[ -t 0 ]] || { echo '请在交互终端运行 bash install.sh。' >&2; exit 1; }
if [[ $EUID == 0 ]]; then SUDO=(); else SUDO=(sudo); command -v sudo >/dev/null; fi
if ! command -v docker >/dev/null || ! command -v python3 >/dev/null; then
  command -v apt-get >/dev/null || { echo '请先安装 Docker Engine、Compose v2 和 Python 3。'; exit 1; }
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker.io python3
fi
if ! "${SUDO[@]}" docker compose version >/dev/null 2>&1; then
  command -v apt-cache >/dev/null || { echo '请安装 Docker Compose v2 后重试。'; exit 1; }
  if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get install -y docker-compose-v2
  elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get install -y docker-compose-plugin
  else
    echo '当前软件源没有 Compose v2，请按 https://docs.docker.com/engine/install/ 安装后重试。'
    exit 1
  fi
fi
if ! "${SUDO[@]}" docker info >/dev/null 2>&1; then
  "${SUDO[@]}" systemctl start docker
fi
python3 scripts/configure.py initial
"${SUDO[@]}" docker compose config --quiet

read -r APP_UID APP_GID < <(python3 scripts/configure.py ids)
for directory in data data/app data/storage; do
  "${SUDO[@]}" install -d -m 750 -o "$APP_UID" -g "$APP_GID" "$directory"
  "${SUDO[@]}" chown -R "$APP_UID:$APP_GID" "$directory"
done

"${SUDO[@]}" docker compose build app
"${SUDO[@]}" docker compose run --rm app check-config
echo '检查 Telegram 频道权限...'
"${SUDO[@]}" docker compose run --rm app check-channel || true
echo '检查 R2 / B2 对象存储连接...'
"${SUDO[@]}" docker compose run --rm app check-storage || true

"${SUDO[@]}" docker compose up -d app
"${SUDO[@]}" docker compose ps
echo '=================================================='
echo 'Hentai-Telegram 服务已成功启动！'
echo '请使用管理员账号在 Telegram 中私聊机器人发送 /start 开始使用。'
echo '查看运行日志：sudo docker compose logs -f app'
echo '=================================================='
