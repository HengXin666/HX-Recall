"""B站相关配置数据类"""

from dataclasses import dataclass, field


@dataclass
class BiliTokenConfig:
    """OAuth2 Token 管理配置（BiliTokenManager 参数）"""

    repo: str = "https://github.com/HengXin666/__HX-Data__.git"
    branch: str = "bilibili-token"
    auto_refresh_days: int = 7
    token: str = ""  # GitHub PAT，空则自动读取 GITHUB_TOKEN 环境变量


@dataclass
class DustConfig:
    """吃灰清灰策略配置"""

    cooldown_days: int = 30
    allow_repush: bool = True
