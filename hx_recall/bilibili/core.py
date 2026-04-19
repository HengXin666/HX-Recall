"""B站收藏夹回顾推送 - 核心运行逻辑"""

import sys
import io
import os
import stat
import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hx_git_db import DataBase

# Windows GBK终端兼容：强制UTF-8输出 + 行缓冲
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from hx_recall.config import load_config
from hx_recall.bilibili.fetcher import (
    get_user_favorites,
    get_favorite_videos,
    get_video_info,
    get_video_ai_conclusion,
    get_comment_ai_summary,
    get_hot_comments,
    create_credential,
)
from hx_recall.selector import select_videos
from hx_recall.notifier import notify, send_credential_alert
from hx_recall.formatter import format_message, format_video_data_list
from hx_recall.state import RecallState, get_state_path
from hx_recall.video_cache import VideoCache, get_cache_path


def _win_rmtree_onexc(func, path, exc_info):
    """Windows 兼容的 rmtree 回调：清除只读属性后重试"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _patch_db_cleanup(db: "DataBase") -> None:
    """Patch DataBase.cleanup 使其在 Windows 上能删除 git 只读文件"""
    if sys.platform != "win32":
        return
    original_cleanup = db.cleanup

    def win_safe_cleanup():
        if db._is_temp and os.path.exists(db._work_dir):
            shutil.rmtree(db._work_dir, onexc=_win_rmtree_onexc)
        else:
            original_cleanup()

    db.cleanup = win_safe_cleanup


def _load_houtiku_config_from_gitdb(cfg: "AppConfig") -> None:
    """从 Git DB 的 HX-HouTiKu 分支读取 .env 获取 HouTiKu 配置

    使用 hx-git-db 从 https://github.com/HengXin666/__HX-Data__.git 的
    HX-HouTiKu 分支下读取 .env 文件，解析 HX_HOUTIKU_ENDPOINT 和 HX_HOUTIKU_TOKEN。
    """
    if not cfg.git_db.enabled:
        return

    try:
        from hx_git_db import make_database

        repo_url = "https://github.com/HengXin666/__HX-Data__.git"
        branch = "HX-HouTiKu"
        token = cfg.git_db.token or None

        db = make_database(repo_url, branch, only=True, token=token)
        _patch_db_cleanup(db)
        with db:
            with db.open(".env") as f:
                env_content = f.read()

        if not env_content:
            print("[HouTiKu] Git DB 中未找到 .env 文件")
            return

        # 解析 .env 文件
        endpoint = ""
        api_token = ""
        for line in env_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key == "HX_HOUTIKU_ENDPOINT":
                    endpoint = value
                elif key == "HX_HOUTIKU_TOKEN":
                    api_token = value

        if endpoint and api_token:
            cfg.houtiku.enabled = True
            cfg.houtiku.endpoint = endpoint
            cfg.houtiku.token = api_token
            print(f"[HouTiKu] 已从 Git DB 加载配置 (endpoint: {endpoint[:30]}...)")
        else:
            print("[HouTiKu] .env 中缺少 HX_HOUTIKU_ENDPOINT 或 HX_HOUTIKU_TOKEN")

    except Exception as e:
        print(f"[HouTiKu] 从 Git DB 加载配置失败: {e}")


async def _verify_and_recover_credential(
    config_path: str, cfg: "AppConfig"
) -> "Credential":
    """验证凭证有效性，失效时触发浏览器登录回退，CI环境中发告警邮件"""
    from hx_recall.bilibili.browser_login import browser_login_fallback, verify_credential
    from hx_recall.config import load_config

    cred_cfg = cfg.bilibili_credential
    is_ci = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))

    if cred_cfg.sessdata:
        data = await verify_credential(cred_cfg.sessdata, cred_cfg.dedeuserid)
        if data.get("isLogin"):
            print(f"[Credential] 当前凭证有效, 用户: {data.get('uname', 'unknown')}")
            return create_credential(
                sessdata=cred_cfg.sessdata,
                bili_jct=cred_cfg.bili_jct,
                dedeuserid=cred_cfg.dedeuserid,
                dedeuserid_ckmd5=cred_cfg.dedeuserid_ckmd5,
            )
        print("[Credential] 当前凭证已失效")

        # CI环境：无法打开浏览器，发告警邮件并退出
        if is_ci:
            print("[Credential] CI环境检测到，无法打开浏览器")
            send_credential_alert(cfg)
            raise SystemExit(1)

        # 本地环境：尝试浏览器登录回退
        print("[Credential] 尝试浏览器登录回退...")

    browser_cred = await browser_login_fallback(config_path)
    if browser_cred and browser_cred.is_valid:
        cfg = load_config(config_path)
        cred_cfg = cfg.bilibili_credential
        return create_credential(
            sessdata=cred_cfg.sessdata,
            bili_jct=cred_cfg.bili_jct,
            dedeuserid=cred_cfg.dedeuserid,
            dedeuserid_ckmd5=cred_cfg.dedeuserid_ckmd5,
        )

    # 浏览器登录也失败
    print("[Credential] 浏览器登录失败，继续使用原始凭证（后续可能遇到权限错误）")
    send_credential_alert(cfg)
    return create_credential(
        sessdata=cred_cfg.sessdata,
        bili_jct=cred_cfg.bili_jct,
        dedeuserid=cred_cfg.dedeuserid,
        dedeuserid_ckmd5=cred_cfg.dedeuserid_ckmd5,
    )


def _save_cache(cache: VideoCache, dest: "str | DataBase") -> None:
    """保存缓存，自动选择本地文件或 Git DB"""
    if isinstance(dest, str):
        cache.save(dest)
    else:
        cache.save_to_db(dest)


def _save_state(state: RecallState, dest: "str | DataBase") -> None:
    """保存状态，自动选择本地文件或 Git DB"""
    if isinstance(dest, str):
        state.save(dest)
    else:
        state.save_to_db(dest)


async def _crawl_favorites_incremental(
    target_favs: list[dict],
    credential,
    cache: VideoCache,
    state: RecallState,
    cache_dest: "str | DataBase",
    state_dest: "str | DataBase",
    strategy: str,
) -> list[dict]:
    """增量爬取收藏夹视频（边爬边存，断点续爬）

    爬取进度由 state 管理（断点页码、已知bvid等），
    视频元数据由 cache 管理（详细信息、AI总结等）。
    每页爬完立即存盘，中断也不丢数据。

    Args:
        target_favs: 目标收藏夹列表
        credential: B站凭证
        cache: 视频元数据缓存
        state: 推送状态+爬取进度
        cache_dest: 缓存保存目标（文件路径或 DataBase）
        state_dest: 状态保存目标（文件路径或 DataBase）
        strategy: 选取策略(决定增量模式)

    Returns:
        所有视频列表(缓存+新爬取)
    """
    all_new_videos = []

    for i, fav in enumerate(target_favs):
        fav_id = fav["id"]
        fav_title = fav["title"]
        media_count = fav.get("media_count", 0)

        # 从 state 获取断点续爬页码
        resume_page = state.get_resume_page(fav_id)
        cached_count = state.get_fav_crawled_count(fav_id)

        # 增量模式：有缓存时使用已知bvid集合，遇到已缓存视频则停止翻页
        known_bvids = None
        if cached_count > 0:
            known_bvids = state.get_known_bvids(fav_id)
            print(f"[Fav] ({i+1}/{len(target_favs)}) {fav_title}: "
                  f"缓存 {cached_count} 个, 增量模式(从第{resume_page}页)")
        else:
            print(f"[Fav] ({i+1}/{len(target_favs)}) {fav_title}: "
                  f"无缓存, 全量模式(从第{resume_page}页)")

        print(f"[Fav] ({i+1}/{len(target_favs)}) 正在获取: {fav_title} "
              f"({media_count}个视频, 从第{resume_page}页开始)")

        # 逐页回调：每页爬完就存盘
        async def _on_page_done(
            page_videos: list[dict],
            page: int,
            is_last_page: bool,
            _fav_id: int = fav_id,
            _fav_title: str = fav_title,
            _media_count: int = media_count,
        ) -> None:
            # 更新视频元数据缓存
            cache.update_fav_videos(_fav_id, _fav_title, page_videos)
            _save_cache(cache, cache_dest)

            # 更新爬取进度
            new_bvids = [v["bvid"] for v in page_videos if v.get("bvid")]
            state.update_fav_progress(
                _fav_id, _fav_title, page,
                media_count=_media_count,
                new_bvids=new_bvids,
            )
            if is_last_page:
                state.mark_fav_complete(_fav_id)
            _save_state(state, state_dest)

        videos, final_page, is_complete = await get_favorite_videos(
            fav_id, credential=credential,
            source=fav.get("source", ""),
            resume_page=resume_page,
            media_count=media_count,
            known_bvids=known_bvids,
            on_page_done=_on_page_done,
        )

        for v in videos:
            v["_fav_name"] = fav_title

        all_new_videos.extend(videos)
        print(f"[Fav] ({i+1}/{len(target_favs)}) {fav_title} 完成: "
              f"本次新增 {len(videos)} 个, 总计 {state.get_fav_crawled_count(fav_id)} 个")

        if i < len(target_favs) - 1:
            await asyncio.sleep(2.0)

    return all_new_videos


async def _enrich_videos_with_detail(
    selected: list[dict],
    credential,
    cache: VideoCache,
    cache_dest: "str | DataBase",
) -> list[dict]:
    """获取视频详细信息+AI总结（缓存复用，边获取边存）

    对于已缓存详细信息的视频直接使用缓存。
    AI总结是不变数据，缓存后不再重复获取。
    """
    detailed = []
    total = len(selected)

    for idx, v in enumerate(selected):
        bvid = v["bvid"]
        entry = cache.get(bvid)

        print(f"[Detail] ({idx+1}/{total}) {v.get('title', bvid)[:40]}...")

        # --- 详细信息 ---
        if entry and entry.has_detail:
            info = {
                "bvid": bvid,
                "aid": entry.aid,
                "cid": entry.cid,
                "title": entry.title or v.get("title", ""),
                "desc": entry.desc,
                "cover": entry.cover or v.get("cover", ""),
                "duration": entry.duration,
                "owner_name": entry.owner_name,
                "owner_mid": entry.owner_mid,
                "view": entry.view,
                "like": entry.like,
                "coin": entry.coin,
                "favorite": entry.favorite,
                "danmaku": entry.danmaku,
                "pubdate": entry.pubdate,
                "link": f"https://www.bilibili.com/video/{bvid}",
                "_fav_name": v.get("_fav_name", ""),
            }
            print(f"  详细信息: 缓存命中")
        else:
            try:
                info = await get_video_info(bvid, credential=credential)
            except Exception as e:
                # 已失效/不可见的视频(如 code 62002 "稿件不可见")，跳过
                print(f"  ⚠️ 已失效视频, 跳过: {e}")
                continue
            info["_fav_name"] = v.get("_fav_name", "")
            cache.update_video_detail(bvid, info)
            _save_cache(cache, cache_dest)
            print(f"  详细信息: 已获取并缓存")

        # --- AI视频总结 ---
        if entry and entry.has_ai:
            info["ai_conclusion"] = entry.ai_conclusion
            print(f"  AI总结: 缓存命中")
        else:
            ai_conclusion = ""
            if info.get("cid") and info.get("owner_mid"):
                ai_conclusion = await get_video_ai_conclusion(
                    bvid, info["cid"], info["owner_mid"], credential=credential
                )
            info["ai_conclusion"] = ai_conclusion
            cache.update_video_ai(bvid, ai_conclusion=ai_conclusion)
            _save_cache(cache, cache_dest)
            print(f"  AI总结: 已获取并缓存")

        # --- AI评论总结 ---
        if entry and entry.has_ai and entry.comment_summary:
            info["comment_summary"] = entry.comment_summary
            print(f"  评论总结: 缓存命中")
        else:
            comment_summary = ""
            if info.get("aid"):
                comment_summary = await get_comment_ai_summary(info["aid"])
            info["comment_summary"] = comment_summary
            cache.update_video_ai(bvid, comment_summary=comment_summary)
            _save_cache(cache, cache_dest)
            print(f"  评论总结: 已获取并缓存")

        # --- 热门评论 ---
        if entry and entry.has_ai and entry.hot_comments:
            info["hot_comments"] = entry.hot_comments
            print(f"  热门评论: 缓存命中 ({len(entry.hot_comments)}条)")
        else:
            hot_comments = []
            if info.get("aid"):
                hot_comments = await get_hot_comments(info["aid"], credential=credential)
            info["hot_comments"] = hot_comments
            cache.update_video_ai(bvid, hot_comments=hot_comments)
            _save_cache(cache, cache_dest)
            print(f"  热门评论: 已获取并缓存 ({len(hot_comments)}条)")

        detailed.append(info)

    return detailed


async def run(config_path: str = "config.yaml") -> None:
    cfg = load_config(config_path)
    uid = cfg.bilibili_uid

    # 从 Git DB 加载 HouTiKu 推送配置
    _load_houtiku_config_from_gitdb(cfg)

    use_git_db = cfg.git_db.enabled

    if use_git_db:
        from hx_git_db import make_database

        token = cfg.git_db.token or None
        db = make_database(cfg.git_db.repo_url, cfg.git_db.branch, only=True, token=token)
        _patch_db_cleanup(db)
        with db:
            await _run_inner(cfg, uid, db=db, config_path=config_path)
    else:
        await _run_inner(cfg, uid, db=None, config_path=config_path)


async def _run_inner(
    cfg: "AppConfig",
    uid: int,
    db: "DataBase | None",
    config_path: str,
) -> None:
    """核心运行逻辑，db=None 时使用本地文件，否则使用 Git DB"""
    if db:
        print("[GitDB] 已启用远程缓存 (only 模式)")
        state = RecallState.load_from_db(db)
        cache = VideoCache.load_from_db(db)
    else:
        local_cache_path = get_cache_path(config_path)
        local_state_path = get_state_path(config_path)
        state = RecallState.load(local_state_path)
        cache = VideoCache.load(local_cache_path)

    print(f"[State] 历史推送记录: {len(state.push_history)} 条, 总推送: {state.total_pushes} 次")
    print(f"[State] 爬取进度: {len(state.fav_progress)} 个收藏夹, 已知 {state.total_cached_bvids} 个视频")
    print(f"[Cache] 视频缓存: {len(cache.videos)} 个视频")

    # 创建凭证对象（验证有效性，失效时浏览器登录回退）
    credential = await _verify_and_recover_credential(config_path, cfg)

    # 获取收藏夹列表
    fav_lists = await get_user_favorites(uid, credential=credential)
    target_favs = fav_lists

    if cfg.favorite_ids:
        target_favs = [f for f in fav_lists if f["id"] in cfg.favorite_ids]
        if not target_favs:
            print(f"未找到指定的收藏夹ID: {cfg.favorite_ids}")
            print(f"可用的收藏夹: {[f['id'] for f in fav_lists]}")
            return

    print(f"[Fav] 目标收藏夹 {len(target_favs)} 个: {[f['title'] for f in target_favs]}")

    # 确定缓存保存路径（本地模式用文件路径，Git DB 模式用 db 实例）
    if db:
        cache_save = db
        state_save = db
    else:
        cache_save = str(get_cache_path(config_path))
        state_save = str(get_state_path(config_path))

    # 增量爬取收藏夹视频（边爬边存，断点续爬）
    all_new_videos = await _crawl_favorites_incremental(
        target_favs, credential, cache, state,
        cache_save, state_save, cfg.strategy
    )

    # 合并所有视频（缓存 + 新爬取）用于选取
    all_videos = []
    seen_bvids = set()
    for bvid, entry in cache.videos.items():
        if cfg.favorite_ids:
            if not any(fid in cfg.favorite_ids for fid in entry.fav_ids):
                continue
        if bvid not in seen_bvids:
            seen_bvids.add(bvid)
            all_videos.append({
                "bvid": bvid,
                "title": entry.title,
                "fav_time": entry.fav_time,
                "cover": entry.cover,
                "duration": entry.duration,
                "upper_name": entry.upper_name,
                "upper_mid": entry.upper_mid,
                "_fav_name": entry.fav_names[0] if entry.fav_names else "",
            })

    if not all_videos:
        print("收藏夹中没有视频")
        return

    print(f"[Video] 总计 {len(all_videos)} 个视频 (缓存 {len(cache.videos)} 个, 本次新增 {len(all_new_videos)} 个)")

    # 吃灰过滤：排除冷却期内已推送过的视频
    if cfg.strategy == "dusty" and cfg.dust.allow_repush:
        cooldown = cfg.dust.cooldown_days
        filtered = []
        skipped = 0
        for v in all_videos:
            days = state.days_since_push(v["bvid"])
            if days < cooldown and days != float("inf"):
                skipped += 1
            else:
                filtered.append(v)
        all_videos = filtered
        if skipped > 0:
            print(f"[Dust] 过滤冷却期(<{cooldown}天)内已推的 {skipped} 个, 剩余 {len(all_videos)} 个候选")

    # 选取 Top-K 视频
    selected = select_videos(all_videos, cfg.top_k, cfg.strategy, state=state)

    if not selected:
        print("没有符合条件的待推送视频（可能全部在冷却期内）")
        return

    print(f"[Select] 选取 {len(selected)} 个视频: {[v.get('title', '')[:20] for v in selected]}")

    # 获取视频详细信息 + AI总结（缓存复用）
    detailed = await _enrich_videos_with_detail(
        selected, credential, cache, cache_save
    )

    # 格式化消息
    msg, videos_data = format_video_data_list(detailed, cfg.strategy)

    # 推送通知
    await notify(msg, cfg, videos_data)

    # 标记已推送，保存状态
    state.mark_batch_pushed(selected)
    _save_state(state, state_save)
    print(f"[State] 已标记 {len(selected)} 个视频为已推送, 状态已保存")
