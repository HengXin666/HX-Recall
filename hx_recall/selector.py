"""视频选取策略模块"""

import random
from datetime import datetime


def select_videos(
    videos: list[dict],
    top_k: int = 5,
    strategy: str = "random",
    state=None,
) -> list[dict]:
    """根据策略从视频列表中选取 Top-K 个视频

    策略:
    - random: 随机选取（默认，适合发现遗忘内容）
    - latest: 按收藏时间倒序（最近收藏的）
    - oldest: 按收藏时间正序（最早收藏的，适合回顾老收藏）
    - dusty: 优先推送吃灰最久的（基于state历史记录，需要传入state参数）

    Args:
        videos: 候选视频列表（每项含 bvid, fav_time 等）
        top_k: 选取数量
        strategy: 策略名称
        state: RecallState 实例（dusty策略必需）
    """
    if len(videos) <= top_k:
        return videos

    if strategy == "latest":
        sorted_videos = sorted(videos, key=lambda v: v.get("fav_time", 0), reverse=True)
        return sorted_videos[:top_k]

    if strategy == "oldest":
        sorted_videos = sorted(videos, key=lambda v: v.get("fav_time", 0))
        return sorted_videos[:top_k]

    if strategy == "dusty":
        return _select_dusty(videos, top_k, state)

    # random 策略
    return random.sample(videos, top_k)


def _select_dusty(videos: list[dict], top_k: int, state) -> list[dict]:
    """吃灰策略：优先推送最久未见的视频

    排序规则:
      1. 从未推送过的排最前面（days=inf）
      2. 已推送过的按上次推送时间升序（越早推的越优先）
      3. 同等条件下随机打乱，避免每次都一样
    """
    if state is None:
        # 没有状态则退化为oldest策略
        return select_videos(videos, top_k, "oldest")

    # 计算每个视频的" dust score"
    scored = []
    for v in videos:
        days = state.days_since_push(v["bvid"])
        record = state.get_record(v["bvid"])
        # 从未推送: score = inf (最高优先级), push_count = 0
        # 已推送: score = 距上次天数, push_count = 历史次数
        scored.append({
            **v,
            "_dust_days": days,
            "_push_count": record.push_count,
        })

    # 排序: dust_days降序(越大越久没见) -> push_count升序(越少越新鲜)
    scored.sort(key=lambda x: (-x["_dust_days"], x["_push_count"]))

    selected = scored[:top_k]

    # 清理内部字段再返回
    for item in selected:
        item.pop("_dust_days", None)
        item.pop("_push_count", None)

    return selected
