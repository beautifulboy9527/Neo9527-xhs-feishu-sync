#!/usr/bin/env python3
import argparse
import html
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

from xhs_skill_config import load_feishu_user_config, pick_str


def resolve_bin(name: str) -> str:
    return shutil.which(name) or shutil.which(f"{name}.cmd") or name


def run_cmd(cmd: List[str]) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        hint = ""
        if "Code: -104" in stderr or '"code":-104' in stderr:
            hint = (
                "\n提示: 当前 cookie 已登录但没有访问权限（code=-104）。"
                "请在已登录小红书的同一浏览器里重新复制完整 cookie，"
                "重点确认 a1 与 web_session 来自同一次会话，且 a1 值不要带多余分隔符。"
            )
        elif "at least 'a1' and 'web_session'" in stderr:
            hint = "\n提示: 至少提供 a1 和 web_session；可额外补 id_token。"
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{stderr}{hint}")
    return proc.stdout


def parse_json_output(raw: str, cmd_name: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{cmd_name} 输出不是合法 JSON: {exc}") from exc


def parse_json_output_flexible(raw: str, cmd_name: str) -> Any:
    text = (raw or "").strip()
    # redbook read 可能带前缀日志（如 "Using manual cookie string."）
    start = text.find("{")
    if start >= 0:
        text = text[start:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{cmd_name} 输出不是合法 JSON: {exc}") from exc


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in (cookie_string or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key and val:
            cookies[key] = val
    return cookies


def compose_cookie_string(
    cookie_string: str, a1: str, web_session: str, id_token: str
) -> str:
    raw = (cookie_string or "").strip()
    # Friendly shortcut: if user passes only the raw a1 token, auto-wrap it.
    if raw and "=" not in raw and len(raw) >= 16:
        raw = f"a1={raw}"
    parsed = parse_cookie_string(raw)
    if a1:
        parsed["a1"] = a1.strip()
    if web_session:
        parsed["web_session"] = web_session.strip()
    if id_token:
        parsed["id_token"] = id_token.strip()
    ordered = []
    for k in ("a1", "web_session", "id_token"):
        v = parsed.pop(k, "")
        if v:
            ordered.append(f"{k}={v}")
    for k, v in parsed.items():
        if k and v:
            ordered.append(f"{k}={v}")
    return "; ".join(ordered)


def fetch_profile_html(profile_url: str, cookies: Dict[str, str]) -> str:
    req = urllib.request.Request(
        profile_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()]),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def read_cookie_file(path_text: str) -> str:
    p = Path(path_text).expanduser()
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def enrich_author_from_profile_html(profile_html: str) -> Dict[str, str]:
    def find_one(pattern: str) -> str:
        m = re.search(pattern, profile_html, re.S)
        return html.unescape(m.group(1)).strip() if m else ""

    # 优先从主页可见信息解析，避免继续依赖帖子卡片里的稀疏作者字段
    follows = find_one(r'<span class="count"[^>]*>([^<]+)</span><span class="shows"[^>]*>\s*关注\s*</span>')
    fans = find_one(r'<span class="count"[^>]*>([^<]+)</span><span class="shows"[^>]*>\s*粉丝\s*</span>')
    likes_and_collects = find_one(
        r'<span class="count"[^>]*>([^<]+)</span><span class="shows"[^>]*>\s*获赞与收藏\s*</span>'
    )
    desc = find_one(r'<div class="user-desc"[^>]*>(.*?)</div>')
    ip_location = find_one(r'<span class="user-IP"[^>]*>\s*IP属地：([^<]+)</span>')
    red_id = find_one(r'<span class="user-redId"[^>]*>\s*小红书号：([^<]+)</span>')
    nick_name = find_one(r'<div class="user-name"[^>]*>\s*([^<]+)')
    note_count = find_one(r'<span>\s*笔记・([^<]+)\s*</span>')

    return {
        "nick_name": nick_name,
        "fans": fans,
        "follows": follows,
        "likes_and_collects": likes_and_collects,
        "desc": desc,
        "ip_location": ip_location,
        "red_id": red_id,
        "note_count": note_count,
    }


def normalize_note(item: Dict[str, Any]) -> Dict[str, Any]:
    note_card = item.get("note_card") if isinstance(item.get("note_card"), dict) else {}
    note_user = note_card.get("user") if isinstance(note_card.get("user"), dict) else {}
    interact = (
        note_card.get("interact_info")
        if isinstance(note_card.get("interact_info"), dict)
        else {}
    )
    corner_tags = (
        note_card.get("corner_tag_info")
        if isinstance(note_card.get("corner_tag_info"), list)
        else []
    )

    note_id = (
        item.get("noteId")
        or item.get("note_id")
        or item.get("id")
        or item.get("noteid")
        or ""
    )
    xsec_token = item.get("xsec_token") or ""
    note_url = (
        item.get("noteUrl")
        or item.get("note_url")
        or item.get("url")
        or item.get("noteurl")
        or f"https://www.xiaohongshu.com/explore/{item.get('id')}" if item.get("id") else ""
        or ""
    )
    if note_url and xsec_token and "xsec_token=" not in note_url:
        sep = "&" if "?" in note_url else "?"
        note_url = f"{note_url}{sep}xsec_token={xsec_token}"
    user_id = (
        item.get("userId")
        or item.get("user_id")
        or item.get("authorId")
        or item.get("auther_user_id")
        or note_user.get("user_id")
        or ""
    )
    user_xsec_token = note_user.get("xsec_token") or ""
    author_profile_url = (
        item.get("auther_home_page_url")
        or item.get("author_profile_url")
        or item.get("profile_url")
        or (f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else "")
    )
    if author_profile_url and user_xsec_token and "xsec_token=" not in author_profile_url:
        sep = "&" if "?" in author_profile_url else "?"
        author_profile_url = f"{author_profile_url}{sep}xsec_token={user_xsec_token}"
    title = (
        item.get("title")
        or item.get("note_display_title")
        or note_card.get("display_title")
        or ""
    )
    liked_count = (
        item.get("likedCount")
        or item.get("note_liked_count")
        or interact.get("liked_count")
        or 0
    )
    comment_count = (
        item.get("commentCount")
        or item.get("comment_count")
        or interact.get("comment_count")
        or 0
    )
    share_count = (
        item.get("shareCount")
        or item.get("share_count")
        or interact.get("shared_count")
        or 0
    )
    tags = item.get("tags") or item.get("note_tags") or []
    if not tags and isinstance(item.get("note_card", {}), dict):
        desc_text = str(item.get("note_card", {}).get("desc", "") or "")
        if "# " in desc_text or "#" in desc_text:
            tags = []
    created_at = item.get("createdAt") or item.get("note_create_time") or ""
    if not created_at:
        for tag in corner_tags:
            if isinstance(tag, dict) and tag.get("type") == "publish_time":
                created_at = str(tag.get("text", ""))
                break

    return {
        "note_id": str(note_id),
        "note_url": str(note_url),
        "user_id": str(user_id),
        "author_name": note_user.get("nickname") or note_user.get("nick_name") or "",
        "author_profile_url": str(author_profile_url),
        "title": title,
        "liked_count": str(liked_count),
        "comment_count": str(comment_count),
        "share_count": str(share_count),
        "collected_count": str(interact.get("collected_count") or item.get("collectedCount") or 0),
        "note_desc": str(note_card.get("desc") or item.get("note_desc") or ""),
        "note_type": str(note_card.get("type") or item.get("note_card_type") or ""),
        "cover_url": "",
        "tags": tags if isinstance(tags, list) else [],
        "created_at": str(created_at),
        "raw": item,
    }


def normalize_author(item: Dict[str, Any]) -> Dict[str, Any]:
    user_id = item.get("userId") or item.get("user_id") or ""
    nick_name = item.get("nickName") or item.get("nick_name") or item.get("nickname") or ""
    fans = item.get("fans") or item.get("fansCount") or ""
    follows = item.get("follows") or item.get("followCount") or ""
    desc = item.get("desc") or item.get("description") or ""
    profile_url = item.get("profileUrl") or item.get("profile_url") or item.get("user_link_url") or ""
    avatar = item.get("avatar") or ""
    likes_and_collects = item.get("likes_and_collects") or ""
    ip_location = item.get("ip_location") or ""
    red_id = item.get("red_id") or ""
    note_count = item.get("note_count") or ""

    return {
        "user_id": str(user_id),
        "nick_name": nick_name,
        "fans": str(fans),
        "follows": str(follows),
        "desc": desc,
        "profile_url": profile_url,
        "avatar": avatar,
        "likes_and_collects": str(likes_and_collects),
        "interaction": str(likes_and_collects),
        "ip_location": str(ip_location),
        "red_id": str(red_id),
        "note_count": str(note_count),
        "raw": item,
    }


def extract_notes_from_search(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("items") or payload.get("notes") or payload.get("data") or []
    else:
        candidates = []

    if not isinstance(candidates, list):
        return []
    notes: List[Dict[str, Any]] = []
    for x in candidates:
        if not isinstance(x, dict):
            continue
        if x.get("model_type") not in ("note", None):
            continue
        if "note_card" not in x:
            continue
        normalized = normalize_note(x)
        if not normalized.get("note_id"):
            continue
        notes.append(normalized)
    return notes


def collect_authors(notes: List[Dict[str, Any]], cookie_string: str = "") -> List[Dict[str, Any]]:
    cookies = parse_cookie_string(cookie_string)
    seen: Dict[str, Dict[str, Any]] = {}
    for n in notes:
        uid = n.get("user_id", "")
        if not uid:
            continue
        if uid in seen:
            continue
        raw = n.get("raw", {})
        note_card = raw.get("note_card") if isinstance(raw.get("note_card"), dict) else {}
        note_user = note_card.get("user") if isinstance(note_card.get("user"), dict) else {}
        author = {
            "user_id": uid,
            "nick_name": raw.get("auther_nick_name") or raw.get("nickName") or note_user.get("nickname") or note_user.get("nick_name") or "",
            "fans": "",
            "follows": "",
            "desc": "",
            "profile_url": raw.get("auther_home_page_url") or (f"https://www.xiaohongshu.com/user/profile/{uid}" if uid else ""),
            "avatar": raw.get("auther_avatar") or note_user.get("avatar") or "",
            "latest_note_title": n.get("title", ""),
            "latest_note_time": n.get("created_at", ""),
            # 注意：engagement_snapshot 已移除——作者表的赞藏数只能来自主页 HTML 补采，
            # 绝对不能把「这条笔记」的点赞数充数写进去。
            "raw": raw,
        }
        profile_token = note_user.get("xsec_token") or ""
        if author.get("profile_url") and profile_token and "xsec_token=" not in str(author["profile_url"]):
            sep = "&" if "?" in str(author["profile_url"]) else "?"
            author["profile_url"] = f"{author['profile_url']}{sep}xsec_token={profile_token}"
        if author["profile_url"] and cookies:
            try:
                profile_html = fetch_profile_html(author["profile_url"], cookies)
                enriched = enrich_author_from_profile_html(profile_html)
                for k, v in enriched.items():
                    if not author.get(k) and v:
                        author[k] = v
            except Exception:
                # 主页补采失败时保留帖子卡片兜底数据，不影响主流程可用性
                pass
        seen[uid] = normalize_author(author)
    return list(seen.values())


def search_payload(args: argparse.Namespace) -> Any:
    cmd = [
        resolve_bin("redbook"),
        "search",
        args.keyword,
        "--sort",
        args.sort,
        "--type",
        args.type,
        "--json",
    ]
    if args.cookie_string:
        cmd.extend(["--cookie-string", args.cookie_string])
    return parse_json_output(run_cmd(cmd), "redbook search")


def read_note_detail(note_url: str, cookie_string: str) -> Dict[str, Any]:
    if not note_url:
        return {}
    cmd = [resolve_bin("redbook"), "read", note_url, "--json"]
    if cookie_string:
        cmd.extend(["--cookie-string", cookie_string])
    raw = run_cmd(cmd)
    detail = parse_json_output_flexible(raw, "redbook read")
    return detail if isinstance(detail, dict) else {}


def extract_cover_url_from_detail(detail: Dict[str, Any]) -> str:
    image_list = detail.get("image_list")
    if isinstance(image_list, list) and image_list:
        first = image_list[0] if isinstance(image_list[0], dict) else {}
        for k in ("url_default", "url_pre", "url"):
            val = first.get(k)
            if isinstance(val, str) and val:
                return val
        info_list = first.get("info_list")
        if isinstance(info_list, list):
            for x in info_list:
                if isinstance(x, dict) and isinstance(x.get("url"), str) and x.get("url"):
                    return x["url"]
    return ""


def enrich_notes_with_details(notes: List[Dict[str, Any]], cookie_string: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for note in notes:
        patched = dict(note)
        try:
            detail = read_note_detail(str(note.get("note_url", "")), cookie_string)
        except Exception:
            detail = {}
        if detail:
            user = detail.get("user") if isinstance(detail.get("user"), dict) else {}
            if not patched.get("note_desc"):
                patched["note_desc"] = str(detail.get("desc") or "")
            if not patched.get("title"):
                patched["title"] = str(detail.get("title") or "")
            if not patched.get("note_type"):
                patched["note_type"] = str(detail.get("type") or "")
            if not patched.get("author_name"):
                patched["author_name"] = str(user.get("nickname") or "")
            if not patched.get("user_id"):
                patched["user_id"] = str(user.get("user_id") or "")
            if not patched.get("author_profile_url") and patched.get("user_id"):
                patched["author_profile_url"] = f"https://www.xiaohongshu.com/user/profile/{patched.get('user_id')}"
            user_token = str(user.get("xsec_token") or "")
            if patched.get("author_profile_url") and user_token and "xsec_token=" not in str(patched["author_profile_url"]):
                sep = "&" if "?" in str(patched["author_profile_url"]) else "?"
                patched["author_profile_url"] = f"{patched['author_profile_url']}{sep}xsec_token={user_token}"
            if not patched.get("created_at"):
                t = detail.get("time")
                patched["created_at"] = str(t or "")
            if not patched.get("cover_url"):
                patched["cover_url"] = extract_cover_url_from_detail(detail)

            if not patched.get("tags"):
                tag_list = detail.get("tag_list")
                if isinstance(tag_list, list):
                    patched["tags"] = [
                        str(t.get("name"))
                        for t in tag_list
                        if isinstance(t, dict) and str(t.get("name", "")).strip()
                    ]
            patched["raw"] = detail
        out.append(patched)
    return out


def do_collect_notes(args: argparse.Namespace) -> int:
    payload = search_payload(args)
    notes = extract_notes_from_search(payload)[: args.total]
    notes = enrich_notes_with_details(notes, args.cookie_string)
    notes_path = Path(args.out_notes)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(notes)


def do_collect_authors(args: argparse.Namespace) -> int:
    payload = search_payload(args)
    notes = extract_notes_from_search(payload)[: args.total]
    authors = collect_authors(notes, args.cookie_string)
    authors_path = Path(args.out_authors)
    authors_path.parent.mkdir(parents=True, exist_ok=True)
    authors_path.write_text(json.dumps(authors, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(authors)


def do_collect_both(args: argparse.Namespace) -> Tuple[int, int]:
    payload = search_payload(args)
    notes = extract_notes_from_search(payload)[: args.total]
    notes = enrich_notes_with_details(notes, args.cookie_string)
    authors = collect_authors(notes, args.cookie_string)
    notes_path = Path(args.out_notes)
    authors_path = Path(args.out_authors)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    authors_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    authors_path.write_text(json.dumps(authors, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(notes), len(authors)


def normalize_comment(item: Dict[str, Any]) -> Dict[str, Any]:
    """将 redbook comments --json 返回的单条评论标准化。"""
    user_info = item.get("user_info") or {}
    target_comment = item.get("target_comment") or {}
    return {
        "comment_id": str(item.get("id") or ""),
        "note_id": str(item.get("note_id") or ""),
        "content": str(item.get("content") or ""),
        "liked_count": str(item.get("like_count") or 0),
        "ip_location": str(item.get("ip_location") or ""),
        "is_author": 1 if item.get("at_user") == 1 or item.get("is_author") else 0,
        "created_at": str(item.get("create_time") or item.get("time") or ""),
        # 子评论
        "parent_id": str(target_comment.get("id") or ""),
        "reply_to_user": str(target_comment.get("user_info", {}).get("nickname") or ""),
        "is_root": 1 if not target_comment.get("id") else 0,
        # 评论者
        "user_id": str(user_info.get("userid") or user_info.get("user_id") or ""),
        "user_nickname": str(user_info.get("nickname") or ""),
        "sub_comment_count": str(item.get("sub_comment_count") or item.get("sub_comment_count") or 0),
        "raw": item,
    }


def fetch_comments_for_note(note_url: str, cookie_string: str, fetch_all: bool = False) -> List[Dict[str, Any]]:
    """调用 redbook comments 获取指定笔记的全部评论。"""
    cmd = [resolve_bin("redbook"), "comments", note_url, "--json"]
    if fetch_all:
        cmd.append("--all")
    if cookie_string:
        cmd.extend(["--cookie-string", cookie_string])
    raw = run_cmd(cmd)
    data = parse_json_output_flexible(raw, "redbook comments")

    comments: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("comments") or data.get("data") or data.get("list") or []
    else:
        items = []

    for c in items:
        if isinstance(c, dict):
            comments.append(normalize_comment(c))
    return comments


def do_collect_comments(args: argparse.Namespace) -> int:
    """采集一条或多条笔记的评论，输出 JSON 文件。"""
    # 支持 note_url 或从 notes JSON 文件批量读取
    note_urls = []
    if args.notes_file:
        p = Path(args.notes_file)
        if p.is_file():
            notes_data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(notes_data, list):
                for n in notes_data:
                    url = n.get("note_url") or n.get("url") or ""
                    if url:
                        note_urls.append(url)
    if args.note_url:
        note_urls.insert(0, args.note_url)

    if not note_urls:
        raise ValueError("至少提供一个 --note-url 或 --notes-file")

    all_comments: List[Dict[str, Any]] = []
    for url in note_urls[:args.max_notes]:
        print(f"正在获取评论: {url}", flush=True)
        try:
            comments = fetch_comments_for_note(url, args.cookie_string, fetch_all=args.all)
            print(f"  → 获取 {len(comments)} 条评论", flush=True)
            all_comments.extend(comments)
        except Exception as e:
            print(f"  → 失败: {e}", flush=True)

    out_path = Path(args.out_comments)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_comments, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"总计 {len(all_comments)} 条评论 → {out_path}")
    return len(all_comments)


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取小红书笔记与作者（基于 redbook）")
    parser.add_argument("--config", default="", help="config.json 路径")
    parser.add_argument("--cookie-string", default="", help="全局 cookie，优先级最高")
    parser.add_argument("--cookie-file", default="", help="包含 cookie 的文本文件路径")
    parser.add_argument("--a1", default="", help="可选：仅提供 a1")
    parser.add_argument("--web-session", default="", help="可选：仅提供 web_session")
    parser.add_argument("--id-token", default="", help="可选：仅提供 id_token")
    sub = parser.add_subparsers(dest="action", required=True)

    sp_notes = sub.add_parser("notes", help="按关键词采集帖子数据（仅帖子）")
    sp_notes.add_argument("--keyword", required=True)
    sp_notes.add_argument("--sort", default="popular", choices=["general", "popular", "latest"])
    sp_notes.add_argument("--type", default="all", choices=["all", "video", "image"])
    sp_notes.add_argument("--total", type=int, default=5)
    sp_notes.add_argument("--out-notes", required=True)

    sp_authors = sub.add_parser("authors", help="按关键词采集博主数据（仅博主）")
    sp_authors.add_argument("--keyword", required=True)
    sp_authors.add_argument("--sort", default="popular", choices=["general", "popular", "latest"])
    sp_authors.add_argument("--type", default="all", choices=["all", "video", "image"])
    sp_authors.add_argument("--total", type=int, default=5)
    sp_authors.add_argument("--out-authors", required=True)

    sp_both = sub.add_parser("both", help="按关键词同时采集帖子与博主")
    sp_both.add_argument("--keyword", required=True)
    sp_both.add_argument("--sort", default="popular", choices=["general", "popular", "latest"])
    sp_both.add_argument("--type", default="all", choices=["all", "video", "image"])
    sp_both.add_argument("--total", type=int, default=5)
    sp_both.add_argument("--out-notes", required=True)
    sp_both.add_argument("--out-authors", required=True)

    sp_comments = sub.add_parser("comments", help="采集指定笔记的评论")
    sp_comments.add_argument("--note-url", default="", help="单条笔记 URL")
    sp_comments.add_argument("--notes-file", default="", help="笔记 JSON 文件（批量，从 note_url 字段读取）")
    sp_comments.add_argument("--max-notes", type=int, default=10, help="最多处理多少条笔记")
    sp_comments.add_argument("--all", action="store_true", help="获取全部评论（含子评论翻页）")
    sp_comments.add_argument("--out-comments", required=True, help="评论输出 JSON 文件路径")

    args = parser.parse_args()
    cfg = load_feishu_user_config(args.config.strip() or None)
    cookie_file = pick_str(args.cookie_file, cfg, "xhs_cookie_file", "XHS_COOKIE_FILE", "")
    cookie_cfg = pick_str("", cfg, "xhs_cookie", "XHS_COOKIE", "")
    base_cookie = (
        (args.cookie_string or "").strip()
        or (read_cookie_file(cookie_file) if cookie_file else "")
        or cookie_cfg
    )
    a1 = pick_str(args.a1, cfg, "xhs_a1", "XHS_A1", "")
    web_session = pick_str(args.web_session, cfg, "xhs_web_session", "XHS_WEB_SESSION", "")
    id_token = pick_str(args.id_token, cfg, "xhs_id_token", "XHS_ID_TOKEN", "")
    args.cookie_string = compose_cookie_string(base_cookie, a1, web_session, id_token)

    if args.action == "notes":
        note_count = do_collect_notes(args)
        print(json.dumps({"ok": True, "mode": "notes", "note_count": note_count, "out_notes": args.out_notes}, ensure_ascii=False))
    elif args.action == "authors":
        author_count = do_collect_authors(args)
        print(json.dumps({"ok": True, "mode": "authors", "author_count": author_count, "out_authors": args.out_authors}, ensure_ascii=False))
    elif args.action == "both":
        note_count, author_count = do_collect_both(args)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "both",
                    "note_count": note_count,
                    "author_count": author_count,
                    "out_notes": args.out_notes,
                    "out_authors": args.out_authors,
                },
                ensure_ascii=False,
            )
        )
    elif args.action == "comments":
        comment_count = do_collect_comments(args)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "comments",
                    "comment_count": comment_count,
                    "out_comments": args.out_comments,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
