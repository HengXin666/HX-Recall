# HX-Recall

B站收藏夹吃灰清灰工具 —— 定时从你的收藏夹中挑选好内容推送给你, 不让好收藏在角落里落灰。

## 功能特点

- **4 种选取策略**: 随机回顾、最近收藏、往期回顾、吃灰清灰(优先推送最久未见的)
- **增量爬取 + 断点续爬**: 收藏夹视频逐页爬取并即时存盘, 中断不丢数据
- **AI 内容增强**: 自动获取视频 AI 总结、评论区 AI 总结、热门评论
- **SESSDATA 自动续期**: 配置 `refresh_token` 后 Cookie 自动刷新, 几乎不再需要手动更新
- **浏览器登录回退**: 本地运行凭证失效时, 自动打开浏览器登录并提取 Cookie
- **多渠道推送**: 邮件(精美 HTML)、Server酱、Telegram Bot、自定义 Webhook
- **凭证失效告警**: CI 环境凭证过期时自动发送告警邮件
- **双存储后端**: 本地 JSON 文件 / Git DB 远程存储(适合 GitHub Actions)
- **速率控制**: 内置令牌桶限流器, 防止触发 B 站 WAF

## 快速开始

### 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)(包管理)

### 安装

```bash
git clone https://github.com/HengXin666/HX-Recall.git
cd HX-Recall
uv sync
```

### 配置

1. 复制并编辑配置文件: 

```bash
cp config.yaml config.yaml  # 按实际情况修改
```

2. 填写必要字段(详见 [获取 Cookie 指南](GET_COOKIES_GUIDE.md)): 

```yaml
bilibili_uid: 12345678  # 你的B站UID

bilibili_credential:
  sessdata: "粘贴SESSDATA"
  bili_jct: "粘贴bili_jct"
  dedeuserid: "粘贴DedeUserID"
  refresh_token: "粘贴ac_time_value"  # 可选但强烈推荐, 启用自动续期
```

### 运行

```bash
# 使用默认配置
uv run hx-recall

# 指定配置文件
uv run hx-recall -c /path/to/config.yaml

# 临时覆盖选取策略和数量
uv run hx-recall -s dusty -k 3
```

命令行参数: 

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-c, --config` | 配置文件路径 | `config.yaml` |
| `-k, --top-k` | 覆盖配置中的选取数量 | 配置值 |
| `-s, --strategy` | 覆盖选取策略 (`random`/`latest`/`oldest`/`dusty`) | 配置值 |

## 选取策略

| 策略 | 说明 | 适合场景 |
|------|------|----------|
| `random` | 随机选取 | 发现遗忘内容 |
| `latest` | 按收藏时间倒序 | 关注最近收藏 |
| `oldest` | 按收藏时间正序 | 回顾老收藏 |
| `dusty` | 优先推送最久未见的 | 清理吃灰收藏 |

`dusty` 策略支持冷却期配置: 同一视频两次推送之间至少间隔 `cooldown_days` 天(默认 30 天), 冷却期过后可重复推送。

## 推送渠道

在 `config.yaml` 的 `notify` 下配置, 可同时启用多个: 

| 渠道 | 配置键 | 说明 |
|------|--------|------|
| 邮件 | `notify.email` | 支持 HTML 精美排版, 推荐 |
| Server酱 | `notify.server_chan` | 微信推送 |
| Telegram | `notify.telegram` | Bot 推送 |
| Webhook | `notify.webhook` | 自定义 POST JSON |
| 控制台 | `notify.console` | 调试用 |

## GitHub Actions 部署

### 1. Fork 仓库

### 2. 配置 Secrets

进入 **Settings → Secrets and variables → Actions**, 添加: 

| Name | 说明 | 必需 |
|------|------|------|
| `SESSDATA` | B站登录凭证 | 是 |
| `DEDEUSERID` | B站 UID | 是 |
| `REFRESH_TOKEN` | `ac_time_value`, 启用自动续期 | 推荐 |
| `GIT_DB_REPO_URL` | Git DB 仓库地址, 如 `https://github.com/user/repo.git` | Git DB 模式 |
| `GIT_DB_BRANCH` | Git DB 分支名, 如 `HX-RECALL` | Git DB 模式 |
| `GIT_DB_TOKEN` | 有目标仓库写权限的 PAT (见下方说明) | Git DB 模式 |
| `NOTIFY_EMAIL_SMTP` | SMTP 服务器, 如 `smtp.qq.com` | 邮件推送 |
| `NOTIFY_EMAIL_SENDER` | 发件邮箱 | 邮件推送 |
| `NOTIFY_EMAIL_PASSWORD` | 邮箱授权码 | 邮件推送 |
| `NOTIFY_EMAIL_RECEIVERS` | 收件邮箱, 多个逗号分隔 | 邮件推送 |

**Variables** (非机密, 可随时修改, 位于 Settings → Variables):

| Name | 说明 |
|------|------|
| `FAVORITE_IDS` | 收藏夹 ID, 多个逗号分隔, 留空则全部 |

所有配置均通过 Secrets/Variables 注入, **无需修改仓库中的任何文件**。

### GIT_DB_TOKEN 配置指南

Git DB 需要一个有目标仓库写权限的 **Personal Access Token (PAT)** 来推送缓存数据。

> **为什么不能用 `GITHUB_TOKEN`?** `GITHUB_TOKEN` 只能访问当前仓库(HX-Recall), 无法推送到其他仓库。

#### 第一步: 创建数据仓库

在 GitHub 上新建一个**私有仓库**用于存储缓存数据(如 `my-data`)，不需要添加任何文件。

#### 第二步: 创建 PAT

1. 进入 GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. 点击 **Generate new token**
3. 填写:
   - **Token name**: `HX-Recall Git DB` (随意)
   - **Expiration**: 选择最长有效期
   - **Repository access**: 选择 **Only select repositories** → 选中你刚创建的数据仓库
4. **Permissions → Repository permissions → Contents**: 设为 **Read and write**
5. 点击 **Generate token**, 复制生成的 token

> 也可以使用 **Classic token**, 勾选 `repo` 权限即可, 但 Fine-grained token 更安全(最小权限原则)。

#### 第三步: 配置 Secrets

回到 HX-Recall 仓库 → **Settings → Secrets → Actions**:

| Secret | 值 |
|--------|-----|
| `GIT_DB_REPO_URL` | `https://github.com/<你的用户名>/<数据仓库名>.git` |
| `GIT_DB_BRANCH` | 分支名, 如 `HX-RECALL` |
| `GIT_DB_TOKEN` | 上一步生成的 PAT |

### 3. 手动触发验证

**Actions → Recall Dusty Favorites → Run workflow**

### 凭证过期处理

程序会自动发送告警邮件。按邮件指引更新 Secrets 即可: 

1. 登录 bilibili.com
2. F12 → Console → 粘贴一键脚本(见 [GET_COOKIES_GUIDE.md](GET_COOKIES_GUIDE.md))→ 复制新 SESSDATA
3. Settings → Secrets → 更新 SESSDATA
4. 重新 Run workflow

## 存储模式

### 本地文件模式(默认)

缓存和状态保存在 `config.yaml` 同目录下的 `.video_cache.json` 和 `.recall_state.json`。

### Git DB 模式

```yaml
git_db:
  enabled: true
  repo_url: "https://github.com/user/repo.git"  # Git DB 仓库地址
  branch: "HX-RECALL"                           # 分支名
  token: ""  # 留空则自动读取 GITHUB_TOKEN 环境变量
```

启用后缓存数据存储到指定的 Git 仓库分支, 使用 [HX-Git-DB](https://github.com/HengXin666/HX-Git-DB) only 模式(分支永远只有一个提交), 适合 GitHub Actions 场景。

> **GitHub Actions 部署时**, `repo_url` 和 `branch` 优先从 Secrets `GIT_DB_REPO_URL` / `GIT_DB_BRANCH` 注入, 无需在 `config.yaml` 中明文填写。

## 项目结构

```
hx_recall/
├── __init__.py           # 包入口
├── __main__.py           # python -m 入口
├── cli.py                # 命令行参数解析
├── config.py             # 通用配置加载(YAML → dataclass)
├── selector.py           # 视频选取策略(通用)
├── formatter.py          # 消息格式化(纯文本 + HTML)
├── notifier.py           # 多渠道推送通知(通用)
├── rate_limiter.py       # 令牌桶速率限制器(通用)
├── state.py              # 推送状态 + 爬取进度持久化
├── video_cache.py        # 视频元数据缓存
└── bilibili/             # B站平台实现
    ├── __init__.py       # 子包入口
    ├── config.py         # B站配置 dataclass(凭证、吃灰策略)
    ├── core.py           # B站核心运行逻辑
    ├── fetcher.py        # B站 API 数据获取
    ├── browser_login.py  # 浏览器登录回退(rookiepy)
    └── sessdata_keeper.py # SESSDATA 自动续期
```

## 依赖

- [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) — B站 API 封装
- [hx-git-db](https://github.com/HengXin666/HX-Git-DB) — Git 远程数据库
- httpx — 异步 HTTP 客户端
- pycryptodome — RSA-OAEP 加密(SESSDATA 续期用)
- pyyaml — 配置文件解析

可选依赖(浏览器登录): 

- rookiepy — 从浏览器提取 Cookie(`uv add 'hx-recall[browser]'`)

## 安全提醒

- Cookie 等同于账号密码, **绝不分享或上传到公开仓库**
- `config.yaml` 已在 `.gitignore` 中, 不会被 git 跟踪
- GitHub Secrets 是加密存储的, 只有 Actions 可以读取
- 配置 `refresh_token` 后 SESSDATA 会自动续期, 减少手动操作

## License

MIT
