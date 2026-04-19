"""推送通知模块"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from hx_recall.config import AppConfig
from hx_recall.formatter import CommentData, VideoData, _format_count

from datetime import datetime, timezone


def _today_str() -> str:
    """当前日期字符串 (YYYY-MM-DD)"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def notify(message: str, cfg: AppConfig, videos_data: list[VideoData] | None = None) -> None:
    """根据配置推送到所有启用的渠道"""
    results: list[str] = []

    if cfg.notify.console.enabled:
        _notify_console(message)
        results.append("console")

    if cfg.notify.email.enabled:
        _notify_email(message, cfg.notify.email, videos_data)
        results.append("email")

    if cfg.notify.server_chan.enabled:
        await _notify_server_chan(message, cfg.notify.server_chan)
        results.append("server_chan")

    if cfg.notify.telegram.enabled:
        await _notify_telegram(message, cfg.notify.telegram)
        results.append("telegram")

    if cfg.notify.webhook.enabled:
        await _notify_webhook(message, cfg.notify.webhook)
        results.append("webhook")

    if cfg.houtiku.enabled:
        _notify_houtiku(message, cfg)
        results.append("houtiku")

    if not results:
        print("⚠️ 没有启用任何推送渠道")


def _notify_console(message: str) -> None:
    print(message)


# ---- HTML邮件模板 ----

_EMAIL_CSS = """
body {
  margin: 0; padding: 0;
  background: #f4f5f7;
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;
}
.container {
  max-width: 620px; margin: 24px auto; padding: 0 16px;
}
.header {
  background: linear-gradient(135deg, #00a1d6 0%, #fb7299 100%);
  border-radius: 12px 12px 0 0;
  padding: 28px 32px;
  color: #fff;
}
.header h1 {
  margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px;
}
.header p {
  margin: 6px 0 0; font-size: 13px; opacity: 0.85;
}
.video-card {
  background: #fff;
  border-radius: 0;
  padding: 24px 28px;
  border-bottom: 1px solid #f0f0f0;
}
.video-card:last-of-type {
  border-bottom: none;
  border-radius: 0 0 12px 12px;
}
.video-header {
  display: flex; align-items: flex-start; gap: 16px;
  margin-bottom: 14px;
}
.video-cover {
  width: 160px; min-width: 160px; height: 100px;
  border-radius: 8px; object-fit: cover;
  background: #eee;
}
.video-info { flex: 1; }
.video-title {
  font-size: 16px; font-weight: 600; color: #18191c;
  margin: 0 0 6px; line-height: 1.4;
  text-decoration: none;
}
.video-title:hover { color: #00a1d6; }
.video-meta {
  font-size: 12px; color: #9499a0; margin: 0; line-height: 1.7;
}
.video-meta span { margin-right: 12px; }
.stats-row {
  display: flex; gap: 16px; flex-wrap: wrap;
  margin: 10px 0 0; padding: 8px 12px;
  background: #f6f7f8; border-radius: 6px;
}
.stat-item {
  font-size: 12px; color: #61666d;
}
.stat-item .val { font-weight: 600; color: #18191c; margin-left: 2px; }
.section-label {
  font-size: 12px; font-weight: 600; color: #9499a0;
  margin: 14px 0 6px; text-transform: uppercase; letter-spacing: 0.5px;
}
.ai-box {
  background: #f0f7ff; border-left: 3px solid #00a1d6;
  padding: 10px 14px; border-radius: 0 6px 6px 0;
  font-size: 13px; color: #333; line-height: 1.6;
  margin: 4px 0;
}
.comment-box {
  background: #fff8f0; border-left: 3px solid #fb7299;
  padding: 10px 14px; border-radius: 0 6px 6px 0;
  margin: 4px 0;
}
.comment-item {
  font-size: 13px; color: #333; line-height: 1.5;
  margin: 6px 0; padding-bottom: 6px;
  border-bottom: 1px dashed #f0e0d6;
}
.comment-item:last-child { border-bottom: none; padding-bottom: 0; }
.comment-name { font-weight: 600; color: #fb7299; }
.comment-like { font-size: 11px; color: #9499a0; margin-left: 6px; }
.hot-comment-item {
  font-size: 13px; color: #333; line-height: 1.5;
  margin: 6px 0; padding: 8px 10px;
  background: #fff; border-radius: 4px;
  border: 1px solid #f0f0f0;
}
.hot-name { font-weight: 600; color: #ff6633; }
.hot-like {
  font-size: 11px; color: #9499a0; float: right;
}
.footer {
  text-align: center; padding: 20px;
  font-size: 12px; color: #9499a0;
}
.footer a { color: #00a1d6; text-decoration: none; }
"""


def _render_video_card(vd: VideoData, idx: int) -> str:
    """渲染单个视频卡片HTML"""
    cover_html = ""
    if vd.cover:
        cover_url = vd.cover
        if cover_url.startswith("//"):
            cover_url = "https:" + cover_url
        cover_html = f'<img class="video-cover" src="{cover_url}" alt="cover" loading="lazy">'

    # AI视频总结
    ai_html = ""
    if vd.ai_conclusion:
        escaped = vd.ai_conclusion.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        ai_html = f'<div class="section-label">🤖 AI 视频总结</div><div class="ai-box">{escaped}</div>'

    # AI评论总结
    comment_summary_html = ""
    if vd.comment_summary:
        escaped = vd.comment_summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        comment_summary_html = f'<div class="section-label">💭 AI 评论总结</div><div class="comment-box">{escaped}</div>'

    # 热门评论
    hot_comments_html = ""
    if vd.hot_comments:
        items = []
        for c in vd.hot_comments[:3]:
            name_esc = c.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            content_esc = c.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            items.append(
                f'<div class="hot-comment-item">'
                f'<span class="hot-name">{name_esc}</span>'
                f'<span class="hot-like">👍 {_format_count(c.like)}</span><br>'
                f'{content_esc}'
                f'</div>'
            )
        hot_comments_html = (
            f'<div class="section-label">🔥 热门评论</div>'
            + "".join(items)
        )

    # 简介
    desc_html = ""
    if vd.desc.strip():
        short = vd.desc.strip()[:120] + ("..." if len(vd.desc.strip()) > 120 else "")
        desc_esc = short.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        desc_html = f'<div class="section-label">📝 简介</div><div style="font-size:12px;color:#61666d;line-height:1.5">{desc_esc}</div>'

    return f"""
<div class="video-card">
  <div class="video-header">
    {cover_html}
    <div class="video-info">
      <a class="video-title" href="{vd.link}" target="_blank">{vd.title.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")}</a>
      <p class="video-meta">
        <span>👤 {vd.owner_name}</span>
        <span>📁 {vd.fav_name}</span>
      </p>
      <p class="video-meta">
        <span>⏱ {vd.duration_str}</span>
        <span>📅 {vd.pubdate_str}</span>
      </p>
    </div>
  </div>
  <div class="stats-row">
    <span class="stat-item">👀 <span class="val">{vd.view_str}</span></span>
    <span class="stat-item">👍 <span class="val">{vd.like_str}</span></span>
    <span class="stat-item">🪙 <span class="val">{vd.coin_str}</span></span>
    <span class="stat-item">⭐ <span class="val">{vd.favorite_str}</span></span>
    <span class="stat-item">💬 <span class="val">{vd.danmaku_str}</span></span>
  </div>
  {ai_html}
  {comment_summary_html}
  {hot_comments_html}
  {desc_html}
</div>"""


def _render_html_email(videos_data: list[VideoData], strategy: str) -> str:
    """渲染完整HTML邮件"""
    from hx_recall.formatter import STRATEGY_LABELS
    label = STRATEGY_LABELS.get(strategy, "回顾")

    cards = "".join(_render_video_card(vd, i) for i, vd in enumerate(videos_data, 1))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body>
<div class="container">
  <div class="header">
    <h1>📚 B站收藏夹{label}</h1>
    <p>共 {len(videos_data)} 个精选视频，来看看你的好收藏~</p>
  </div>
  {cards}
  <div class="footer">
    🔄 策略: {label} | <a href="https://github.com" target="_blank">HX-Recall</a> 自动推送
  </div>
</div>
<style>{_EMAIL_CSS}</style>
</body></html>"""


def _notify_email(message: str, cfg, videos_data: list[VideoData] | None = None) -> None:
    """通过SMTP发送邮件推送"""
    if not cfg.receivers:
        print("⚠️ 邮件推送未配置收件人")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📚 B站收藏夹回顾 ({_today_str()})"
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.receivers)

    # 纯文本版本
    msg.attach(MIMEText(message, "plain", "utf-8"))

    # HTML版本
    if videos_data:
        html = _render_html_email(videos_data, "latest")
    else:
        html = _message_to_html_simple(message)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if cfg.use_ssl:
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port)
            server.starttls()

        server.login(cfg.sender, cfg.password)
        server.sendmail(cfg.sender, cfg.receivers, msg.as_string())
        server.quit()
        print(f"✅ 邮件推送成功 -> {', '.join(cfg.receivers)}")
    except Exception as e:
        print(f"❌ 邮件推送失败: {e}")


def _message_to_html_simple(message: str) -> str:
    """纯文本→简单HTML（降级用）"""
    lines = message.split("\n")
    html_lines = []
    for line in lines:
        if line.startswith("📚"):
            html_lines.append(f"<h2>{line}</h2>")
        elif line.startswith("─"):
            html_lines.append("<hr>")
        elif line and line[0].isdigit() and "." in line[:4]:
            html_lines.append(f"<h3>{line}</h3>")
        elif line.startswith("   🔗"):
            url = line.replace("   🔗", "").strip()
            html_lines.append(f'<p>🔗 <a href="{url}">{url}</a></p>')
        elif line.strip():
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(f"<p>{escaped}</p>")

    return f"""<html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
{"".join(html_lines)}
</body></html>"""


async def _notify_server_chan(message: str, cfg) -> None:
    url = f"https://sctapi.ftqq.com/{cfg.sendkey}.send"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            data={
                "title": f"📚 B站收藏夹回顾 ({_today_str()})",
                "desp": message,
            },
        )
        resp.raise_for_status()
        print(f"✅ Server酱推送成功")


async def _notify_telegram(message: str, cfg) -> None:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                "chat_id": cfg.chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
        )
        resp.raise_for_status()
        print(f"✅ Telegram推送成功")


async def _notify_webhook(message: str, cfg) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg.url,
            json={"content": message, "title": f"📚 B站收藏夹回顾 ({_today_str()})"},
            headers=cfg.headers or {},
        )
        resp.raise_for_status()
        print(f"✅ Webhook推送成功")


def _notify_houtiku(message: str, cfg: "AppConfig") -> None:
    """通过 HX-HouTiKu SDK 发送端到端加密推送通知"""
    try:
        from hx_houtiku import HxHoutikuClient

        client = HxHoutikuClient(
            endpoint=cfg.houtiku.endpoint,
            api_token=cfg.houtiku.token,
        )
        client.send(
            title=f"📚 B站收藏夹回顾 ({_today_str()})",
            body=message,
            content_type="text",
            priority="default",
            group="hx-recall",
        )
        print("✅ HouTiKu推送成功")
    except Exception as e:
        print(f"❌ HouTiKu推送失败: {e}")


# ---- 凭证失效告警邮件 ----

_CREDENTIAL_ALERT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">
<div style="max-width:560px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;">
  <div style="background:linear-gradient(135deg,#ff4757,#ff6b81);padding:24px 28px;color:#fff;">
    <h1 style="margin:0;font-size:20px;">⚠️ B站凭证已失效</h1>
    <p style="margin:6px 0 0;font-size:13px;opacity:0.9;">HX-Recall 无法继续运行，需要更新登录凭证</p>
  </div>
  <div style="padding:24px 28px;">
    <p style="font-size:14px;color:#333;line-height:1.7;">
      你的B站 <strong>SESSDATA</strong> 已过期或失效，HX-Recall 无法访问收藏夹数据。
    </p>

    <h3 style="font-size:15px;color:#18191c;margin:20px 0 10px;">🔧 本地运行 — 一键修复</h3>
    <ol style="font-size:13px;color:#555;line-height:1.8;padding-left:20px;">
      <li>打开 <a href="https://www.bilibili.com" style="color:#00a1d6;">bilibili.com</a> 并登录</li>
      <li>按 <kbd style="background:#f0f0f0;padding:2px 6px;border-radius:3px;font-size:12px;">F12</kbd> → Application → Cookies</li>
      <li>复制 <code style="background:#f6f7f8;padding:2px 6px;border-radius:3px;font-size:12px;">SESSDATA</code> 的值</li>
      <li>更新 <code style="background:#f6f7f8;padding:2px 6px;border-radius:3px;font-size:12px;">config.yaml</code> 中的 <code>sessdata</code> 字段</li>
    </ol>

    <h3 style="font-size:15px;color:#18191c;margin:20px 0 10px;">🤖 GitHub Actions — 更新 Secrets</h3>
    <ol style="font-size:13px;color:#555;line-height:1.8;padding-left:20px;">
      <li>登录 <a href="https://www.bilibili.com" style="color:#00a1d6;">bilibili.com</a></li>
      <li>按 <kbd style="background:#f0f0f0;padding:2px 6px;border-radius:3px;font-size:12px;">F12</kbd> → Console，输入:
        <pre style="background:#2d2d2d;color:#f8f8f2;padding:12px;border-radius:6px;font-size:12px;overflow-x:auto;margin:8px 0;">document.cookie.match(/SESSDATA=([^;]+)/)?.[1]</pre>
      </li>
      <li>复制输出的值</li>
      <li>前往仓库 <strong>Settings → Secrets and variables → Actions</strong></li>
      <li>更新 <code style="background:#f6f7f8;padding:2px 6px;border-radius:3px;font-size:12px;">SESSDATA</code> 的值</li>
      <li>手动触发 <strong>Run workflow</strong> 验证</li>
    </ol>

    <div style="background:#fff8f0;border-left:3px solid #fb7299;padding:10px 14px;border-radius:0 6px 6px 0;margin:16px 0 0;">
      <p style="margin:0;font-size:12px;color:#61666d;">
        💡 <strong>提示</strong>: 配置 <code>refresh_token</code> (localStorage 中的 <code>ac_time_value</code>) 可实现 SESSDATA 自动续期，无需手动更新。
        详见 <a href="https://github.com/your-repo/HX-Recall#seessdata自动续期" style="color:#00a1d6;">项目文档</a>
      </p>
    </div>
  </div>
  <div style="text-align:center;padding:16px;font-size:11px;color:#9499a0;border-top:1px solid #f0f0f0;">
    HX-Recall 凭证告警 | __TODAY__
  </div>
</div>
</body></html>"""


def send_credential_alert(cfg: AppConfig) -> None:
    """凭证失效时发送告警邮件

    只使用邮件渠道（GitHub Actions中最可靠的通知方式）。
    如果邮件未配置，打印警告到控制台。
    """
    email_cfg = cfg.notify.email
    if not email_cfg.enabled or not email_cfg.receivers:
        print("⚠️ 凭证已失效且未配置邮件告警渠道！请在 config.yaml 中启用邮件推送")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ HX-Recall 凭证失效告警 ({_today_str()})"
    msg["From"] = email_cfg.sender
    msg["To"] = ", ".join(email_cfg.receivers)

    plain_text = (
        "⚠️ B站凭证已失效\n\n"
        "HX-Recall 无法继续运行，需要更新 SESSDATA。\n\n"
        "本地修复:\n"
        "  1. 登录 bilibili.com\n"
        "  2. F12 → Application → Cookies → 复制 SESSDATA\n"
        "  3. 更新 config.yaml\n\n"
        "GitHub Actions:\n"
        "  1. F12 → Console: document.cookie.match(/SESSDATA=([^;]+)/)?.[1]\n"
        "  2. Settings → Secrets → 更新 SESSDATA\n\n"
        "💡 配置 refresh_token 可自动续期，详见项目文档"
    )
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    html = _CREDENTIAL_ALERT_HTML.replace("__TODAY__", _today_str())
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if email_cfg.use_ssl:
            server = smtplib.SMTP_SSL(email_cfg.smtp_host, email_cfg.smtp_port)
        else:
            server = smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port)
            server.starttls()

        server.login(email_cfg.sender, email_cfg.password)
        server.sendmail(email_cfg.sender, email_cfg.receivers, msg.as_string())
        server.quit()
        print(f"✅ 凭证告警邮件已发送 -> {', '.join(email_cfg.receivers)}")
    except Exception as e:
        print(f"❌ 凭证告警邮件发送失败: {e}")
