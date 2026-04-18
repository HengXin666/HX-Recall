"""状态持久化模块

管理两类持久化状态：
1. 推送历史（去重 + 吃灰时间计算）
2. 收藏夹爬取进度（断点续爬 + 增量追踪）

支持两种存储后端：
- 本地文件：save(path) / load(path)
- Git DB (hx_git_db)：save_to_db(db) / load_from_db(db)
  使用 HX-Git-DB only 模式，存储到 __HX-Data__ 仓库 HX-RECALL 分支 bilibili/ 下

数据结构:
  {
    "push_history": {
      "<bvid>": {
        "last_pushed_at": ISO8601,
        "push_count": int,
        "fav_name": str,
        "title": str,
      }
    },
    "fav_progress": {
      "<fav_id>": {
        "title": str,
        "last_crawled_page": int,       // 上次爬到的页码(0=完成)
        "last_crawled_at": ISO8601,     // 上次爬取时间
        "media_count": int,             // 收藏夹视频总数
        "crawled_count": int,           // 已爬取的视频数
        "known_bvids": [str],           // 已知视频bvid列表(增量停止用)
      }
    },
    "stats": {
      "total_pushes": int,
      "last_run": ISO8601 | null,
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

logger = logging.getLogger("hx_recall.state")

GIT_DB_PATH = "bilibili/recall_state.json"


@dataclass
class VideoPushRecord:
    """单条视频的推送记录"""
    last_pushed_at: str = ""  # ISO8601 UTC
    push_count: int = 0
    fav_name: str = ""
    title: str = ""

    @property
    def days_since_last_push(self) -> float:
        """距离上次推送的天数（未推送过返回inf）"""
        if not self.last_pushed_at:
            return float("inf")
        try:
            last_dt = datetime.fromisoformat(self.last_pushed_at)
            now = datetime.now(timezone.utc)
            return (now - last_dt).total_seconds() / 86400
        except (ValueError, TypeError):
            return float("inf")


@dataclass
class FavProgress:
    """单个收藏夹的爬取进度"""
    title: str = ""
    last_crawled_page: int = 0     # 上次成功爬到的页码(0=完成或未开始)
    last_crawled_at: str = ""      # ISO8601
    media_count: int = 0           # 收藏夹视频总数
    crawled_count: int = 0         # 已爬取的视频数
    known_bvids: list[str] = field(default_factory=list)  # 已知bvid(增量停止用)

    @property
    def is_complete(self) -> bool:
        """该收藏夹是否已完整爬取"""
        return self.last_crawled_page == 0 and self.crawled_count > 0

    @property
    def known_bvids_set(self) -> set[str]:
        """返回known_bvids的set视图"""
        return set(self.known_bvids)


class RecallState:
    """完整的推送状态 + 爬取进度"""

    def __init__(self):
        self.push_history: dict[str, VideoPushRecord] = {}
        self.fav_progress: dict[int, FavProgress] = {}
        self.total_pushes: int = 0
        self.last_run: str = ""  # ISO8601
        self._dirty: bool = False

    # ---- 推送历史：查询 ----

    def is_pushed(self, bvid: str) -> bool:
        return bvid in self.push_history

    def get_record(self, bvid: str) -> VideoPushRecord:
        return self.push_history.get(bvid, VideoPushRecord())

    def days_since_push(self, bvid: str) -> float:
        return self.get_record(bvid).days_since_last_push

    # ---- 推送历史：写入 ----

    def mark_pushed(
        self,
        bvid: str,
        title: str = "",
        fav_name: str = "",
    ) -> None:
        """标记一个视频为已推送（或更新推送记录）"""
        existing = self.get_record(bvid)
        existing.last_pushed_at = datetime.now(timezone.utc).isoformat()
        existing.push_count += 1
        if title:
            existing.title = title
        if fav_name:
            existing.fav_name = fav_name
        self.push_history[bvid] = existing
        self.total_pushes += 1
        self._dirty = True

    def mark_batch_pushed(self, videos: list[dict]) -> None:
        """批量标记已推送（每个video需含 bvid/title/_fav_name）"""
        for v in videos:
            self.mark_pushed(
                bvid=v["bvid"],
                title=v.get("title", ""),
                fav_name=v.get("_fav_name", ""),
            )
        self.last_run = datetime.now(timezone.utc).isoformat()

    def filter_unpushed(self, videos: list[dict]) -> list[dict]:
        """过滤掉已推送的视频（返回未推送的）"""
        return [v for v in videos if not self.is_pushed(v["bvid"])]

    def sort_by_dust(self, videos: list[dict]) -> list[dict]:
        """按吃灰时间排序（最久没见过的排前面）"""
        return sorted(
            videos,
            key=lambda v: self.days_since_push(v["bvid"]),
            reverse=True,
        )

    # ---- 爬取进度：查询 ----

    def get_fav_progress(self, fav_id: int) -> FavProgress:
        """获取收藏夹爬取进度（不存在返回空进度）"""
        return self.fav_progress.get(fav_id, FavProgress())

    def get_resume_page(self, fav_id: int) -> int:
        """获取断点续爬的起始页码(1-based)

        last_crawled_page=0 表示完成或未开始，都从第1页开始。
        last_crawled_page>0 表示上次爬到该页，从该页开始（该页可能未完成）。
        """
        prog = self.fav_progress.get(fav_id)
        if prog and prog.last_crawled_page > 0:
            return prog.last_crawled_page
        return 1

    def get_known_bvids(self, fav_id: int) -> set[str]:
        """获取收藏夹已知bvid集合（用于增量停止）"""
        return self.fav_progress.get(fav_id, FavProgress()).known_bvids_set

    def get_fav_crawled_count(self, fav_id: int) -> int:
        """获取收藏夹已爬取的视频数"""
        prog = self.fav_progress.get(fav_id)
        return prog.crawled_count if prog else 0

    # ---- 爬取进度：写入 ----

    def update_fav_progress(
        self,
        fav_id: int,
        title: str,
        page: int,
        media_count: int = 0,
        new_bvids: list[str] | None = None,
        crawled_count: int = 0,
    ) -> None:
        """更新收藏夹爬取进度

        Args:
            fav_id: 收藏夹ID
            title: 收藏夹名称
            page: 当前爬到的页码
            media_count: 收藏夹视频总数
            new_bvids: 本次新发现的bvid列表(追加到known_bvids)
            crawled_count: 已爬取的视频数(0则自动计算)
        """
        prog = self.fav_progress.get(fav_id, FavProgress())
        prog.title = title
        prog.last_crawled_page = page
        prog.last_crawled_at = datetime.now(timezone.utc).isoformat()
        prog.media_count = media_count

        if new_bvids:
            existing = set(prog.known_bvids)
            for bvid in new_bvids:
                if bvid not in existing:
                    prog.known_bvids.append(bvid)
                    existing.add(bvid)

        if crawled_count > 0:
            prog.crawled_count = crawled_count
        else:
            prog.crawled_count = len(prog.known_bvids)

        self.fav_progress[fav_id] = prog
        self._dirty = True

    def mark_fav_complete(self, fav_id: int) -> None:
        """标记收藏夹已完整爬取（重置断点页码为0）"""
        prog = self.fav_progress.get(fav_id)
        if prog:
            prog.last_crawled_page = 0  # 0 = 完成，下次从1开始
            prog.crawled_count = len(prog.known_bvids)
            self._dirty = True

    def mark_fav_needs_refresh(self, fav_id: int) -> None:
        """标记收藏夹需要重新爬取（清除进度，从头开始）"""
        prog = self.fav_progress.get(fav_id)
        if prog:
            prog.last_crawled_page = 0
            prog.known_bvids = []
            prog.crawled_count = 0
            self._dirty = True

    def remove_bvids_from_known(self, bvids: set[str]) -> None:
        """从所有收藏夹的known_bvids中移除指定bvid

        用于视频被移出收藏夹后清理进度缓存。
        """
        if not bvids:
            return
        for prog in self.fav_progress.values():
            before = len(prog.known_bvids)
            prog.known_bvids = [b for b in prog.known_bvids if b not in bvids]
            if len(prog.known_bvids) != before:
                prog.crawled_count = len(prog.known_bvids)
                self._dirty = True

    # ---- 统计 ----

    @property
    def total_cached_bvids(self) -> int:
        """所有收藏夹已知bvid总数（去重）"""
        all_bvids: set[str] = set()
        for prog in self.fav_progress.values():
            all_bvids.update(prog.known_bvids)
        return len(all_bvids)

    # ---- 持久化 ----

    def _to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "push_history": {
                bvid: asdict(rec) for bvid, rec in self.push_history.items()
            },
            "fav_progress": {
                str(fid): asdict(prog) for fid, prog in self.fav_progress.items()
            },
            "stats": {
                "total_pushes": self.total_pushes,
                "last_run": self.last_run,
            },
        }

    @staticmethod
    def _from_dict(raw: dict) -> RecallState:
        """从字典反序列化"""
        state = RecallState()
        for bvid, rec in raw.get("push_history", {}).items():
            state.push_history[bvid] = VideoPushRecord(**rec)
        for fid_str, prog_raw in raw.get("fav_progress", {}).items():
            try:
                state.fav_progress[int(fid_str)] = FavProgress(**prog_raw)
            except (TypeError, ValueError):
                continue
        stats = raw.get("stats", {})
        state.total_pushes = stats.get("total_pushes", 0)
        state.last_run = stats.get("last_run", "")
        return state

    @classmethod
    def load(cls, path: str | Path) -> RecallState:
        """从JSON文件加载状态"""
        p = Path(path)
        state = cls()

        if not p.exists():
            state.save(path)
            return state

        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return cls._from_dict(raw)
        except (json.JSONDecodeError, TypeError):
            return state

    def save(self, path: str | Path) -> None:
        """保存到JSON文件"""
        if not self._dirty:
            return

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with open(p, "w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, ensure_ascii=False, indent=2)

        self._dirty = False

    @classmethod
    def load_from_db(cls, db: DataBase) -> RecallState:
        """从 Git DB 加载状态

        直接使用 hx_git_db 的 db.open().read_json() 接口。

        Args:
            db: hx_git_db.DataBase 实例

        Returns:
            RecallState 实例，远程无数据时返回空状态
        """
        with db.open(GIT_DB_PATH) as f:
            data = f.read_json()

        if not data:
            logger.info("Git DB 中无状态数据，返回空状态")
            return cls()

        loaded = cls._from_dict(data)
        logger.info(f"从 Git DB 加载状态: {len(loaded.push_history)} 条推送记录")
        return loaded

    def save_to_db(self, db: DataBase) -> None:
        """保存状态到 Git DB

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
        logger.debug(f"状态已写入 Git DB: {len(self.push_history)} 条推送记录")


def get_state_path(config_path: str | Path | None = None) -> Path:
    """获取state文件的默认路径（与config.yaml同目录）"""
    if config_path:
        base_dir = Path(config_path).parent
    else:
        base_dir = Path(".")
    return base_dir / ".recall_state.json"
