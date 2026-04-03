"""
sync_comments_to_feishu.py — 评论同步到飞书多维表格

设计原则：
  1. 根评论 = 一行，子评论合并到文本字段（避免数据混乱）
  2. 每条记录关联笔记ID/标题，可按笔记维度分组筛选
  3. 采集必须通过 @lucasygu/redbook CLI（不可直接调 edith HTTP API）

数据流：
  redbook comments → 分离根/子评论 → 合并子评论 → 查飞书获取笔记标题 → 去重 → 写入

经验沉淀（踩坑记录）：
  ❌ 不要用 Python httpx/urllib 直接调 edith.xiaohongshu.com API → 406 风控拦截
  ❌ 不要用 curl_cffi 模拟 TLS 指纹 → 仍然 406
  ❌ Chrome 127+ Windows App-Bound Encryption → 外部工具读不到 cookie
  ✅ 只用 redbook CLI（npm install -g @lucasygu/redbook），它内部处理了风控
  ✅ cookie 通过 --cookie-string "a1=xxx; web_session=xxx" 传入
"""

import json
import sys
import subprocess
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

import httpx

# ─── Feishu API helpers ────────────────────────────────────────────────

def get_feishu_creds():
    """从 ~/.openclaw/openclaw.json 读取飞书凭证"""
    import os
    config_path = Path(os.path.expanduser("~/.openclaw/openclaw.json"))
    if config_path.exists():
        data = json.loads(config_path.read_text("utf-8"))
        feishu = data.get("channels", {}).get("feishu", {})
        if feishu.get("appId") and feishu.get("appSecret"):
            return feishu["appId"], feishu["appSecret"]
    raise SystemExit(
        "❌ 找不到飞书凭证。请配置 ~/.openclaw/openclaw.json → channels.feishu.appId/appSecret"
    )


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token"""
    r = httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("tenant_access_token", "")


def read_openclaw_config():
    """读取 openclaw.json 获取 base_token 等"""
    import os
    config_path = Path(os.path.expanduser("~/.openclaw/openclaw.json"))
    if config_path.exists():
        return json.loads(config_path.read_text("utf-8"))
    return {}


# ─── Redbook CLI helpers ───────────────────────────────────────────────

def fetch_comments_via_cli(note_url: str, cookie_string: str) -> list:
    """
    通过 redbook CLI 获取评论。
    ⚠️ 不要尝试用 httpx/urllib 直接调 edith API，会 406。
    """
    cmd = ["redbook.cmd", "comments", note_url, "--json"]
    if cookie_string:
        cmd.extend(["--cookie-string", cookie_string])
    result = subprocess.run(cmd, capture_output=True, timeout=60, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"redbook CLI failed: {result.stderr}")
    data = json.loads(result.stdout)
    return data if isinstance(data, list) else []


# ─── Comment processing ────────────────────────────────────────────────

def process_comments(raw_comments: list) -> list:
    """
    分离根评论和子评论，合并子评论到根评论。
    返回：只包含根评论的列表，每条带 sub_comments_text 字段。
    """
    root_comments = []
    # 用 id 索引根评论，方便子评论查找
    root_map = {}

    for c in raw_comments:
        if not isinstance(c, dict):
            continue
        target = c.get("target_comment") or {}
        if target.get("id"):
            # 这是子评论，挂到父评论下
            parent_id = target["id"]
            if parent_id not in root_map:
                # 父评论可能不在当前批次，跳过
                continue
            root_map[parent_id].setdefault("_subs", []).append(c)
        else:
            # 根评论
            c["_subs"] = list(c.get("sub_comments") or [])
            root_comments.append(c)
            root_map[c["id"]] = c

    result = []
    for root in root_comments:
        all_subs = root.get("_subs", [])
        subs_text = _format_sub_comments(all_subs)

        user_info = root.get("user_info") or {}
        show_tags = root.get("show_tags") or []

        result.append({
            "comment_id": str(root.get("id") or ""),
            "note_id": str(root.get("note_id") or ""),
            "content": str(root.get("content") or ""),
            "user_nickname": user_info.get("nickname", ""),
            "user_id": str(user_info.get("user_id") or user_info.get("userid") or ""),
            "user_avatar": user_info.get("image", ""),
            "like_count": int(root.get("like_count") or 0),
            "sub_comment_count": int(root.get("sub_comment_count") or 0),
            "sub_comments_text": subs_text,
            "ip_location": str(root.get("ip_location") or ""),
            "create_time": root.get("create_time"),
            "is_author": "is_author" in show_tags,
        })

    return result


def _format_sub_comments(subs: list) -> str:
    """将子评论格式化为易读文本"""
    if not subs:
        return ""
    lines = []
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        sub_user = (sub.get("user_info") or {}).get("nickname", "匿名")
        sub_content = sub.get("content", "")
        target = sub.get("target_comment") or {}
        reply_to = (target.get("user_info") or {}).get("nickname", "")
        like = sub.get("like_count", "0")
        if reply_to and reply_to != sub_user:
            lines.append(f"├─ {sub_user} 回复 {reply_to}: {sub_content} (👍{like})")
        else:
            lines.append(f"├─ {sub_user}: {sub_content} (👍{like})")
    return "\n".join(lines)


# ─── Note title lookup ─────────────────────────────────────────────────

def get_note_titles(token: str, app_token: str, table_id: str) -> dict:
    """从飞书笔记表获取 note_id → 标题 映射"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    note_map = {}
    page_token = ""

    while True:
        params = {"page_size": 500, "field_names": "note_id,标题"}
        if page_token:
            params["page_token"] = page_token
        r = httpx.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {})
        for item in data.get("items") or []:
            fields = item.get("fields", {})
            nid = fields.get("note_id", "")
            title = fields.get("标题", "")
            if nid:
                note_map[nid] = title
        page_token = data.get("page_token", "")
        if not page_token:
            break

    return note_map


# ─── Feishu write ──────────────────────────────────────────────────────

def build_feishu_record(comment: dict, note_title: str, note_url: str) -> dict:
    """构建飞书多维表格记录"""
    create_time = comment.get("create_time")
    if create_time:
        if isinstance(create_time, (int, float)):
            dt = datetime.fromtimestamp(create_time / 1000, tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_str = str(create_time)
    else:
        time_str = ""

    user_id = comment.get("user_id", "")
    user_profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else ""

    # 自动风险等级判断
    like = comment.get("like_count", 0)
    sub_count = comment.get("sub_comment_count", 0)
    if like >= 20 or sub_count >= 10:
        risk = "高"
    elif like >= 5 or sub_count >= 3:
        risk = "中"
    else:
        risk = "低"

    fields = {
        "评论ID": comment["comment_id"],
        "笔记ID": comment["note_id"],
        "笔记标题": note_title,
        "笔记链接": {"link": note_url, "text": note_title or note_url} if note_url else None,
        "评论内容": comment["content"],
        "评论者昵称": comment["user_nickname"],
        "评论者ID": comment["user_id"],
        "是否贴主": "是" if comment.get("is_author") else "否",
        "是否主评论": "是",  # 我们只存根评论
        "层级": 1,  # 根评论层级为1
        "根评论ID": comment["comment_id"],  # 根评论ID就是评论ID
        "用户主页": {"link": user_profile_url, "text": comment["user_nickname"]} if user_profile_url else None,
        "用户头像": {"link": comment.get("user_avatar", ""), "text": "头像"} if comment.get("user_avatar") else None,
        "评论点赞数": comment["like_count"],
        "子评论数": comment["sub_comment_count"],
        "IP属地": comment.get("ip_location", ""),
        "评论时间": time_str,
        "风险等级": risk,
        # 子评论摘要存在「原始JSON」字段
        "原始JSON": comment.get("sub_comments_text", ""),
    }
    # 清除 None 值
    return {k: v for k, v in fields.items() if v is not None}


def get_existing_comment_ids(token: str, app_token: str, table_id: str) -> set:
    """获取飞书表中已有的评论ID"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    existing = set()
    page_token = ""

    while True:
        params = {"page_size": 500, "field_names": "评论ID"}
        if page_token:
            params["page_token"] = page_token
        r = httpx.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            break
        data = r.json().get("data", {})
        for item in data.get("items") or []:
            cid = (item.get("fields") or {}).get("评论ID", "")
            if cid:
                existing.add(cid)
        page_token = data.get("page_token", "")
        if not page_token:
            break

    return existing


def batch_write(token: str, app_token: str, table_id: str, records: list, batch_size: int = 50):
    """批量写入飞书"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    total = 0
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        payload = {"records": [{"fields": r} for r in batch]}
        r = httpx.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        code = r.json().get("code", -1)
        if code != 0:
            print(f"  ⚠️ 写入失败 batch {i}: {r.json().get('msg', '')}")
        else:
            written = len(r.json().get("data", {}).get("records") or [])
            total += written
            print(f"  ✅ 写入 {written} 条 (batch {i // batch_size + 1})")

    return total


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="同步小红书评论到飞书多维表格")
    parser.add_argument("--note-url", help="单条笔记 URL")
    parser.add_argument("--notes-file", help="笔记 JSON 文件（批量，需含 note_url 字段）")
    parser.add_argument("--comments-file", help="已有的评论 JSON 文件（跳过采集，直接同步）")
    parser.add_argument("--cookie-string", help="小红书 cookie 字符串")
    parser.add_argument("--cookie-file", help="cookie 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只处理不写入")
    parser.add_argument("--base-token", help="飞书 Base token")
    parser.add_argument("--table-id", help="飞书评论表 table_id")
    args = parser.parse_args()

    # 读取 cookie
    cookie_string = args.cookie_string
    if not cookie_string and args.cookie_file:
        cookie_string = Path(args.cookie_file).read_text("utf-8").strip()
    if not cookie_string:
        # 尝试从 config.json 读取
        config = read_openclaw_config()
        a1 = config.get("xhs_a1", "")
        ws = config.get("xhs_web_session", "")
        id_token = config.get("xhs_id_token", "")
        if a1 and ws:
            cookie_string = f"a1={a1}; web_session={ws}"
            if id_token:
                cookie_string += f"; id_token={id_token}"

    # 读取飞书配置
    oc_config = read_openclaw_config()
    base_token = args.base_token or oc_config.get("base_token", "")
    table_id = args.table_id or oc_config.get("comments_table_id", "")
    notes_table_id = oc_config.get("notes_table_id", "")

    if not base_token or not table_id:
        print("❌ 缺少 base_token 或 table_id，请通过参数或 config.json 提供")
        sys.exit(1)

    # 飞书认证
    app_id, app_secret = get_feishu_creds()
    token = get_tenant_token(app_id, app_secret)
    print(f"✅ 飞书认证成功")

    # 获取笔记标题映射
    note_titles = {}
    if notes_table_id:
        note_titles = get_note_titles(token, base_token, notes_table_id)
        print(f"✅ 从笔记表获取 {len(note_titles)} 条标题映射")

    # 收集要处理的笔记 URL
    note_urls = []
    if args.note_url:
        note_urls.append(args.note_url)
    if args.notes_file:
        notes = json.loads(Path(args.notes_file).read_text("utf-8"))
        for n in notes:
            url = n.get("note_url") or n.get("url") or ""
            if url and url not in note_urls:
                note_urls.append(url)

    # 采集或读取评论
    all_comments = []
    if args.comments_file:
        print(f"📄 从文件读取评论: {args.comments_file}")
        raw = json.loads(Path(args.comments_file).read_text("utf-8"))
        if isinstance(raw, list):
            all_comments.extend(raw)
    else:
        for url in note_urls:
            print(f"🔍 采集评论: {url[:60]}...")
            raw = fetch_comments_via_cli(url, cookie_string)
            print(f"   获取 {len(raw)} 条原始评论")
            all_comments.extend(raw)
            time.sleep(1)  # 避免频繁请求

    if not all_comments:
        print("⚠️ 没有评论数据")
        return

    # 处理评论：根评论 + 子评论合并
    processed = process_comments(all_comments)
    print(f"📊 处理后: {len(processed)} 条根评论")

    # 获取已存在的评论 ID（去重）
    if not args.dry_run:
        existing_ids = get_existing_comment_ids(token, base_token, table_id)
        print(f"📋 飞书已有 {len(existing_ids)} 条评论记录")
    else:
        existing_ids = set()

    # 构建 Feishu 记录
    to_write = []
    skipped = 0
    for c in processed:
        if c["comment_id"] in existing_ids:
            skipped += 1
            continue
        note_id = c["note_id"]
        title = note_titles.get(note_id, "")
        # 如果笔记表中没有标题，尝试从 URL 推断
        if not title and note_urls:
            for url in note_urls:
                if note_id in url:
                    title = f"笔记 {note_id[:8]}..."
                    break
        record = build_feishu_record(c, title, note_urls[0] if note_urls else "")
        to_write.append(record)

    print(f"📝 待写入: {len(to_write)} 条, 跳过重复: {skipped} 条")

    if args.dry_run:
        print("\n=== DRY RUN ===")
        for r in to_write[:5]:
            print(f"  [{r['笔记标题']}] {r['评论者昵称']}: {r['评论内容'][:50]}")
        if len(to_write) > 5:
            print(f"  ... 还有 {len(to_write) - 5} 条")
        return

    # 写入飞书
    if to_write:
        written = batch_write(token, base_token, table_id, to_write)
        print(f"\n🎉 完成！写入 {written} 条评论到飞书")
    else:
        print("\n✅ 无新评论需要写入")


if __name__ == "__main__":
    main()
