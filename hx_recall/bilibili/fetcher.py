"""B站数据获取模块"""

from bilibili_api import favorite_list, video, user, comment
from bilibili_api import Credential
import httpx
import asyncio
from hx_recall.rate_limiter import get_limiter


def _try_auto_refresh(credential: Credential = None) -> None:
    """尝试自动续期SESSDATA（非阻塞，失败不影响主流程）

    在每次API调用前检查并自动刷新过期的Cookie。
    需要config.yaml中配置了refresh_token才生效。
    注意：此函数仅在首次调用时执行，后续调用跳过以避免WAF限流。
    """
    import logging

    log = logging.getLogger("hx_recall")
    # 全局标记：只执行一次续期检查，避免频繁触发412
    if getattr(_try_auto_refresh, "_done", False):
        return
    _try_auto_refresh._done = True  # type: ignore[attr-defined]

    try:
        from hx_recall.bilibili.sessdata_keeper import check_and_refresh

        # 获取当前事件循环
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(check_and_refresh())
        except RuntimeError:
            asyncio.run(check_and_refresh())
    except Exception as e:
        log.debug(f"SESSDATA自动续期跳过: {e}")


def create_credential(
    sessdata: str = "",
    bili_jct: str = "",
    dedeuserid: str = "",
    dedeuserid_ckmd5: str = "",
) -> Credential:
    """创建B站登录凭证对象"""
    credential_data = {}

    if sessdata:
        credential_data["sessdata"] = sessdata
    if bili_jct:
        credential_data["bili_jct"] = bili_jct
    if dedeuserid:
        credential_data["dedeuserid"] = dedeuserid
    if dedeuserid_ckmd5:
        credential_data["dedeuserid__ckMd5"] = dedeuserid_ckmd5

    if credential_data:
        return Credential(**credential_data)
    else:
        # 如果没有凭证，返回空的Credential对象（匿名访问）
        return Credential()


async def get_user_favorites_direct_api(
    uid: int, credential: Credential = None
) -> list[dict]:
    """直接调用API获取用户的收藏夹列表（包括私有收藏夹和收藏的他人收藏夹）

    使用两个API:
    1. created/list-all: 获取用户创建的所有收藏夹（包括默认收藏夹）
    2. collected/list: 获取用户收藏的他人收藏夹（需带platform=web参数，需分页）
    """
    # 尝试自动续期SESSDATA
    _try_auto_refresh(credential)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://space.bilibili.com/{uid}/favlist",
        "Origin": "https://space.bilibili.com",
    }

    cookies = {}
    if credential:
        if hasattr(credential, "sessdata") and credential.sessdata:
            cookies["SESSDATA"] = credential.sessdata
        if hasattr(credential, "bili_jct") and credential.bili_jct:
            cookies["bili_jct"] = credential.bili_jct
        if hasattr(credential, "dedeuserid") and credential.dedeuserid:
            cookies["DedeUserID"] = credential.dedeuserid

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
        async with httpx.AsyncClient() as client:
            # 获取用户创建的所有收藏夹
            created_url = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
            response = await client.get(
                created_url,
                params={"up_mid": uid},
                headers=headers,
                cookies=cookies,
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    fav_list = data.get("data", {}).get("list", []) or []
                    for fav in fav_list:
                        fid = fav.get("id", 0)
                        if fid and fid not in seen_ids:
                            seen_ids.add(fid)
                            favorites.append(_parse_folder(fav, "created"))
                else:
                    print(
                        f"获取创建的收藏夹API错误: {data.get('message')} (code: {data.get('code')})"
                    )
            else:
                print(f"获取创建的收藏夹HTTP错误: {response.status_code}")

            # 获取用户收藏的他人收藏夹（需分页，需带platform=web）
            page = 1
            while True:
                collected_url = (
                    "https://api.bilibili.com/x/v3/fav/folder/collected/list"
                )
                response = await client.get(
                    collected_url,
                    params={"up_mid": uid, "ps": 20, "pn": page, "platform": "web"},
                    headers=headers,
                    cookies=cookies,
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == 0:
                        fav_list = data.get("data", {}).get("list", []) or []
                        for fav in fav_list:
                            fid = fav.get("id", 0)
                            if fid and fid not in seen_ids:
                                seen_ids.add(fid)
                                favorites.append(_parse_folder(fav, "collected"))

                        if len(fav_list) < 20:
                            break
                    else:
                        print(
                            f"获取收藏的收藏夹API错误: {data.get('message')} (code: {data.get('code')})"
                        )
                        break
                else:
                    print(f"获取收藏的收藏夹HTTP错误: {response.status_code}")
                    break

                page += 1

    except Exception as e:
        print(f"获取收藏夹列表失败: {e}")

    return favorites


async def get_user_favorites(uid: int, credential: Credential = None) -> list[dict]:
    """获取用户的所有收藏夹列表"""
    # 首先尝试直接API调用
    favorites = await get_user_favorites_direct_api(uid, credential)

    if favorites:
        return favorites

    # 如果直接API调用失败，尝试其他方法
    print("直接API调用失败，尝试其他方法...")

    # 这里可以添加其他备用方法
    return []


async def get_user_favorites_backup(
    uid: int, credential: Credential = None
) -> list[dict]:
    """获取用户的所有收藏夹列表（备用方案）"""
    try:
        # 导入收藏夹列表相关模块
        from bilibili_api.favorite_list import FavoriteList

        # 先尝试获取默认收藏夹信息
        # 通常收藏夹ID会有一些固定范围
        favorites = []

        # 尝试常见收藏夹ID范围
        test_ids = [
            uid,  # 有时默认收藏夹ID就是用户ID
            1,
            2,
            3,  # 常见收藏夹ID
        ]

        for fav_id in test_ids:
            try:
                fav = FavoriteList(fav_id, credential=credential)
                info = await fav.get_info()

                if info:
                    favorites.append(
                        {
                            "id": fav_id,
                            "title": info.get("title", f"收藏夹{fav_id}"),
                            "media_count": info.get("media_count", 0),
                            "intro": info.get("intro", ""),
                        }
                    )
            except:
                continue

            # 收藏夹间间隔
            await asyncio.sleep(1.5)

        return favorites

    except Exception as e:
        print(f"备用方案获取收藏夹失败: {e}")
        return []


async def get_user_published_videos(
    uid: int, credential: Credential = None
) -> list[dict]:
    """获取用户发布的视频列表"""
    u = user.User(uid, credential=credential)

    # 获取用户投稿视频列表
    # 注意：可能需要分页获取
    videos = []
    page = 1

    while True:
        try:
            # 使用 get_videos 方法获取投稿视频
            result = await u.get_videos(pn=page)

            if not result or "list" not in result:
                break

            video_list = result["list"]["vlist"] if "list" in result else []

            if not video_list:
                break

            for item in video_list:
                videos.append(
                    {
                        "bvid": item.get("bvid", ""),
                        "title": item.get("title", ""),
                        "created": item.get("created", 0),
                        "length": item.get("length", ""),
                        "play": item.get("play", 0),
                        "comment": item.get("comment", 0),
                        "description": item.get("description", ""),
                        "pic": item.get("pic", ""),
                    }
                )

            # 检查是否有更多页
            page_info = result.get("page", {})
            current_page = page_info.get("pn", 1)
            total_pages = page_info.get("count", 1)

            if current_page >= total_pages:
                break

            page += 1

        except Exception as e:
            print(f"获取第{page}页视频失败: {e}")
            break

    return videos


async def get_favorite_videos(
    fav_id: int, credential: Credential = None, source: str = "",
    resume_page: int = 1, media_count: int = 0,
    known_bvids: set[str] | None = None,
    on_page_done: "Callable[[list[dict], int, bool], Awaitable[None]] | None" = None,
) -> tuple[list[dict], int, bool]:
    """获取指定收藏夹中的视频列表

    支持增量爬取和断点续爬。

    Args:
        fav_id: 收藏夹mlid
        credential: B站登录凭证
        source: 收藏夹来源("created"或"collected")
        resume_page: 断点续爬起始页(1=从头)
        media_count: 收藏夹视频总数(用于进度显示)
        known_bvids: 已知视频bvid集合，遇到已知视频时停止(增量模式)
        on_page_done: 每页爬完的异步回调(videos, page, is_last_page)

    Returns:
        (视频列表, 最终页码, 是否完整爬完)
    """
    return await get_favorite_videos_direct_api(
        fav_id, credential, source,
        resume_page=resume_page, media_count=media_count,
        known_bvids=known_bvids,
        on_page_done=on_page_done,
    )


async def _fetch_via_lib(
    fav: "favorite_list.FavoriteList", fav_id: int
) -> list[dict]:
    """通过bilibili_api-python库获取收藏夹视频列表（分页）"""
    limiter = get_limiter(rps=0.8)
    results = []
    page = 1

    while True:
        await limiter.acquire()
        info = await fav.get_content_video(page=page)
        # 返回结构: {"info": {...}, "medias": [...], "has_more": bool}
        medias = info.get("medias", [])
        if not isinstance(medias, list):
            break

        for item in medias:
            if not isinstance(item, dict):
                continue
            bvid = item.get("bvid") or item.get("bv_id")
            if bvid:
                results.append({
                    "bvid": bvid,
                    "title": item.get("title", ""),
                    "fav_time": item.get("fav_time") or item.get("favtime", 0) or item.get("atime", 0),
                    "cover": item.get("cover", ""),
                    "type": "video",
                    "type_code": 2,
                    "id": item.get("id", 0),
                    "duration": item.get("duration", 0),
                    "upper_name": (item.get("upper") or {}).get("name", "") or item.get("upper_name", ""),
                    "upper_mid": (item.get("upper") or {}).get("mid", 0) or item.get("upper_mid", 0),
                })

        has_more = info.get("has_more", False)
        if not has_more and len(medias) < 20:
            break
        page += 1
        # 分页间延迟，避免触发WAF（由速率限制器统一控制）

    print(f"[fav:{fav_id}] 获取 {len(results)} 个视频 (via lib)")
    return results


async def get_favorite_videos_direct_api(
    fav_id: int, credential: Credential = None, source: str = "",
    resume_page: int = 1, media_count: int = 0,
    known_bvids: set[str] | None = None,
    on_page_done: "Callable[[list[dict], int, bool], Awaitable[None]] | None" = None,
) -> tuple[list[dict], int, bool]:
    """直接调用B站API获取收藏夹视频（支持增量/断点续爬）

    Args:
        fav_id: 收藏夹mlid
        credential: B站登录凭证
        source: 收藏夹来源
        resume_page: 断点续爬起始页(1=从头)
        media_count: 收藏夹视频总数(用于进度显示)
        known_bvids: 已知视频bvid集合，遇到已知视频时停止(增量模式)
        on_page_done: 每页爬完的异步回调(page_videos, page, is_last_page)

    Returns:
        (视频列表, 最终页码, 是否完整爬完)
    """
    import random
    from typing import Awaitable, Callable

    limiter = get_limiter(rps=0.8)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }

    cookies = {}
    if credential:
        if hasattr(credential, "sessdata") and credential.sessdata:
            cookies["SESSDATA"] = credential.sessdata
        if hasattr(credential, "bili_jct") and credential.bili_jct:
            cookies["bili_jct"] = credential.bili_jct
        if hasattr(credential, "dedeuserid") and credential.dedeuserid:
            cookies["DedeUserID"] = credential.dedeuserid

    if not cookies:
        from hx_recall.config import load_config

        try:
            config = load_config()
            cred_config = config.bilibili_credential

            if cred_config.sessdata:
                cookies["SESSDATA"] = cred_config.sessdata
            if cred_config.dedeuserid:
                cookies["DedeUserID"] = cred_config.dedeuserid
            if cred_config.bili_jct:
                cookies["bili_jct"] = cred_config.bili_jct
        except:
            pass

    results: list[dict] = []
    max_retries = 3
    total_waf_wait = 0.0
    max_total_waf_wait = 30.0
    incremental_stop = False
    has_more = True  # 初始为True，由API响应更新
    final_page = resume_page

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            page = resume_page
            while has_more:
                url = f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={fav_id}&pn={page}&ps=20&type=2"

                await limiter.acquire()

                page_success = False
                page_videos: list[dict] = []

                for retry in range(max_retries):
                    try:
                        response = await client.get(
                            url, headers=headers, cookies=cookies, timeout=15.0
                        )

                        if response.status_code == 412:
                            wait = (retry + 1) * 2 + random.uniform(0.5, 1.5)
                            total_waf_wait += wait
                            if total_waf_wait > max_total_waf_wait:
                                print(
                                    f"收藏夹 {fav_id} WAF拦截累计等待超限({total_waf_wait:.0f}s), 跳过剩余页"
                                )
                                return results, final_page, False
                            print(
                                f"收藏夹 {fav_id} 第{page}页被WAF拦截，等待{wait:.1f}秒后重试... (累计{total_waf_wait:.0f}s)"
                            )
                            await asyncio.sleep(wait)
                            continue

                        if response.status_code != 200:
                            print(
                                f"收藏夹 {fav_id} 第{page}页HTTP错误: {response.status_code}"
                            )
                            break

                        if not response.text or not response.text.strip():
                            if retry < max_retries - 1:
                                await asyncio.sleep(1)
                                continue
                            break

                        data = response.json()

                        if data.get("code") == -412:
                            wait = (retry + 1) * 3 + random.uniform(1, 3)
                            total_waf_wait += wait
                            if total_waf_wait > max_total_waf_wait:
                                print(
                                    f"收藏夹 {fav_id} API WAF拦截累计等待超限({total_waf_wait:.0f}s), 跳过剩余页"
                                )
                                return results, final_page, False
                            print(
                                f"收藏夹 {fav_id} 第{page}页API被拦截(code:-412)，等待{wait:.1f}秒后重试... (累计{total_waf_wait:.0f}s)"
                            )
                            await asyncio.sleep(wait)
                            continue

                        if data.get("code") != 0:
                            break

                        # 请求成功
                        content_data = data.get("data") or {}
                        medias = content_data.get("medias") or []
                        has_more = content_data.get("has_more", False)

                        for item in medias:
                            if not isinstance(item, dict):
                                continue
                            if item.get("type") == 2:
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

                    except httpx.TimeoutException:
                        if retry < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        print(f"收藏夹 {fav_id} 第{page}页请求超时")
                        break
                    except Exception as e:
                        if retry < max_retries - 1:
                            await asyncio.sleep(1)
                            continue
                        print(f"收藏夹 {fav_id} 第{page}页请求异常: {e}")
                        break

                # 本页处理完毕（无论成功失败）
                final_page = page

                if page_success:
                    # 逐页回调：每页爬完就存盘
                    if on_page_done:
                        await on_page_done(page_videos, page, not has_more)

                if incremental_stop:
                    break

                if not page_success:
                    # 本页失败且重试用尽，停止翻页
                    break

                page += 1
                # 分页间延迟
                await asyncio.sleep(random.uniform(0.5, 1.0))

    except Exception as e:
        print(f"获取收藏夹 {fav_id} 内容失败: {e}")

    is_complete = not incremental_stop and not has_more
    if results:
        mode = "增量" if incremental_stop else "全量"
        print(f"[fav:{fav_id}] 获取 {len(results)} 个视频 ({mode}), 爬到第{final_page}页")
    return results, final_page, is_complete


async def get_video_info(bvid: str, credential: Credential = None) -> dict:
    """获取视频详细信息"""
    limiter = get_limiter(rps=0.8)
    await limiter.acquire()
    v = video.Video(bvid=bvid, credential=credential)
    info = await v.get_info()

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
    bvid: str, cid: int, up_mid: int, credential: Credential = None
) -> str:
    """获取视频AI总结内容"""
    try:
        limiter = get_limiter(rps=0.8)
        await limiter.acquire()
        v = video.Video(bvid=bvid, credential=credential)
        result = await v.get_ai_conclusion(cid=cid, up_mid=up_mid)
        # AI总结返回结构: { model_result: { summary: "xxx", outline: [...] } }
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
    oid: int, credential: Credential = None, top_k: int = 5
) -> list[dict]:
    """获取视频热门评论（按点赞排序）

    Args:
        oid: 视频aid
        credential: B站凭证
        top_k: 返回前几条热门评论

    Returns:
        热门评论列表 [{"name": str, "content": str, "like": int, "level": int}]
    """
    if not oid:
        return []

    limiter = get_limiter(rps=0.8)
    await limiter.acquire()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com",
    }

    cookies = {}
    if credential:
        if hasattr(credential, "sessdata") and credential.sessdata:
            cookies["SESSDATA"] = credential.sessdata

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/v2/reply",
                params={"oid": oid, "type": 1, "sort": 1, "ps": min(top_k * 2, 20)},
                headers=headers,
                cookies=cookies,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            return []

        replies = (data.get("data") or {}).get("replies") or []
        if not replies:
            return []

        # 按点赞数排序，取top_k
        replies.sort(key=lambda r: (r.get("like", 0), r.get("rcount", 0)), reverse=True)

        results = []
        for r in replies[:top_k]:
            member = r.get("member") or {}
            level_info = member.get("level_info") or {}
            content = r.get("content", {}).get("message", "") if isinstance(r.get("content"), dict) else str(r.get("content", {}).get("message", ""))
            # 清理内容中的换行
            content = content.replace("\n", " ").strip()
            if not content:
                continue
            results.append({
                "name": member.get("uname", "匿名"),
                "content": content[:200],
                "like": r.get("like", 0),
                "level": level_info.get("current_level", 0),
            })

        return results

    except Exception as e:
        print(f"获取热门评论失败 [oid={oid}]: {e}")
        return []


async def get_comment_ai_summary(oid: int) -> str:
    """获取评论区AI总结"""
    try:
        limiter = get_limiter(rps=0.8)
        await limiter.acquire()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.bilibili.com/x/v2/reply/aisummary",
                params={"oid": oid, "type": 1},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            return ""

        summary_data = data.get("data", {})
        summary = summary_data.get("summary", "")
        tags = summary_data.get("tags", [])

        parts = []
        if summary:
            parts.append(summary)
        if tags:
            parts.append("标签: " + ", ".join(tags))

        return "\n".join(parts) if parts else ""
    except Exception as e:
        print(f"获取评论区AI总结失败 [oid={oid}]: {e}")
        return ""
