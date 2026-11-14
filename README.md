# Hentai-Telegram

**Hentai-Telegram** 是一个专为 **Telegram 频道** 打造的禁漫自动化抓取与发布机器人。

机器人收到管理员发送的禁漫漫画 ID 或链接后，会自动下载章节图片并还原切片混淆，将每一话完整转存为 **Telegraph (Instant View)** 在线阅读文章，多章节漫画自动生成全本目录导航，最后携带高清封面、漫画元数据与阅读按钮直接发布至指定的 **Telegram 频道**。

---

## 🌟 核心特性

- **直连 Telegram 频道**：漫画全部通过 Telegraph 托管并在 Telegram 频道发布，用户在 Telegram 内点击即可通过 Instant View（即时预览）无广告极速阅读，无需自建 Komga 或阅读器服务器。
- **纯私密管理员模式**：移除普通用户注册、邀请码、点赞/举报等交互，仅配置的管理员数字 ID 能够向机器人下发下载任务，杜绝非授权滥用。
- **极简轻量单机架构**：移除多 VPS 节点分发与网页管理后台，无需开放任何外部入站端口（8080/8081），纯靠 Telegram 长轮询与 HTTPS 出站请求运行，安全低耗。
- **零磁盘空间占用**：下载与切片还原仅在本地暂存（Staging），一旦图片上传至 Telegraph 即刻销毁本地文件，无需预留大容量硬盘。
- **Telegraph 自动化管理**：内置 Telegraph 账号自动创建与 Token 本地持久化，自动分批上传图片、构建章节页面与全本目录索引。
- **实时进度反馈**：提交任务后，Bot 在管理员私聊中实时编辑同一条消息动态展示“下载中”、“上传 Telegraph 中”、“发布中”等进度，任务完成回传频道消息直达链接。

---

## 🚀 快速部署

### 方式一：Docker Compose（推荐）

#### 1. 克隆代码与配置环境

```bash
git clone https://github.com/oKafuChino/Hentai-Telegram.git
cd Hentai-Telegram
cp .env.example .env
```

#### 2. 编辑配置文件 `.env`

```dotenv
# Telegram Bot Token（通过 @BotFather 获取）
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

# 管理员数字 ID（可通过 @userinfobot 获取），逗号分隔
TELEGRAM_ALLOWED_USER_IDS=123456789

# 发布目标 Telegram 频道 ID（例如 -1001234567890）或公开频道用户名（例如 @my_channel）
# 注意：请务必先将 Bot 加入该频道并提升为管理员（授予发布消息权限）
TELEGRAM_CHANNEL_ID=-1001234567890

# Telegraph 作者名称（显示在文章顶部）
TELEGRAPH_AUTHOR_NAME=Hentai-Telegram
# Telegraph 作者链接（可选，例如频道的链接）
TELEGRAPH_AUTHOR_URL=https://t.me/my_channel
```

#### 3. 检查配置与频道权限

```bash
docker compose run --rm app check-config
docker compose run --rm app check-channel
```

#### 4. 启动服务

```bash
docker compose up -d app
docker compose logs -f app
```

---

### 方式二：交互式脚本一键安装 (Linux VPS)

```bash
bash install.sh
```

脚本将引导你填写 Bot Token、管理员 ID 和发布频道，并自动构建镜像并启动。

---

### 方式三：本地 Python 原生运行

环境要求：Python 3.12+

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .

# 配置环境变量后启动
python -m hentai_telegram.main run
```

---

## 🤖 Bot 指令说明

> ⚠️ **所有指令仅限 `.env` 中配置的管理员可用**。非管理员私聊机器人将收到权限拦截提醒。

| 指令 | 说明 |
| :--- | :--- |
| `/start` 或 `/help` | 查看使用指南与可用指令列表 |
| `/jm <漫画ID或链接>` | 提交抓取任务并自动发布到频道（例如 `/jm 123456`） |
| **直接发送数字 ID / 链接** | 私聊中直接发送纯数字 ID 或漫画链接，机器人自动识别入队 |
| `/tasks` | 查看当前正在下载、上传或排队中的任务列表及进度 |
| `/cancel <任务ID>` | 取消正在进行或排队中的任务 |
| `/retry <任务ID>` | 重试之前失败的任务 |
| `/channel` | 检查并验证当前配置的 Telegram 频道连通性与 Bot 管理权限 |
| `/status` | 查看服务运行时长、Worker 心跳、磁盘缓存与任务总量统计 |
| `/logs` | 查看最近 100 条脱敏系统日志 |

---

## 📖 频道发布效果展示

成功抓取并转存后，Bot 会自动向频道推送格式化图文消息：

```text
📚 漫画标题名称
👨‍🎨 作者：作者名
🏷 标签：标签1, 标签2, 标签3
🔢 漫画 ID：JM123456

📖 在线阅读（Telegraph）：
• 第 1 话：第一话标题
• 第 2 话：第二话标题
• 📑 查看全本完整目录（多章节自动生成）

[ Inline Button: 📖 点击在线阅读 ] 或 [ 📑 查看全本目录 ]
```

---

## 🛠 运维与诊断

- **检查数据源元数据**（不下载图片，测试 JM API 连通性）：
  ```bash
  docker compose run --rm app check-source --manga-id 123456
  ```
- **备份数据库**：
  ```bash
  docker compose exec app python -m hentai_telegram.main backup --destination /app-data/backup.db
  ```
- **服务升级**：
  ```bash
  bash update.sh
  ```
- **重置 Telegraph Token**：
  删除 `data/app/telegraph_token.txt` 后重启服务，Bot 将自动重新注册新的 Telegraph 账号。


---

## ⚡ 高并发图像处理与发布流水线 (Manga Telegraph Pipeline)

面向 Telegram 漫画频道的高并发自动化图像处理与发布流水线，严格遵守内存流式处理与零磁盘临时文件原则。

### 🔄 核心处理流程 (Pipeline)

1. **输入层 (`Input`)**：自然数排序扫描原始图片序列（支持 `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`）。
2. **压缩与切片层 (`Compressor`)**：`ProcessPoolExecutor` 多进程流式处理：
   - **色彩分流**：根据通道均值差自适应识别黑白漫（`8-bit L` 灰度，宽限制 `1280px`，质量 `75`）与彩漫（`RGB`，宽限制 `1440px`，质量 `80`）。
   - **条漫切片**：高度超过 `2400px` 自动横向切割；尾段不足 `300px` 自动并入上一段，杜绝细碎边角。
   - **无状态编码**：剥离 EXIF/ICC 元数据，内存中以 `WEBP (method=6)` 编码输出字节流。
3. **存储层 (`Storage R2`)**：`ThreadPoolExecutor` 并发上传至 Cloudflare R2，附带 1 年长期缓存响应头 `Cache-Control: public, max-age=31536000`。
4. **排版层 (`Telegraph Publisher`)**：超长章节自动分页（超过 100 张自动切分上、下卷，双向注入互跳导航栏）。
5. **分发与预热层 (`Telegram Bot`)**：先发送至私有缓存频道强制 Telegram 爬虫建立 Instant View 缓存，延迟预热后向正式频道推送精美排版图文消息。

### 💻 命令行使用方法 (CLI)

```bash
# 1. 独立图像压缩与切片性能测试（不执行网络上传与发布）
python -m src.compressor --dir ./downloads/1001/01 --workers 2

# 2. 流水线预演模式（DRY RUN 仅在内存测试压缩统计）
python main.py --dir ./downloads/1001/01 --id 1001 --ch 1 --title "示例漫画 第01话" --dry-run

# 3. 完整端到端上传与频道发布
python main.py --dir ./downloads/1001/01 --id 1001 --ch 1 --title "示例漫画 第01话" --tags "同人,全彩"
```
