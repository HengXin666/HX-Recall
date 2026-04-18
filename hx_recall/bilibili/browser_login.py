"""浏览器登录模块：凭证失效时打开系统浏览器让用户登录，然后自动提取Cookie回写配置"""

from __future__ import annotations

import logging
import re
import webbrowser
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("hx_recall.browser_login")

# 需要从浏览器提取的B站Cookie字段
BILI_COOKIE_NAMES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")

# 按优先级尝试的浏览器列表
BROWSER_ORDER = ("chrome", "edge", "firefox", "brave", "vivaldi", "chromium", "opera", "arc")


@dataclass
class BrowserCredential:
    """从浏览器提取的B站凭证"""
    sessdata: str = ""
    bili_jct: str = ""
    dedeuserid: str = ""
    dedeuserid_ckmd5: str = ""
    refresh_token: str = ""  # localStorage中的ac_time_value，无法从cookie直接获取

    @property
    def is_valid(self) -> bool:
        return bool(self.sessdata)


def extract_bilibili_cookies_from_browser(browser: str = "") -> BrowserCredential:
    """从系统浏览器中提取bilibili.com的Cookie

    Args:
        browser: 指定浏览器名称(chrome/edge/firefox等)，为空则按优先级自动尝试

    Returns:
        BrowserCredential 包含提取到的凭证

    Raises:
        ImportError: rookiepy未安装
    """
    try:
        import rookiepy
    except ImportError:
        raise ImportError(
            "浏览器Cookie提取需要 rookiepy 库。\n"
            "请运行: uv add rookiepy\n"
            "或: uv add 'hx-recall[browser]'"
        )

    cred = CredentialBuilder()

    browsers_to_try = [browser] if browser else BROWSER_ORDER

    for br in browsers_to_try:
        loader = getattr(rookiepy, br, None)
        if loader is None:
            continue

        try:
            cookies = loader(domains=["bilibili.com"])
            if cookies:
                cred.feed(cookies)
                if cred.cred.is_valid:
                    logger.info(f"从 {br} 浏览器成功提取B站Cookie")
                    return cred.cred
        except Exception as e:
            logger.debug(f"从 {br} 浏览器提取Cookie失败: {e}")
            continue

    return cred.cred


class CredentialBuilder:
    """从rookiepy返回的cookie列表构建Credential"""

    def __init__(self):
        self.cred = BrowserCredential()

    def feed(self, cookies: list[dict]) -> None:
        """将cookie列表中的B站凭证填充到cred"""
        mapping = {
            "SESSDATA": "sessdata",
            "bili_jct": "bili_jct",
            "DedeUserID": "dedeuserid",
            "DedeUserID__ckMd5": "dedeuserid_ckmd5",
        }
        for c in cookies:
            name = c.get("name", "")
            if name in mapping:
                setattr(self.cred, mapping[name], c.get("value", ""))


async def verify_credential(sessdata: str, dedeuserid: str = "") -> dict:
    """验证SESSDATA是否仍然有效

    Returns:
        nav API的data字段，isLogin=True表示有效
    """
    from hx_recall.bilibili.sessdata_keeper import verify_login

    return await verify_login(sessdata, dedeuserid)


def open_login_page() -> None:
    """打开系统浏览器到B站登录页面"""
    login_url = "https://passport.bilibili.com/login"
    logger.info("正在打开系统浏览器，请在浏览器中登录B站...")
    webbrowser.open(login_url)


def update_config_with_credential(
    config_path: str | Path,
    cred: BrowserCredential,
) -> None:
    """将浏览器提取的凭证回写到config.yaml

    Args:
        config_path: 配置文件路径
        cred: 浏览器提取的凭证
    """
    import yaml

    config_path = Path(config_path)
    if not config_path.exists():
        return

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    def _replace_field(text: str, field_name: str, new_value: str) -> str:
        pattern = rf'(^\s+{field_name}:\s*)"([^"]*)"'
        replacement = f'\\1"{new_value}"'
        return re.sub(pattern, replacement, text, flags=re.MULTILINE)

    if cred.sessdata:
        content = _replace_field(content, "sessdata", cred.sessdata)
    if cred.bili_jct:
        content = _replace_field(content, "bili_jct", cred.bili_jct)
    if cred.dedeuserid:
        content = _replace_field(content, "dedeuserid", cred.dedeuserid)
    if cred.dedeuserid_ckmd5:
        content = _replace_field(content, "dedeuserid_ckmd5", cred.dedeuserid_ckmd5)

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"已将浏览器凭证回写到 {config_path}")


async def browser_login_fallback(config_path: str | Path = "config.yaml") -> BrowserCredential | None:
    """凭证失效时的浏览器登录回退方案

    流程:
    1. 先尝试从已登录的浏览器中直接提取Cookie（需要rookiepy）
    2. 如果提取不到有效Cookie，打开浏览器登录页面，等待用户登录后重试提取
    3. 提取成功后验证凭证有效性
    4. 回写config.yaml

    如果rookiepy未安装，则降级为仅打开浏览器+提示手动更新Cookie的模式。

    Args:
        config_path: 配置文件路径

    Returns:
        BrowserCredential 如果成功提取并验证，否则None
    """
    has_rookiepy = _check_rookiepy_available()

    if has_rookiepy:
        # Step 1: 先尝试直接从浏览器提取（可能用户已经登录了）
        print("[BrowserLogin] 正在尝试从系统浏览器提取B站Cookie...")
        try:
            cred = extract_bilibili_cookies_from_browser()
        except ImportError:
            has_rookiepy = False
        else:
            if cred.is_valid:
                # 验证提取到的Cookie是否有效
                data = await verify_credential(cred.sessdata, cred.dedeuserid)
                if data.get("isLogin"):
                    print(f"[BrowserLogin] 从浏览器提取的Cookie有效! 用户: {data.get('uname', 'unknown')}")
                    update_config_with_credential(config_path, cred)
                    return cred
                else:
                    print("[BrowserLogin] 浏览器中的Cookie已失效，需要重新登录")

    # Step 2: 打开浏览器让用户登录
    print("[BrowserLogin] 即将打开浏览器，请在浏览器中登录B站账号...")
    if has_rookiepy:
        print("[BrowserLogin] 登录成功后，请回到此终端按回车键继续...")
    else:
        print("[BrowserLogin] 登录成功后，请手动将Cookie更新到 config.yaml")
        print("[BrowserLogin] 需要的字段: SESSDATA, bili_jct, DedeUserID")
        print("[BrowserLogin] 提示: 安装 rookiepy (uv add rookiepy) 可实现自动提取Cookie")
    open_login_page()

    if not has_rookiepy:
        # 没有rookiepy，无法自动提取，等用户手动更新后重试
        input("\n[BrowserLogin] 手动更新Cookie后，按回车键继续 >>> ")
        # 重新加载配置，检查用户是否已手动更新
        from hx_recall.config import load_config

        cfg = load_config(str(config_path))
        cred_cfg = cfg.bilibili_credential
        if cred_cfg.sessdata:
            data = await verify_credential(cred_cfg.sessdata, cred_cfg.dedeuserid)
            if data.get("isLogin"):
                print(f"[BrowserLogin] Cookie验证成功! 用户: {data.get('uname', 'unknown')}")
                return BrowserCredential(
                    sessdata=cred_cfg.sessdata,
                    bili_jct=cred_cfg.bili_jct,
                    dedeuserid=cred_cfg.dedeuserid,
                    dedeuserid_ckmd5=cred_cfg.dedeuserid_ckmd5,
                )
        print("[BrowserLogin] 未能验证到有效Cookie，请检查配置")
        return None

    # 等待用户按回车
    input("\n[BrowserLogin] 按回车键继续（确认已在浏览器中登录B站）>>> ")

    # Step 3: 登录后重新从浏览器提取Cookie
    print("[BrowserLogin] 正在从浏览器提取新的Cookie...")
    try:
        cred = extract_bilibili_cookies_from_browser()
    except ImportError:
        print("[BrowserLogin] rookiepy不可用，无法自动提取")
        return None

    if not cred.is_valid:
        print("[BrowserLogin] 未能从浏览器提取到有效的B站Cookie")
        print("[BrowserLogin] 可能原因: 浏览器未登录 / 浏览器加密限制(Chrome需管理员权限)")
        return None

    # Step 4: 验证新Cookie
    data = await verify_credential(cred.sessdata, cred.dedeuserid)
    if not data.get("isLogin"):
        print("[BrowserLogin] 提取的Cookie验证失败，可能还未完全登录")
        return None

    print(f"[BrowserLogin] Cookie验证成功! 用户: {data.get('uname', 'unknown')}")

    # Step 5: 回写配置
    update_config_with_credential(config_path, cred)
    return cred


def _check_rookiepy_available() -> bool:
    """检查rookiepy是否可用"""
    try:
        import rookiepy  # noqa: F401
        return True
    except ImportError:
        return False
