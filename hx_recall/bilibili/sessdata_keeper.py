"""B站SESSDATA自动续期模块

基于B站官方Cookie刷新机制，实现SESSDATA的自动续期，避免手动更新。
核心流程:
  1. 检查是否需要刷新 (/x/passport-login/web/cookie/info)
  2. RSA-OAEP加密生成CorrespondPath
  3. 获取refresh_csrf (/correspond/1/{path})
  4. 执行刷新获取新Cookie (/x/passport-login/web/cookie/refresh)
  5. 确认旧token失效 (/x/passport-login/web/confirm/refresh)

依赖: pycryptodome, httpx

使用方式:
    from hx_recall.bilibili.sessdata_keeper import SessdataKeeper

    keeper = SessdataKeeper(
        sessdata="...",
        bili_jct="...",
        refresh_token="...",  # 从浏览器localStorage ac_time_value获取
        config_path="config.yaml",  # 可选，自动更新config文件
    )
    result = await keeper.refresh_if_needed()
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("hx_recall.sessdata_keeper")

# B站RSA公钥 (PEM格式)
BILI_RSA_PUBLIC_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0EgUc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""


@dataclass
class RefreshResult:
    """续期结果"""
    success: bool
    refreshed: bool  # 是否实际执行了刷新（False表示无需刷新）
    new_sessdata: str = ""
    new_bili_jct: str = ""
    new_refresh_token: str = ""
    message: str = ""


def _rsa_oaep_encrypt(plaintext: bytes) -> bytes:
    """使用B站公钥进行RSA-OAEP加密 (SHA-256)"""
    try:
        from Crypto.Cipher import PKCS1_OAEP
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
    except ImportError:
        raise ImportError(
            "需要安装 pycryptodome: uv add pycryptodome"
        )

    key = RSA.import_key(BILI_RSA_PUBLIC_KEY_PEM)
    cipher = PKCS1_OAEP.new(key, SHA256)
    return cipher.encrypt(plaintext)


def generate_correspond_path(timestamp_ms: int | None = None) -> str:
    """生成CorrespondPath: 对 'refresh_{timestamp}' 进行RSA-OAEP加密后转hex小写

    Args:
        timestamp_ms: 毫秒时间戳，默认当前时间
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    message = f"refresh_{timestamp_ms}".encode()
    encrypted = _rsa_oaep_encrypt(message)
    return binascii.b2a_hex(encrypted).decode()


class SessdataKeeper:
    """SESSDATA自动续期管理器

    Args:
        sessdata: 当前SESSDATA值
        bili_jct: 当前bili_jct值
        refresh_token: 刷新令牌（从浏览器localStorage ac_time_value获取）
        config_path: 可选，续期成功后自动更新此config文件的sessdata
        dedeuserid: DedeUserID值（可选）
    """

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        refresh_token: str,
        config_path: str | Path | None = None,
        dedeuserid: str = "",
    ):
        self._sessdata = sessdata
        self._bili_jct = bili_jct
        self._refresh_token = refresh_token
        self._config_path = Path(config_path) if config_path else None
        self._dedeuserid = dedeuserid

        # 更新后的值（refresh后填充）
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.refresh_token = refresh_token

    @property
    def cookies(self) -> dict[str, str]:
        """构造cookies字典"""
        c = {}
        if self._sessdata:
            c["SESSDATA"] = self._sessdata
        if self._bili_jct:
            c["bili_jct"] = self._bili_jct
        if self._dedeuserid:
            c["DedeUserID"] = self._dedeuserid
        return c

    @property
    def has_refresh_token(self) -> bool:
        """是否有有效的refresh_token"""
        return bool(self._refresh_token and len(self._refresh_token) > 10)

    async def _check_need_refresh(self, client: httpx.AsyncClient) -> tuple[bool, int]:
        """检查是否需要刷新Cookie

        Returns:
            (需要刷新, timestamp)
        """
        url = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
        resp = await client.get(url, cookies=self.cookies, timeout=10.0)

        if resp.status_code != 200 or not resp.text.strip():
            logger.warning(f"检查刷新状态HTTP失败: status={resp.status_code}")
            return False, 0

        data = resp.json()

        if data.get("code") != 0:
            logger.warning(f"检查刷新状态失败: code={data.get('code')} msg={data.get('message')}")
            # 如果是未登录错误，说明cookie已过期，尝试强制刷新
            return True, int(time.time() * 1000)

        info = data.get("data", {})
        need_refresh = info.get("refresh", False)
        timestamp = info.get("timestamp", int(time.time() * 1000))

        logger.debug(f"刷新检查: need_refresh={need_refresh}, timestamp={timestamp}")
        return need_refresh, timestamp

    async def _get_refresh_csrf(
        self, client: httpx.AsyncClient, correspond_path: str
    ) -> str:
        """通过加密路径获取refresh_csrf

        从HTML响应中提取 <div id="1-name">...</div> 的内容
        """
        url = f"https://www.bilibili.com/correspond/1/{correspond_path}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }
        resp = await client.get(url, headers=headers, timeout=10.0)

        # 从HTML中提取refresh_csrf
        match = re.search(r'id="1-name"[^>]*>([^<]+)<', resp.text)
        if match:
            csrf = match.group(1).strip()
            logger.debug(f"获取到refresh_csrf: {csrf[:8]}...")
            return csrf

        raise RuntimeError(f"未能从correspond页面提取refresh_csrf, 响应长度={len(resp.text)}")

    async def _do_refresh(
        self, client: httpx.AsyncClient, refresh_csrf: str
    ) -> tuple[dict[str, str], str]:
        """执行Cookie刷新

        Returns:
            (新cookies字典, 新refresh_token)
        """
        url = "https://passport.bilibili.com/x/passport-login/web/cookie/refresh"
        payload = {
            "csrf": self._bili_jct,
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": self._refresh_token,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }

        resp = await client.post(url, data=payload, cookies=self.cookies, headers=headers, timeout=15.0)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"刷新Cookie失败: code={data.get('code')} msg={data.get('message')}"
            )

        # 从Set-Cookie头提取新cookie
        new_cookies = self._extract_set_cookies(resp.headers.get_list("set-cookie"))
        new_token = data.get("data", {}).get("refresh_token", "")

        if not new_cookies.get("SESSDATA"):
            raise RuntimeError("刷新成功但未返回新SESSDATA")

        return new_cookies, new_token

    async def _confirm_refresh(
        self, client: httpx.AsyncClient, old_refresh_token: str, new_cookies: dict[str, str]
    ) -> bool:
        """确认刷新，使旧token失效"""
        url = "https://passport.bilibili.com/x/passport-login/web/confirm/refresh"
        new_bili_jct = new_cookies.get("bili_jct", self._bili_jct)
        payload = {
            "csrf": new_bili_jct,
            "refresh_token": old_refresh_token,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.bilibili.com/",
        }

        resp = await client.post(
            url, data=payload, cookies=new_cookies, headers=headers, timeout=10.0
        )
        data = resp.json()

        success = data.get("code") == 0
        if not success:
            logger.warning(f"确认刷新失败: code={data.get('code')}, 但新cookie可能仍然有效")

        return success

    @staticmethod
    def _extract_set_cookies(set_cookie_headers: list[str]) -> dict[str, str]:
        """从Set-Cookie头解析cookie字典"""
        cookies = {}
        for header in set_cookie_headers:
            # 提取name=value部分（到第一个分号之前）
            match = re.match(r"([^=]+)=([^;]+)", header)
            if match:
                name = match.group(1).strip()
                value = match.group(2).strip()
                cookies[name] = value
        return cookies

    def _update_config(self, new_sessdata: str, new_bili_jct: str, new_refresh_token: str):
        """更新config.yaml中的凭证"""
        if not self._config_path or not self._config_path.exists():
            return

        import yaml

        with open(self._config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 用正则替换各字段（保留格式）
        def _replace_field(text: str, field_name: str, new_value: str) -> str:
            pattern = rf'(^\s+{field_name}:\s*)"([^"]*)"'
            replacement = f'\\1"{new_value}"'
            return re.sub(pattern, replacement, text, flags=re.MULTILINE)

        content = _replace_field(content, "sessdata", new_sessdata)
        content = _replace_field(content, "bili_jct", new_bili_jct)
        # refresh_token可能不存在于config中
        if "refresh_token:" in content:
            content = _replace_field(content, "refresh_token", new_refresh_token)

        with open(self._config_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"已更新配置文件: {self._config_path}")

    async def refresh_if_needed(self, force: bool = False) -> RefreshResult:
        """执行续期流程（仅在需要时）

        Args:
            force: 强制刷新，跳过need_refresh检查

        Returns:
            RefreshResult 包含结果和新的cookie信息
        """
        if not self.has_refresh_token:
            return RefreshResult(
                success=False,
                refreshed=False,
                message="缺少refresh_token，无法自动续期。请从浏览器localStorage获取ac_time_value。",
            )

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                # Step 1: 检查是否需要刷新
                if not force:
                    need_refresh, timestamp = await self._check_need_refresh(client)
                    if not need_refresh:
                        return RefreshResult(
                            success=True, refreshed=False, message="Cookie仍有效，无需刷新"
                        )
                    logger.info("检测到Cookie需要刷新")
                else:
                    timestamp = int(time.time() * 1000)
                    logger.info("强制刷新模式")

                # Step 2: 生成CorrespondPath
                correspond_path = generate_correspond_path(timestamp)
                logger.debug(f"生成CorrespondPath: {correspond_path[:32]}...")

                # Step 3: 获取refresh_csrf
                refresh_csrf = await self._get_refresh_csrf(client, correspond_path)

                # Step 4: 执行刷新（保存旧token用于后续确认）
                old_token = self._refresh_token
                new_cookies, new_token = await self._do_refresh(client, refresh_csrf)

                # Step 5: 确认旧token失效
                await self._confirm_refresh(client, old_token, new_cookies)

                # 更新内部状态
                self._sessdata = new_cookies.get("SESSDATA", self._sessdata)
                self._bili_jct = new_cookies.get("bili_jct", self._bili_jct)
                self._refresh_token = new_token or self._refresh_token
                self.sessdata = self._sessdata
                self.bili_jct = self._bili_jct
                self.refresh_token = self._refresh_token

                # 自动更新config文件
                self._update_config(self._sessdata, self._bili_jct, self._refresh_token)

                return RefreshResult(
                    success=True,
                    refreshed=True,
                    new_sessdata=self._sessdata,
                    new_bili_jct=self._bili_jct,
                    new_refresh_token=self._refresh_token,
                    message=f"Cookie刷新成功! 新SESSDATA前缀: {self._sessdata[:16]}...",
                )

        except ImportError as e:
            return RefreshResult(success=False, refreshed=False, message=str(e))
        except Exception as e:
            logger.error(f"Cookie续期异常: {e}", exc_info=True)
            return RefreshResult(success=False, refreshed=False, message=f"续期异常: {e}")


async def check_and_refresh(config_path: str | Path | None = None) -> RefreshResult:
    """便捷函数：从配置加载并执行续期检查

    Args:
        config_path: 配置文件路径，默认项目根目录下 config.yaml
    """
    if config_path is None:
        # 尝试从常见位置查找
        for candidate in [Path("config.yaml"), Path("../config.yaml"), Path("../../config.yaml")]:
            if candidate.exists():
                config_path = candidate
                break
        else:
            return RefreshResult(
                success=False, refreshed=False, message="找不到config.yaml且未指定路径"
            )

    config_path = Path(config_path)

    # 加载配置
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cred = cfg.get("bilibili_credential", {})
    keeper = SessdataKeeper(
        sessdata=cred.get("sessdata", ""),
        bili_jct=cred.get("bili_jct", ""),
        refresh_token=cred.get("refresh_token", ""),
        dedeuserid=cred.get("dedeuserid", ""),
        config_path=config_path,
    )
    return await keeper.refresh_if_needed()


async def verify_login(sessdata: str, dedeuserid: str = "") -> dict:
    """验证当前SESSDATA是否有效

    Returns:
        包含 isLogin, uname, mid, expires 等字段的字典
    """
    cookies = {"SESSDATA": sessdata}
    if dedeuserid:
        cookies["DedeUserID"] = dedeuserid

    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookies,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            timeout=15.0,
        )
        if not resp.text or not resp.text.strip():
            return {}
        try:
            return resp.json().get("data", {})
        except Exception:
            logger.warning(f"verify_login返回非JSON (status={resp.status_code}, len={len(resp.text)})")
            return {}
