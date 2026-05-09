"""B站数据获取模块 — 基于 bilibili_api (OAuth2 Token 持久化保活)"""

import asyncio
import random
from typing import Awaitable, Callable

from bilibili_api import BiliAPI
from hx_recall.rate_limiter import get_limiter


async def get_user_favorites_direct_api(uid: int, api: BiliAPI) -> list[dict]:
    """直接调用API获取用户的收藏夹列表（包括私有收藏夹和收藏的他人收藏夹）"""
    favorites = []
    seen_ids = set()

    def _parse_folder(fav: dict, source: str) -> dict:
        attr = fav.get("attr", 0)
        is_private = bool(attr & 1)
        is_default = not bool(attr & 2)
        upper = fav.get("upper", {})
        return {
            "id": fav.get("id", 0),
            "fid": fav.get("fid", 0),
            "title": fav.get("title", "未命名收藏夹"),
            "media_count": fav.get("media_count", 0),
            "intro": fav.get("intro", ""),
            "attr": attr,
            "is_private": is_private,
            "is_default": is_default,
            "cover": fav.get("cover", ""),
            "mid": fav.get("mid", 0),
            "ctime": fav.get("ctime", 0),
            "mtime": fav.get("mtime", 0),
            "fav_state": fav.get("fav_state", 0),
            "like_state": fav.get("like_state", 0),
            "state": fav.get("state", 0),
            "source": source,
            "upper_name": upper.get("name", ""),
            "upper_mid": upper.get("mid", 0),
        }

    try:
        # 获取用户创建的所有收藏夹
        created = await asyncio.to_thread(api.get_fav_list, uid)
        for fav in (created or []):
            fid = fav.get("id", 0)
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                favorites.append(_parse_folder(fav, "created"))

        # 获取用户收藏的他人收藏夹（分页）
        page = 1
        while True:
            data = await asyncio.to_thread(api.get_fav_collected_list, uid, page)
            fav_list = data.get("list", []) or []
            for fav in fav_list:
                fid = fav.get("id", 0)
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    favorites.append(_parse_folder(fav, "collected"))
            if len(fav_list) < 20:
                break
            page += 1

    except Exception as e:
        print(f"获取收藏夹列表失败: {e}")

    return favorites


async def get_user_favorites(uid: int, api: BiliAPI) -> list[dict]:
    """获取用户的所有收藏夹列表"""
    return await get_user_favorites_direct_api(uid, api)


async def get_favorite_videos(
    fav_id: int, api: BiliAPI, source: str = "",
    resume_page: int = 1, media_count: int = 0,
    known_bvids: set[str] | None = None,
    on_page_done: "Callable[[list[dict], int, bool], Awaitable[None]] | None" = None,
) -> tuple[list[dict], int, bool]:
    """获取指定收藏夹中的视频列表（支持增量/断点续爬）

    Args:
        fav_id: 收藏夹mlid
        api: BiliAPI 实例
        source: 收藏夹来源("created"或"collected")
        resume_page: 断点续爬起始页(1=从头)
        media_count: 收藏夹视频总数(用于进度显示)
        known_bvids: 已知视频bvid集合，遇到已知视频时停止(增量模式)
        on_page_done: 每页爬完的异步回调(page_videos, page, is_last_page)

    Returns:
        (视频列表, 最终页码, 是否完整爬完)
    """
    limiter = get_limiter(rps=0.8)
    results: list[dict] = []
    max_retries = 3
    max_total_waf_wait = 30.0
    total_waf_wait = 0.0
    incremental_stop = False
    has_more = True
    final_page = resume_page

    try:
        page = resume_page
        while has_more:
            await limiter.acquire()

            page_success = False
            page_videos: list[dict] = []

            for retry in range(max_retries):
                try:
                    data = await asyncio.to_thread(
                        api.get_fav_media_list, fav_id, page, 20, 2
                    )
                except RuntimeError as e:
                    msg = str(e)
                    if "-412" in msg or "412" in msg or "WAF" in msg:
                        wait = (retry + 1) * 2 + random.uniform(0.5, 1.5)
                        total_waf_wait += wait
                        if total_waf_wait > max_total_waf_wait:
                            print(f"收藏夹 {fav_id} WAF拦截累计等待超限({total_waf_wait:.0f}s), 跳过剩余页")
                            return results, final_page, False
                        print(f"收藏夹 {fav_id} 第{page}页被WAF拦截，等待{wait:.1f}秒后重试... (累计{total_waf_wait:.0f}s)")
                        await asyncio.sleep(wait)
                        continue
                    print(f"收藏夹 {fav_id} 第{page}页API错误: {e}")
                    break

                # 请求成功
                medias = data.get("medias") or []
                has_more = data.get("has_more", False)

                for item in medias:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == 2:
                        item_attr = item.get("attr", 0)
                        if item_attr and (item_attr & 9):
                            continue
                        item_title = item.get("title", "")
                        if item_title in ("已失效视频", ""):
                            continue

                        bvid = item.get("bvid") or item.get("bv_id")
                        if bvid:
                            if known_bvids and bvid in known_bvids:
                                incremental_stop = True
                                print(f"[fav:{fav_id}] 第{page}页遇到已缓存视频 {bvid}, 增量停止")
                                break
                            v = {
                                "bvid": bvid,
                                "title": item.get("title", ""),
                                "fav_time": item.get("fav_time", 0),
                                "cover": item.get("cover", ""),
                                "type": "video",
                                "type_code": 2,
                                "id": item.get("id", 0),
                                "duration": item.get("duration", 0),
                                "upper_name": (
                                    item.get("upper") or {}
                                ).get("name", ""),
                                "upper_mid": (
                                    item.get("upper") or {}
                                ).get("mid", 0),
                            }
                            results.append(v)
                            page_videos.append(v)

                # 进度日志
                if media_count > 0:
                    pct = min(len(results) / media_count * 100, 100)
                    print(f"  [fav:{fav_id}] 第{page}页: +{len(page_videos)} 累计 {len(results)}/{media_count} ({pct:.0f}%)")
                else:
                    print(f"  [fav:{fav_id}] 第{page}页: +{len(page_videos)} 累计 {len(results)}")

                page_success = True
                break
            else:
                # 所有重试都用尽
                print(f"收藏夹 {fav_id} 第{page}页请求失败")

            final_page = page

            if page_success:
                if on_page_done:
                    await on_page_done(page_videos, page, not has_more)

            if incremental_stop:
                break

            if not page_success:
                break

            page += 1
            await asyncio.sleep(random.uniform(0.5, 1.0))

    except Exception as e:
        print(f"获取收藏夹 {fav_id} 内容失败: {e}")

    is_complete = not incremental_stop and not has_more
    if results:
        mode = "增量" if incremental_stop else "全量"
        print(f"[fav:{fav_id}] 获取 {len(results)} 个视频 ({mode}), 爬到第{final_page}页")
    return results, final_page, is_complete


async def get_video_info(bvid: str, api: BiliAPI) -> dict:
    """获取视频详细信息"""
    limiter = get_limiter(rps=0.8)
    await limiter.acquire()

    info = await asyncio.to_thread(api.get_video_info, bvid)

    stat = info.get("stat", {})
    owner = info.get("owner", {})

    return {
        "bvid": bvid,
        "aid": info.get("aid", 0),
        "cid": info.get("cid", 0),
        "title": info.get("title", ""),
        "desc": info.get("desc", ""),
        "cover": info.get("pic", ""),
        "duration": info.get("duration", 0),
        "owner_name": owner.get("name", ""),
        "owner_mid": owner.get("mid", 0),
        "view": stat.get("view", 0),
        "like": stat.get("like", 0),
        "coin": stat.get("coin", 0),
        "favorite": stat.get("favorite", 0),
        "danmaku": stat.get("danmaku", 0),
        "pubdate": info.get("pubdate", 0),
        "link": f"https://www.bilibili.com/video/{bvid}",
    }


async def get_video_ai_conclusion(
    bvid: str, cid: int, up_mid: int, api: BiliAPI
) -> str:
    """获取视频AI总结内容"""
    try:
        limiter = get_limiter(rps=0.8)
        await limiter.acquire()
        result = await asyncio.to_thread(api.get_video_ai_conclusion, bvid, cid, up_mid)
        model_result = result.get("model_result", {})
        summary = model_result.get("summary", "")
        outline = model_result.get("outline", [])

        parts = []
        if summary:
            parts.append(summary)
        for item in outline:
            title = item.get("title", "")
            bullet_points = item.get("bullet_point", [])
            if title:
                parts.append(f"  {title}")
            for bp in bullet_points:
                parts.append(f"    - {bp}")

        return "\n".join(parts) if parts else ""
    except Exception as e:
        print(f"获取视频AI总结失败 [{bvid}]: {e}")
        return ""


async def get_hot_comments(
    oid: int, api: BiliAPI, top_k: int = 5
) -> list[dict]:
    """获取视频热门评论

    Args:
        oid: 视频aid
        api: BiliAPI 实例
        top_k: 返回前几条热门评论

    Returns:
        热门评论列表 [{"name": str, "content": str, "like": int, "level": int}]
    """
    if not oid:
        return []

    limiter = get_limiter(rps=0.8)
    await limiter.acquire()

    try:
        comments = await asyncio.to_thread(api.get_top_comments, oid, top_k)
    except Exception as e:
        print(f"获取热门评论失败 [oid={oid}]: {e}")
        return []

    results = []
    for c in comments:
        content = c.get("content", "").replace("\n", " ").strip()
        if not content:
            continue
        results.append({
            "name": c.get("uname", "匿名"),
            "content": content[:200],
            "like": c.get("like", 0),
            "level": 0,
        })

    results.sort(key=lambda c: c["like"], reverse=True)
    return results


async def get_comment_ai_summary(oid: int, api: BiliAPI) -> str:
    """获取评论区AI总结"""
    try:
        limiter = get_limiter(rps=0.8)
        await limiter.acquire()
        return await asyncio.to_thread(api.get_comment_ai_summary, oid)
    except Exception as e:
        print(f"获取评论区AI总结失败 [oid={oid}]: {e}")
        return ""
