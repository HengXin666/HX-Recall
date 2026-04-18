"""配置加载模块

通用配置 + 各平台配置的组合。
B站相关配置从 hx_recall.bilibili.config 导入。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hx_recall.bilibili.config import BilibiliCredentialConfig, DustConfig


@dataclass
class ServerChanConfig:
    enabled: bool = False
    sendkey: str = ""


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    headers: dict = field(default_factory=dict)


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    use_ssl: bool = True
    sender: str = ""
    password: str = ""
    receivers: list[str] = field(default_factory=list)


@dataclass
class ConsoleConfig:
    enabled: bool = True


@dataclass
class NotifyConfig:
    server_chan: ServerChanConfig = field(default_factory=ServerChanConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    console: ConsoleConfig = field(default_factory=ConsoleConfig)


@dataclass
class ScheduleConfig:
    """定时调度配置"""
    # Cron表达式（用于GitHub Actions / 自动化）
    cron: str = "0 10 * * *"  # 每天 UTC 10:00 (北京 18:00)
    enabled: bool = True


@dataclass
class GitDBConfig:
    """Git DB 远程存储配置

    将缓存数据持久化到远程 Git 仓库（使用 HX-Git-DB only 模式）。
    若 enabled=False，则使用本地 JSON 文件（原始行为）。
    """
    enabled: bool = False
    token: str = ""  # GitHub token，空则自动读取 GITHUB_TOKEN 环境变量


@dataclass
class AppConfig:
    bilibili_uid: int = 0
    bilibili_credential: BilibiliCredentialConfig = field(
        default_factory=BilibiliCredentialConfig
    )
    top_k: int = 5
    strategy: str = "random"
    favorite_ids: list[int] = field(default_factory=list)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    dust: DustConfig = field(default_factory=DustConfig)
    git_db: GitDBConfig = field(default_factory=GitDBConfig)


def load_config(path: str = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n请复制 config.example.yaml 为 config.yaml 并填写配置"
        )

    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    notify_raw = raw.get("notify", {})
    sc = notify_raw.get("server_chan", {})
    tg = notify_raw.get("telegram", {})
    wh = notify_raw.get("webhook", {})
    em = notify_raw.get("email", {})
    co = notify_raw.get("console", {})

    cfg = AppConfig(
        bilibili_uid=raw.get("bilibili_uid", 0),
        bilibili_credential=BilibiliCredentialConfig(
            sessdata=raw.get("bilibili_credential", {}).get("sessdata", ""),
            bili_jct=raw.get("bilibili_credential", {}).get("bili_jct", ""),
            dedeuserid=raw.get("bilibili_credential", {}).get("dedeuserid", ""),
            dedeuserid_ckmd5=raw.get("bilibili_credential", {}).get(
                "dedeuserid_ckmd5", ""
            ),
            expires_at=raw.get("bilibili_credential", {}).get("expires_at", ""),
            refresh_token=raw.get("bilibili_credential", {}).get(
                "refresh_token", ""
            ),
        ),
        top_k=raw.get("top_k", 5),
        strategy=raw.get("strategy", "random"),
        favorite_ids=raw.get("favorite_ids", []),
        notify=NotifyConfig(
            server_chan=ServerChanConfig(
                enabled=sc.get("enabled", False),
                sendkey=sc.get("sendkey", ""),
            ),
            telegram=TelegramConfig(
                enabled=tg.get("enabled", False),
                bot_token=tg.get("bot_token", ""),
                chat_id=str(tg.get("chat_id", "")),
            ),
            webhook=WebhookConfig(
                enabled=wh.get("enabled", False),
                url=wh.get("url", ""),
                headers=wh.get("headers", {}),
            ),
            email=EmailConfig(
                enabled=em.get("enabled", False),
                smtp_host=em.get("smtp_host", ""),
                smtp_port=em.get("smtp_port", 465),
                use_ssl=em.get("use_ssl", True),
                sender=em.get("sender", ""),
                password=em.get("password", ""),
                receivers=em.get("receivers", []),
            ),
            console=ConsoleConfig(
                enabled=co.get("enabled", True),
            ),
        ),
        schedule=ScheduleConfig(
            cron=raw.get("schedule", {}).get("cron", "0 10 * * *"),
            enabled=raw.get("schedule", {}).get("enabled", True),
        ),
        dust=DustConfig(
            cooldown_days=raw.get("dust", {}).get("cooldown_days", 30),
            allow_repush=raw.get("dust", {}).get("allow_repush", True),
        ),
        git_db=GitDBConfig(
            enabled=raw.get("git_db", {}).get("enabled", False),
            token=raw.get("git_db", {}).get("token", ""),
        ),
    )

    if cfg.bilibili_uid == 0:
        raise ValueError("请在 config.yaml 中设置 bilibili_uid")

    return cfg
