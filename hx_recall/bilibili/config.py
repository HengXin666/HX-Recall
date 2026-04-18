"""B站相关配置数据类"""

from dataclasses import dataclass, field


@dataclass
class BilibiliCredentialConfig:
    """B站登录凭证配置"""

    sessdata: str = ""
    bili_jct: str = ""
    dedeuserid: str = ""
    dedeuserid_ckmd5: str = ""
    expires_at: str = ""
    refresh_token: str = ""


@dataclass
class DustConfig:
    """吃灰清灰策略配置"""

    # 冷却期(天)：同一视频两次推送之间至少间隔这么多天
    cooldown_days: int = 30
    # 是否允许重复推送已推送过的视频（冷却期过后）
    allow_repush: bool = True
