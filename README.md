# Hentai-Telegram

**Hentai-Telegram** 是一个专为 **Telegram 频道** 打造的高并发自动化漫画抓取、智能处理与即时发布系统。

机器人接收到管理员提交的漫画 ID 或链接后，在内存中进行下载、切片还原、智能色彩分流与条漫切片，无损压缩后并发上传至 **Cloudflare R2 / Backblaze B2** 对象存储，随后自动排版生成 **Telegraph (Instant View)** 极速阅读文章，并通过缓存频道完成 Instant View 预热，最后携带高清封面与元数据直接发布至指定的 **Telegram 频道**。

---

## 🌟 核心特性

- **直连 Telegram 频道与 Instant View 即时预览**：漫画全部通过 Telegraph 在线托管并在 Telegram 频道发布，用户在移动端点击即可秒开即时预览（Instant View），沉浸式无广告阅读。
- **对象存储图床直连 (Cloudflare R2 / Backblaze B2)**：
  - 支持直接接入 Cloudflare R2 或 Backblaze B2（兼容 S3 协议）。
  - 上传自动注入 `Cache-Control: public, max-age=31536000`（1 年长期缓存）响应头，生成自定义域名直链。
  - 彻底摆脱公共图床易限流、失效、被删图等问题。
- **智能图像压缩与切片引擎 (零磁盘临时文件)**：
  - **色彩分流**：自动计算 RGB 通道均值差，识别黑白漫（转为 `8-bit L` 单通道灰度）与彩漫（`RGB`），大幅减除冗余体积。
  - **视口对齐**：使用 `Lanczos` 算法限制最大视口宽度，兼顾手机/平板 Retina 视网膜屏细腻度与加载速度。
  - **条漫智能切片**：对高度超过 `2400px` 的长图自动横向切割；尾段不足 `300px` 自动合并，杜绝细碎裂片。
  - **全程内存流式**：所有缩放、切片、编码均在 `io.BytesIO` 内存中完成，不产生任何本地临时文件。
- **超长章节自动分页与双向导航**：单话切片超过 100 张时，自动切分为（上）、（下）分卷发布并双向注入互跳导航条，防止 Telegram 爬虫抓取超时。
- **原生 Instant View 即时预览卡片直发**：直接向主频道发送消息，采用 Telegram 原生 Link Preview Options 规范（锁定 Telegraph 链接并启用大图媒体卡片展示），直接在主频道消息顶部生成包含封面大图与“⚡ Instant View”极速阅读按钮的卡片，免去私有预热频道的繁琐配置。
- **纯私密管理员模式**：无公开交互，仅管理员可提交任务，杜绝非授权滥用与资源消耗。

---

## 📦 对象存储与图床配置 (Cloudflare R2 / Backblaze B2)

由于 Telegraph 官方上传接口 (`telegra.ph/upload`) 已于 2024 年停用，强烈建议配置 **Cloudflare R2** 或 **Backblaze B2** 作为私有图床。

### 1. 为什么推荐 Cloudflare R2？
- **零出站流量费（Free Egress）**：无论频道有多少读者反复阅读漫画图片，Cloudflare 均不收取任何出站流量费用。
- **全球边缘 CDN 加速**：配合自定义域名，全球访问延迟极低，与 Telegram 爬虫及客户端连接极速。
- **免费额度充足**：Cloudflare 每月提供 10GB 免费存储和 1000 万次免费读操作。

---

### 2. Cloudflare R2 配置步骤

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/)，在左侧菜单点击 **R2 对象存储**。
2. 点击 **创建存储桶**（Bucket），命名为你的桶名（例如 `manga-bucket`）。
3. **绑定自定义域名**：
   - 进入该存储桶设置 -> **设置** -> **公开访问** -> **自定义域**。
   - 绑定你的二级域名（例如 `img.yourdomain.com`），Cloudflare 会自动完成 SSL 证书签发与 CDN 代理。
4. **生成 API 访问凭据**：
   - 返回 R2 概览页，点击右侧的 **管理 R2 API 令牌** -> **创建 API 令牌**。
   - 权限选择 **对象读写**，指定存储桶或所有存储桶。
   - 创建后保存复制：`账号 ID (Account ID)`、`访问密钥 ID (Access Key ID)`、`机密访问密钥 (Secret Access Key)`。
5. 将凭据填入项目的 `.env` 配置文件即可：

```dotenv
# --- Cloudflare R2 配置 ---
R2_ACCOUNT_ID=你的_Cloudflare_账号_ID
R2_ACCESS_KEY_ID=你的_R2_Access_Key_ID
R2_SECRET_ACCESS_KEY=你的_R2_Secret_Access_Key
R2_BUCKET_NAME=manga-bucket
R2_CUSTOM_DOMAIN=https://img.yourdomain.com
```

> **注意**：项目底层全面兼容 S3 协议，若使用通用 S3 变量名（`S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_URL`）同样完全兼容。

---

### 3. Backblaze B2 配置说明

若使用 Backblaze B2，创建 Bucket 并设置为 **Public**，获取应用密钥后在 `.env` 中配置：

```dotenv
S3_ENDPOINT_URL=https://s3.us-west-004.backblazeb2.com
S3_BUCKET_NAME=your-b2-bucket
S3_ACCESS_KEY_ID=your_key_id
S3_SECRET_ACCESS_KEY=your_application_key
S3_PUBLIC_URL=https://img.yourdomain.com
S3_REGION_NAME=us-west-004
```

---

### 4. 存储连通性自检命令

配置完成后，运行以下命令测试上传与外链访问权限：

```bash
# Docker 运行
docker compose run --rm app check-storage

# 本地 Python 运行
python -m hentai_telegram.main check-storage
```

---

## 🖼️ 智能图片压缩与切片优化

系统内置两种优化模式：
1. **Telegram 机器人下载主程序**：执行 Progressive JPEG 优化，自适应等比缩放并移除元数据，保障在任何 Telegram 官方客户端内 100% 正常显示。
2. **高并发流水线批处理（Pipeline）**：支持多进程条漫横向切片与内存流式 `WEBP (method=6)` 高能压缩，单话体积可缩减 **70% ~ 90%**。

### 压缩与切片参数一览表

| 环境变量参数 | 默认值 | 作用说明 |
| :--- | :---: | :--- |
| `IMAGE_OPTIMIZE_ENABLED` | `true` | 是否启用图片体积优化（`true`/`false`） |
| `IMAGE_JPEG_QUALITY` | `85` | JPEG 编码画质（50-95，85 为画质与体积的最佳黄金平衡） |
| `IMAGE_MAX_WIDTH` | `1600` | 超大扫描图等比例缩放的最大像素宽度（设为 `0` 则保持原尺寸） |
| `IMAGE_MIN_COMPRESS_BYTES` | `102400` | 触发压缩的最小文件大小（100KB，低于该大小且合规图片跳过处理） |
| `CPU_WORKERS` | `2` | 图像多进程处理并发进程数（推荐 2~4，防止小内存 VPS 解码触发 OOM） |
| `IO_UPLOAD_WORKERS` | `16` | 对象存储并发上传线程数 |
| `MAX_WIDTH_BW` | `1280` | 黑白漫画页面限制最大宽度（px） |
| `MAX_WIDTH_COLOR` | `1440` | 彩色漫画页面限制最大宽度（px） |
| `MAX_SLICE_HEIGHT` | `2400` | 长条漫自动横向切割高度阈值（px） |
| `QUALITY_BW` | `75` | 黑白页 WebP 编码质量（0-100） |
| `QUALITY_COLOR` | `80` | 彩页 WebP 编码质量（0-100） |
| `GRAYSCALE_THRESHOLD` | `3.5` | 黑白/彩色识别阈值（计算 R-G 与 G-B 通道均值差，小于此值判定为黑白页） |

---

## 🚀 快速部署与使用

### 方式一：Docker Compose（推荐）

#### 1. 克隆代码与配置环境

```bash
git clone https://github.com/oKafuChino/Hentai-Telegram.git
cd Hentai-Telegram
cp .env.example .env
```

#### 2. 编辑配置文件 `.env`

填入你的 Bot Token、管理员 ID、发布频道及 R2 存储配置：

```dotenv
# --- Telegram Bot 配置 ---
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_CHANNEL_ID=-1001234567890

# --- Cloudflare R2 对象存储 ---
R2_ACCOUNT_ID=your_cloudflare_account_id
R2_ACCESS_KEY_ID=your_r2_access_key
R2_SECRET_ACCESS_KEY=your_r2_secret_key
R2_BUCKET_NAME=manga-bucket
R2_CUSTOM_DOMAIN=https://img.yourdomain.com

# --- 图像优化配置 ---
IMAGE_OPTIMIZE_ENABLED=true
IMAGE_MIN_COMPRESS_BYTES=102400   # 100KB 阈值，大于等于该体积的图片必定被压缩优化
MAX_WIDTH_BW=1280                 # 黑白页限制 1280px，转 8-bit 单通道灰度
MAX_WIDTH_COLOR=1440              # 彩页限制 1440px
QUALITY_BW=75                     # 黑白页 Progressive JPEG 质量 (推荐 75)
QUALITY_COLOR=80                  # 彩页 Progressive JPEG 质量 (推荐 80)
GRAYSCALE_THRESHOLD=3.5           # 灰度判定阈值
MAX_SLICE_HEIGHT=2400             # 条漫切片高度阈值
```

#### 3. 检查配置与频道权限

```bash
docker compose run --rm app check-config
docker compose run --rm app check-storage
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

---

### 方式三：本地 Python 原生运行

环境要求：Python 3.10+

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
pip install -e .

# 启动后台常驻机器人
python -m hentai_telegram.main run
```

---

## ⚡ 批处理与流水线命令行 (Pipeline CLI)

除了通过 Telegram Bot 对话触发任务外，系统还提供独立的命令行批处理总控流水线，支持单章节本地测试与批量发布：

```bash
# 1. 独立图像压缩与切片基准测试 (纯内存处理，不执行任何网络上传与发布)
python -m src.compressor --dir ./downloads/1001/01 --workers 2

# 2. 流水线预演模式 (DRY RUN，打印原图/压缩后体积、缩减比例及切片统计)
python main.py --dir ./downloads/1001/01 --id 1001 --ch 1 --title "示例漫画 第01话" --dry-run

# 3. 完整端到端发布 (流式切片压缩 -> R2 并发上传 -> Telegraph 分页 -> Telegram 主频道直发生成 Instant View)
python main.py --dir ./downloads/1001/01 --id 1001 --ch 1 --title "示例漫画 第01话" --tags "同人,全彩"
```

---

## 🤖 Bot 管理员指令说明

> ⚠️ 所有指令仅限 `.env` 中配置的管理员可用，非授权用户将收到权限拦截提醒。

| 指令 | 说明 |
| :--- | :--- |
| `/start` 或 `/help` | 查看使用指南与可用指令列表 |
| `/jm <漫画ID或链接>` | 提交抓取任务并自动发布到频道（例如 `/jm 123456` 或 `/jm JM123456`） |
| **直接发送数字 ID / 链接** | 私聊中直接发送纯数字 ID 或漫画链接，机器人自动识别入队 |
| `/tasks` | 查看当前正在下载、压缩上传或排队中的任务列表及实时进度 |
| `/cancel <任务ID>` | 取消正在进行或排队中的任务 |
| `/retry <任务ID>` | 重试之前失败的任务 |
| `/del <漫画ID/链接/Telegraph链接>` | 移除指定漫画或页面：全量清空Telegraph页面、撤回频道消息、清理R2存储文件、本地缓存与数据库记录（别名 `/delete`、`/rm`） |
| `/channel` | 检查并验证当前配置的 Telegram 频道连通性与 Bot 管理权限 |
| `/status` | 查看服务运行时长、Worker 心跳、磁盘缓存与任务总量统计 |
| `/logs` | 查看最近 100 条脱敏系统运行日志 |

---

## 🛠 运维与诊断指令

- **诊断配置项与优化状态**：
  ```bash
  python -m hentai_telegram.main check-config
  ```
- **检查对象存储 R2 / B2 连通性与上传权限**：
  ```bash
  python -m hentai_telegram.main check-storage
  ```
- **检查 Telegram 频道管理员权限**：
  ```bash
  python -m hentai_telegram.main check-channel
  ```
- **测试禁漫 API 数据源连通性（不下载文件）**：
  ```bash
  python -m hentai_telegram.main check-source --manga-id 123456
  ```
- **备份数据库**：
  ```bash
  docker compose exec app python -m hentai_telegram.main backup --destination /app-data/backup.db
  ```
- **重置 Telegraph Token**：
  删除 `data/app/telegraph_token.txt` 后重启服务，Bot 将自动重新注册新的 Telegraph 账号。
