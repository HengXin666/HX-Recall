"""视频元数据缓存模块

只负责视频元数据的缓存与复用，爬取进度追踪已移至 state.py。

支持两种存储后端：
- 本地文件：save(path) / load(path)
- Git DB (hx_git_db)：save_to_db(db) / load_from_db(db)
  使用 HX-Git-DB only 模式，存储到 __HX-Data__ 仓库 HX-RECALL 分支 bilibili/ 下

数据结构:
{
  "version": 1,
  "videos": {
    "<bvid>": {
      "title": str,
      "fav_time": int,
      "fav_names": [str],
      "fav_ids": [int],
      "cover": str,
      "duration": int,
      "upper_name": str,
      "upper_mid": int,
      // --- 详细信息(仅已获取过) ---
      "aid": int,
      "cid": int,
      "desc": str,
      "owner_name": str,
      "owner_mid": int,
      "view": int,
      "like": int,
      "coin": int,
      "favorite": int,
      "danmaku": int,
      "pubdate": int,
      // --- AI总结(不变数据，缓存复用) ---
      "ai_conclusion": str,
      "comment_summary": str,
      // --- 元信息 ---
      "detail_fetched_at": ISO8601,
      "ai_fetched_at": ISO8601,
    }
  },
  "stats": {
    "last_full_crawl": ISO8601,
    "total_videos": int,
  }
}
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hx_git_db import DataBase

logger = logging.getLogger("hx_recall.video_cache")

CACHE_VERSION = 1

GIT_DB_PATH = "bilibili/video_cache.json"


@dataclass
class VideoCacheEntry:
    """单个视频的缓存条目"""
    # --- 基础信息(收藏夹列表即有) ---
    title: str = ""
    fav_time: int = 0
    fav_names: list[str] = field(default_factory=list)
    fav_ids: list[int] = field(default_factory=list)
    cover: str = ""
    duration: int = 0
    upper_name: str = ""
    upper_mid: int = 0
    # --- 详细信息(需额外API获取) ---
    aid: int = 0
    cid: int = 0
    desc: str = ""
    owner_name: str = ""
    owner_mid: int = 0
    view: int = 0
    like: int = 0
    coin: int = 0
    favorite: int = 0
    danmaku: int = 0
    pubdate: int = 0
    # --- AI总结(不变数据) ---
    ai_conclusion: str = ""
    comment_summary: str = ""
    hot_comments: list[dict] = field(default_factory=list)
    # --- 元信息 ---
    detail_fetched_at: str = ""
    ai_fetched_at: str = ""

    @property
    def has_detail(self) -> bool:
        """是否已获取过详细信息"""
        return bool(self.detail_fetched_at)

    @property
    def has_ai(self) -> bool:
        """是否已获取过AI总结"""
        return bool(self.ai_fetched_at)


class VideoCache:
    """视频元数据缓存管理器

    支持两种存储后端：
    - 本地文件：save(path) / load(path)
    - Git DB (hx_git_db)：save_to_db(db) / load_from_db(db)

    用法(本地):
        cache = VideoCache.load(path)
        cache.update_fav_videos(fav_id, fav_title, new_videos)
        cache.save(path)

    用法(Git DB):
        with make_database(repo, branch, only=True) as db:
            cache = VideoCache.load_from_db(db)
            cache.update_fav_videos(fav_id, fav_title, new_videos)
            cache.save_to_db(db)
        # 退出 with 时自动 commit + push + 清理
    """

    def __init__(self):
        self.videos: dict[str, VideoCacheEntry] = {}
        self.last_full_crawl: str = ""
        self._dirty = False

    # ---- 查询 ----

    def get(self, bvid: str) -> VideoCacheEntry | None:
        """获取视频缓存，不存在返回None"""
        return self.videos.get(bvid)

    def has_video(self, bvid: str) -> bool:
        return bvid in self.videos

    def get_all_cached_videos(self, fav_ids: list[int] | None = None) -> list[VideoCacheEntry]:
        """获取所有缓存视频，可按收藏夹ID过滤"""
        if fav_ids is None:
            return list(self.videos.values())
        return [
            v for v in self.videos.values()
            if any(fid in fav_ids for fid in v.fav_ids)
        ]

    def get_bvids_in_fav(self, fav_id: int) -> set[str]:
        """获取属于指定收藏夹的所有bvid"""
        return {
            bvid for bvid, v in self.videos.items()
            if fav_id in v.fav_ids
        }

    def count_videos_in_fav(self, fav_id: int) -> int:
        """统计属于某收藏夹的视频数"""
        return sum(1 for v in self.videos.values() if fav_id in v.fav_ids)

    # ---- 写入 ----

    def update_fav_videos(
        self,
        fav_id: int,
        fav_title: str,
        videos: list[dict],
    ) -> int:
        """更新收藏夹视频缓存(增量)

        Args:
            fav_id: 收藏夹ID
            fav_title: 收藏夹名称
            videos: 本次爬取到的视频列表

        Returns:
            新增视频数
        """
        new_count = 0
        for v in videos:
            bvid = v.get("bvid", "")
            if not bvid:
                continue

            if bvid in self.videos:
                entry = self.videos[bvid]
                if fav_id not in entry.fav_ids:
                    entry.fav_ids.append(fav_id)
                if fav_title not in entry.fav_names:
                    entry.fav_names.append(fav_title)
                if v.get("title"):
                    entry.title = v["title"]
                if v.get("fav_time"):
                    entry.fav_time = v["fav_time"]
            else:
                self.videos[bvid] = VideoCacheEntry(
                    title=v.get("title", ""),
                    fav_time=v.get("fav_time", 0),
                    fav_names=[fav_title],
                    fav_ids=[fav_id],
                    cover=v.get("cover", ""),
                    duration=v.get("duration", 0),
                    upper_name=v.get("upper_name", ""),
                    upper_mid=v.get("upper_mid", 0),
                )
                new_count += 1

        self._dirty = True
        return new_count

    def update_video_detail(self, bvid: str, detail: dict) -> None:
        """更新视频详细信息缓存"""
        entry = self.videos.get(bvid)
        if entry is None:
            entry = VideoCacheEntry(title=detail.get("title", ""))
            self.videos[bvid] = entry

        for key in ("aid", "cid", "desc", "owner_name", "owner_mid",
                     "view", "like", "coin", "favorite", "danmaku",
                     "pubdate", "duration", "cover", "upper_name", "upper_mid"):
            if key in detail and detail[key]:
                setattr(entry, key, detail[key])
        if "title" in detail and detail["title"]:
            entry.title = detail["title"]

        entry.detail_fetched_at = datetime.now(timezone.utc).isoformat()
        self._dirty = True

    def update_video_ai(self, bvid: str, ai_conclusion: str = "", comment_summary: str = "", hot_comments: list[dict] | None = None) -> None:
        """更新视频AI总结缓存"""
        entry = self.videos.get(bvid)
        if entry is None:
            return

        if ai_conclusion:
            entry.ai_conclusion = ai_conclusion
        if comment_summary:
            entry.comment_summary = comment_summary
        if hot_comments is not None:
            entry.hot_comments = hot_comments
        entry.ai_fetched_at = datetime.now(timezone.utc).isoformat()
        self._dirty = True

    # ---- 持久化 ----

    def _to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "version": CACHE_VERSION,
            "videos": {
                bvid: asdict(ve) for bvid, ve in self.videos.items()
            },
            "stats": {
                "last_full_crawl": self.last_full_crawl,
                "total_videos": len(self.videos),
            },
        }

    @staticmethod
    def _from_dict(raw: dict) -> VideoCache:
        """从字典反序列化"""
        cache = VideoCache()
        for bvid, ve_raw in raw.get("videos", {}).items():
            try:
                cache.videos[bvid] = VideoCacheEntry(**ve_raw)
            except (TypeError, ValueError):
                continue
        stats = raw.get("stats", {})
        cache.last_full_crawl = stats.get("last_full_crawl", "")
        return cache

    def save(self, path: str | Path) -> None:
        """保存缓存到本地文件"""
        if not self._dirty:
            return

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with open(p, "w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, ensure_ascii=False, indent=2)

        self._dirty = False
        logger.debug(f"缓存已保存: {len(self.videos)} 个视频")

    def save_to_db(self, db: DataBase) -> None:
        """保存缓存到 Git DB

        直接使用 hx_git_db 的 db.open().write_json() 接口。
        需在 db 的 with 块内调用，退出 with 时自动 commit + push。

        Args:
            db: hx_git_db.DataBase 实例
        """
        if not self._dirty:
            return

        with db.open(GIT_DB_PATH) as f:
            f.write_json(self._to_dict())

        self._dirty = False
        logger.debug(f"缓存已写入 Git DB: {len(self.videos)} 个视频")

    @classmethod
    def load(cls, path: str | Path) -> VideoCache:
        """从本地文件加载缓存"""
        p = Path(path)
        cache = cls()

        if not p.exists():
            return cache

        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("缓存文件损坏，将重建")
            return cache

        loaded = cls._from_dict(raw)
        logger.info(f"加载缓存: {len(loaded.videos)} 个视频")
        return loaded

    @classmethod
    def load_from_db(cls, db: DataBase) -> VideoCache:
        """从 Git DB 加载缓存

        直接使用 hx_git_db 的 db.open().read_json() 接口。

        Args:
            db: hx_git_db.DataBase 实例

        Returns:
            VideoCache 实例，远程无数据时返回空缓存
        """
        with db.open(GIT_DB_PATH) as f:
            data = f.read_json()

        if not data or not data.get("videos"):
            logger.info("Git DB 中无缓存数据，返回空缓存")
            return cls()

        loaded = cls._from_dict(data)
        logger.info(f"从 Git DB 加载缓存: {len(loaded.videos)} 个视频")
        return loaded


def get_cache_path(config_path: str | Path | None = None) -> Path:
    """获取缓存文件的默认路径"""
    if config_path:
        base_dir = Path(config_path).parent
    else:
        base_dir = Path(".")
    return base_dir / ".video_cache.json"
