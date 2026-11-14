#!/usr/bin/env bash
# Run from the project directory: bash update.sh
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
trap 'echo "更新未完成（行 $LINENO）。配置和数据未删除；请检查错误后重新运行。" >&2' ERR
[[ $(uname -s) == Linux ]] || { echo '仅支持 Linux VPS。' >&2; exit 1; }
[[ -f .env ]] || { echo '未找到 .env，请先运行 bash install.sh。' >&2; exit 1; }
command -v git >/dev/null
git rev-parse --is-inside-work-tree >/dev/null
if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
  echo '项目存在本地代码改动或未跟踪文件，请先提交或妥善保存后更新。不会自动覆盖或清理文件。' >&2
  git status --short
  exit 1
fi
git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1 || {
  echo '当前分支未配置上游，请先配置 Git 跟踪分支。' >&2; exit 1;
}
if [[ $EUID == 0 ]]; then SUDO=(); else SUDO=(sudo); command -v sudo >/dev/null; fi
"${SUDO[@]}" docker compose version >/dev/null
"${SUDO[@]}" docker info >/dev/null
old_commit=$(git rev-parse HEAD)
git pull --ff-only
"${SUDO[@]}" docker compose config --quiet

"${SUDO[@]}" docker compose build --pull app
"${SUDO[@]}" docker compose run --rm --no-deps app check-config

backup_dir="data/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
"${SUDO[@]}" install -d -m 700 "$backup_dir"
printf '升级前提交：%s\n备份目录：%s\n' "$old_commit" "$backup_dir"
"${SUDO[@]}" docker compose stop app
backup_items=(.env)
for directory in data/app; do
  [[ ! -d "$directory" ]] || backup_items+=("$directory")
done
"${SUDO[@]}" tar -czf "$backup_dir/state.tar.gz" -- "${backup_items[@]}"
"${SUDO[@]}" chmod 600 "$backup_dir/state.tar.gz"
echo '已备份配置和服务数据库。'

"${SUDO[@]}" docker compose up -d --no-deps app
"${SUDO[@]}" docker compose ps
echo 'Hentai-Telegram 更新完成！'
echo '日志：sudo docker compose logs --tail 100 app'
