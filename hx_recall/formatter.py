"""消息格式化模块"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

STRATEGY_LABELS = {
    "random": "随机回顾",
    "latest": "最近收藏",
    "oldest": "往期回顾",
    "dusty": "吃灰清灰",
}


def _format_count(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _format_duration(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_timestamp(ts: int) -> str:
    if ts == 0:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---- 结构化数据（供HTML渲染用） ----


@dataclass
class CommentData:
    name: str = ""
    content: str = ""
    like: int = 0
    level: int = 0


@dataclass
class VideoData:
    title: str = ""
    fav_name: str = ""
    owner_name: str = ""
    duration: int = 0
    pubdate: int = 0
    view: int = 0
    like: int = 0
    coin: int = 0
    favorite: int = 0
    danmaku: int = 0
    link: str = ""
    cover: str = ""
    desc: str = ""
    ai_conclusion: str = ""
    comment_summary: str = ""
    hot_comments: list[CommentData] = field(default_factory=list)

    @property
    def duration_str(self) -> str:
        return _format_duration(self.duration)

    @property
    def pubdate_str(self) -> str:
        return _format_timestamp(self.pubdate)

    @property
    def view_str(self) -> str:
        return _format_count(self.view)

    @property
    def like_str(self) -> str:
        return _format_count(self.like)

    @property
    def coin_str(self) -> str:
        return _format_count(self.coin)

    @property
    def favorite_str(self) -> str:
        return _format_count(self.favorite)

    @property
    def danmaku_str(self) -> str:
        return _format_count(self.danmaku)


def _to_video_data(v: dict) -> VideoData:
    hot = []
    for c in v.get("hot_comments", []):
        hot.append(CommentData(
            name=c.get("name", ""),
            content=c.get("content", ""),
            like=c.get("like", 0),
            level=c.get("level", 0),
        ))
    return VideoData(
        title=v.get("title", ""),
        fav_name=v.get("_fav_name", ""),
        owner_name=v.get("owner_name", ""),
        duration=v.get("duration", 0),
        pubdate=v.get("pubdate", 0),
        view=v.get("view", 0),
        like=v.get("like", 0),
        coin=v.get("coin", 0),
        favorite=v.get("favorite", 0),
        danmaku=v.get("danmaku", 0),
        link=v.get("link", ""),
        cover=v.get("cover", ""),
        desc=v.get("desc", ""),
        ai_conclusion=v.get("ai_conclusion", "").strip(),
        comment_summary=v.get("comment_summary", "").strip(),
        hot_comments=hot,
    )


def format_message(videos: list[dict], strategy: str = "random") -> str:
    """将视频列表格式化为纯文本推送消息"""
    label = STRATEGY_LABELS.get(strategy, "回顾")
    lines = [f"📚 B站收藏夹{label}", f"共 {len(videos)} 个视频\n"]

    for i, v in enumerate(videos, 1):
        vd = _to_video_data(v)
        lines.append(f"{'─' * 30}")
        lines.append(f"{i}. {vd.title}")
        lines.append(f"   📁 收藏夹: {vd.fav_name}")
        lines.append(f"   👤 UP主: {vd.owner_name}")
        lines.append(f"   ⏱ 时长: {vd.duration_str}")
        lines.append(f"   📅 发布: {vd.pubdate_str}")
        lines.append(
            f"   👀 {vd.view_str}  "
            f"👍 {vd.like_str}  "
            f"🪙 {vd.coin_str}  "
            f"⭐ {vd.favorite_str}  "
            f"💬 {vd.danmaku_str}"
        )

        if vd.ai_conclusion:
            lines.append(f"   🤖 AI视频总结:")
            for line in vd.ai_conclusion.split("\n"):
                lines.append(f"      {line}")

        if vd.comment_summary:
            lines.append(f"   💭 AI评论总结:")
            for line in vd.comment_summary.split("\n"):
                lines.append(f"      {line}")

        if vd.hot_comments:
            lines.append(f"   🔥 热门评论:")
            for c in vd.hot_comments[:3]:
                lines.append(f"      【{c.name}】{c.content} 👍{c.like}")

        desc = vd.desc.strip()
        if desc:
            short_desc = desc[:100] + "..." if len(desc) > 100 else desc
            lines.append(f"   📝 {short_desc}")

        lines.append(f"   🔗 {vd.link}")

    lines.append(f"{'─' * 30}")
    lines.append(f"\n🔄 策略: {label} | 下次继续发现好内容~")
    return "\n".join(lines)


def format_video_data_list(videos: list[dict], strategy: str = "random") -> tuple[str, list[VideoData]]:
    """格式化为纯文本 + 结构化数据（供HTML渲染）

    Returns:
        (纯文本消息, VideoData列表)
    """
    return format_message(videos, strategy), [_to_video_data(v) for v in videos]
