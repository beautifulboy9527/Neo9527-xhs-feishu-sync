#!/usr/bin/env python3
import argparse
import json
import tempfile
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple


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
        raise RuntimeError(
            f"命令失败: {' '.join(cmd)}\nstdout:\n{proc.stdout.strip()}\nstderr:\n{proc.stderr.strip()}"
        )
    return proc.stdout


def lark_api(method: str, path: str, body: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"code": 0, "msg": "dry-run", "data": {"request_path": path, "request_size": len(body.get("records", []))}}

    cmd = [
        resolve_bin("lark-cli"),
        "api",
        method,
        path,
        "--data",
        json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    ]
    raw = run_cmd(cmd)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"code": -1, "msg": "non-json-response", "raw": raw[:1000]}


def lark_record_upsert(
    base_token: str, table_id: str, record: Dict[str, Any], record_id: str, dry_run: bool
) -> Dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True}

    temp_json_name = f".xhs_feishu_upsert_{uuid.uuid4().hex}.json"
    temp_json_path = str(Path.cwd() / temp_json_name)
    with open(temp_json_path, "w", encoding="utf-8") as tf:
        tf.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))

    cmd = [
        resolve_bin("lark-cli"),
        "base",
        "+record-upsert",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--json",
        f"@./{temp_json_name}",
        "--as",
        "user",
    ]
    if record_id:
        cmd.extend(["--record-id", record_id])
    try:
        raw = run_cmd(cmd)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "raw": raw[:1000]}
    finally:
        try:
            os.remove(temp_json_path)
        except OSError:
            pass


def to_bitable_field(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return value


def normalize_author_likes(item: Dict[str, Any]) -> str:
    # 严格从博主自己的「获赞与收藏」字段读取，不 fallback 到笔记的点赞/收藏数
    direct = str(item.get("likes_and_collects", "") or "").strip()
    return direct


def to_create_record(item: Dict[str, Any]) -> Dict[str, Any]:
    fields = {k: to_bitable_field(v) for k, v in item.items() if k != "raw"}
    return {"fields": fields}


def to_update_record(item: Dict[str, Any], key_field: str) -> Dict[str, Any]:
    fields = {
        k: to_bitable_field(v)
        for k, v in item.items()
        if k not in ("raw", key_field, "record_id")
    }
    fields[key_field] = to_bitable_field(item.get(key_field, ""))
    return {"record_id": item.get("record_id", ""), "fields": fields}


def map_comment_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """将标准化评论数据映射为飞书评论表字段。"""
    raw_json = json.dumps(item.get("raw", {}), ensure_ascii=False, separators=(",", ":")) if item.get("raw") else ""
    mapped = {
        "评论ID": item.get("comment_id", ""),
        "笔记ID": item.get("note_id", ""),
        "评论内容": item.get("content", ""),
        "评论者昵称": item.get("user_nickname", ""),
        "评论者ID": item.get("user_id", ""),
        "是否贴主": "是" if item.get("is_author") else "否",
        "是否主评论": "是" if item.get("is_root") else "否",
        "父评论ID": item.get("parent_id", ""),
        "回复谁": item.get("reply_to_user", ""),
        "评论点赞数": item.get("liked_count", "0"),
        "子评论数": item.get("sub_comment_count", "0"),
        "IP属地": item.get("ip_location", ""),
        "评论时间": item.get("created_at", ""),
        "原始JSON": raw_json,
    }
    return {k: to_bitable_field(v) for k, v in mapped.items() if v not in (None, "")}


def map_fields(item: Dict[str, Any], key_field: str) -> Dict[str, Any]:
    raw_payload = item.get("raw", {})
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    if key_field == "note_id":
        tags_val = item.get("tags", [])
        if isinstance(tags_val, list):
            tags_text = "、".join([str(x) for x in tags_val if str(x).strip()])
        else:
            tags_text = str(tags_val or "")
        # 固定 details 结构，兼容飞书“信息提取”历史规则，避免因原始 raw 形态变化导致空列
        note_details = {
            "note_id": item.get("note_id", ""),
            "note_url": item.get("note_url", ""),
            "user_id": item.get("user_id", ""),
            "author_name": item.get("author_name", ""),
            "author_profile_url": item.get("author_profile_url", ""),
            "title": item.get("title", ""),
            "liked_count": item.get("liked_count", ""),
            "collected_count": item.get("collected_count", ""),
            "comment_count": item.get("comment_count", ""),
            "share_count": item.get("share_count", ""),
            "note_desc": item.get("note_desc", ""),
            "note_tags": item.get("tags", []),
            "note_type": item.get("note_type", ""),
            "note_cover_url_default": item.get("cover_url", ""),
            "raw": raw_payload,
        }
        raw_json = json.dumps(note_details, ensure_ascii=False, separators=(",", ":"))
        mapped = {
            "note_id": item.get("note_id", ""),
            "note_url": item.get("note_url", ""),
            "标题": item.get("title", ""),
            "图片": item.get("cover_url", ""),
            "点赞数": item.get("liked_count", ""),
            "评论数": item.get("comment_count", ""),
            "收藏数": item.get("collected_count", ""),
            "文案": item.get("note_desc", ""),
            "标签": tags_text,
            "作者名字": item.get("author_name", ""),
            "博主主页url": item.get("author_profile_url", ""),
            "类型": item.get("note_type", ""),
            "笔记发布时间": item.get("created_at", ""),
            "主键": item.get("note_id", ""),
            "details": raw_json,
        }
    else:
        author_details = dict(raw_payload)
        # 避免飞书“信息提取”错误抽取四元组，统一给出主页口径字段
        author_details.pop("engagement_snapshot", None)
        author_details["likes_and_collects"] = normalize_author_likes(item)
        author_details["interaction"] = normalize_author_likes(item)
        raw_json = json.dumps(author_details, ensure_ascii=False, separators=(",", ":"))
        profile_url = item.get("profile_url", "")
        if not profile_url and item.get("user_id"):
            profile_url = f"https://www.xiaohongshu.com/user/profile/{item.get('user_id','')}"
        mapped = {
            "user_id": item.get("user_id", ""),
            "账号昵称": item.get("nick_name", ""),
            "粉丝数": item.get("fans", ""),
            "简介": item.get("desc", ""),
            "主页链接": profile_url,
            "头像链接": item.get("avatar", ""),
            "帖子标题": item.get("latest_note_title", ""),
            "更新时间": item.get("latest_note_time", ""),
            "赞藏数": normalize_author_likes(item),
            "主键": item.get("user_id", ""),
            "details": raw_json,
        }
    return {k: to_bitable_field(v) for k, v in mapped.items() if v not in (None, "")}


def chunks(data: List[Dict[str, Any]], size: int = 500) -> List[List[Dict[str, Any]]]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def sync_table(
    base_token: str,
    table_id: str,
    add_items: List[Dict[str, Any]],
    update_items: List[Dict[str, Any]],
    key_field: str,
    dry_run: bool,
) -> Dict[str, Any]:
    results = {"add_ok": 0, "update_ok": 0, "errors": []}

    add_batches = chunks(add_items, 500)
    for batch in add_batches:
        for item in batch:
            rec_fields = map_fields(item, key_field)
            resp = lark_record_upsert(base_token, table_id, rec_fields, "", dry_run)
            if resp.get("ok", False):
                results["add_ok"] += 1
            else:
                results["errors"].append({"phase": "add", "response": resp, "item": item})

    update_batches = chunks(update_items, 500)
    for batch in update_batches:
        for item in batch:
            rec_fields = map_fields(item, key_field)
            resp = lark_record_upsert(
                base_token, table_id, rec_fields, item.get("record_id", ""), dry_run
            )
            if resp.get("ok", False):
                results["update_ok"] += 1
            else:
                results["errors"].append({"phase": "update", "response": resp, "item": item})

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="执行飞书同步计划（新增/更新）")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    base_token = plan["base_token"]
    notes_table_id = plan["notes_table_id"]
    authors_table_id = plan["authors_table_id"]
    mode = plan.get("mode", "both")

    notes_result = {"add_ok": 0, "update_ok": 0, "errors": []}
    authors_result = {"add_ok": 0, "update_ok": 0, "errors": []}
    if mode in ("notes", "both"):
        notes_result = sync_table(
            base_token=base_token,
            table_id=notes_table_id,
            add_items=plan.get("notes_add", []),
            update_items=plan.get("notes_update", []),
            key_field="note_id",
            dry_run=args.dry_run,
        )
    if mode in ("authors", "both"):
        authors_result = sync_table(
            base_token=base_token,
            table_id=authors_table_id,
            add_items=plan.get("authors_add", []),
            update_items=plan.get("authors_update", []),
            key_field="user_id",
            dry_run=args.dry_run,
        )

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "mode": mode,
        "notes": notes_result,
        "authors": authors_result,
        "comments": {"add_ok": 0, "update_ok": 0, "errors": []},
        "summary": {
            "notes_add_ok": notes_result["add_ok"],
            "notes_update_ok": notes_result["update_ok"],
            "authors_add_ok": authors_result["add_ok"],
            "authors_update_ok": authors_result["update_ok"],
        },
    }

    # Comments sync
    if mode == "comments":
        comments_table_id = plan.get("comments_table_id", "")
        if not comments_table_id:
            result["ok"] = False
            result["comments"]["errors"].append({"phase": "config", "msg": "comments_table_id not in plan"})
        else:
            comments_add = plan.get("comments_add", [])
            for c in comments_add:
                rec_fields = map_comment_fields(c)
                resp = lark_record_upsert(base_token, comments_table_id, rec_fields, "", dry_run)
                if resp.get("ok", False):
                    result["comments"]["add_ok"] += 1
                else:
                    result["comments"]["errors"].append({"phase": "add_comment", "response": resp})
            result["summary"]["comments_add_ok"] = result["comments"]["add_ok"]
    out_path = Path(args.plan).with_name("sync_result.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "result": str(out_path), "summary": result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
