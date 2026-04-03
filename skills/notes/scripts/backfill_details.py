#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from xhs_skill_config import load_feishu_user_config, pick_str

DEFAULT_BASE_TOKEN = "Ro3EbZ5vLaXCljs651kc8j8Lndh"
DEFAULT_NOTES_TABLE_ID = "tbll9Qr2IwBHaN9n"
DEFAULT_AUTHORS_TABLE_ID = "tblAINJGFWrPUTwK"


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


def lark_record_list(base_token: str, table_id: str, limit: int, offset: int) -> Dict[str, Any]:
    cmd = [
        resolve_bin("lark-cli"),
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--limit",
        str(limit),
        "--offset",
        str(offset),
        "--as",
        "user",
    ]
    return json.loads(run_cmd(cmd))


def lark_record_upsert(base_token: str, table_id: str, record_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    payload_name = f".xhs_backfill_{uuid.uuid4().hex}.json"
    payload_path = Path.cwd() / payload_name
    payload_path.write_text(json.dumps(fields, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        cmd = [
            resolve_bin("lark-cli"),
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--record-id",
            record_id,
            "--json",
            f"@./{payload_name}",
            "--as",
            "user",
        ]
        return json.loads(run_cmd(cmd))
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except OSError:
            pass


def as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        vals: List[str] = []
        for x in v:
            if isinstance(x, dict):
                vals.append(str(x.get("text") or x.get("name") or x.get("value") or ""))
            else:
                vals.append(str(x))
        return "、".join([x for x in vals if x])
    return str(v)


def load_table_rows(base_token: str, table_id: str) -> Tuple[List[str], List[List[Any]], List[str]]:
    all_rows: List[List[Any]] = []
    all_rids: List[str] = []
    fields: List[str] = []
    offset = 0
    limit = 200
    while True:
        resp = lark_record_list(base_token, table_id, limit, offset).get("data", {})
        if not fields:
            fields = [str(x) for x in resp.get("fields", [])]
        rows = resp.get("data", []) or []
        rids = resp.get("record_id_list", []) or []
        all_rows.extend(rows)
        all_rids.extend([str(x) for x in rids])
        if len(rows) < limit:
            break
        offset += limit
    return fields, all_rows, all_rids


def row_to_dict(fields: List[str], row: List[Any]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for i, f in enumerate(fields):
        if i < len(row):
            data[f] = row[i]
    return data


def normalize_author_likes(v: str) -> str:
    text = (v or "").strip()
    if not text:
        return ""
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return str(int(parts[0]) + int(parts[1]))
    return text


def build_note_details(rec: Dict[str, Any]) -> Dict[str, Any]:
    tags_raw = rec.get("标签", "")
    tags = [x.strip() for x in as_text(tags_raw).replace("，", "、").split("、") if x.strip()]
    return {
        "note_id": as_text(rec.get("note_id")),
        "note_url": as_text(rec.get("note_url")),
        "user_id": "",
        "author_name": as_text(rec.get("作者名字")),
        "author_profile_url": as_text(rec.get("博主主页url")),
        "title": as_text(rec.get("标题")),
        "liked_count": as_text(rec.get("点赞数")),
        "collected_count": as_text(rec.get("收藏数")),
        "comment_count": as_text(rec.get("评论数")),
        "share_count": "",
        "note_desc": as_text(rec.get("文案")),
        "note_tags": tags,
        "note_type": as_text(rec.get("类型")),
        "note_cover_url_default": as_text(rec.get("图片")),
        "raw": {},
    }


def build_author_details(rec: Dict[str, Any]) -> Dict[str, Any]:
    likes = normalize_author_likes(as_text(rec.get("赞藏数")))
    profile_url = as_text(rec.get("主页链接"))
    user_id = as_text(rec.get("user_id"))
    if not profile_url and user_id:
        profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    return {
        "user_id": user_id,
        "nick_name": as_text(rec.get("账号昵称")),
        "fans": as_text(rec.get("粉丝数")),
        "follows": "",
        "desc": as_text(rec.get("简介")),
        "profile_url": profile_url,
        "avatar": as_text(rec.get("头像链接")),
        "latest_note_title": as_text(rec.get("帖子标题")),
        "latest_note_time": as_text(rec.get("更新时间")),
        "likes_and_collects": likes,
        "interaction": likes,
        "raw": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回刷飞书历史记录 details 与关键字段")
    parser.add_argument(
        "--config",
        default="",
        help="config.json；留空则用 XHS_FEISHU_CONFIG 或技能目录 config.json",
    )
    parser.add_argument("--base-token", default="")
    parser.add_argument("--notes-table-id", default="")
    parser.add_argument("--authors-table-id", default="")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 条，0 表示全量")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_feishu_user_config(args.config.strip() or None)
    args.base_token = pick_str(
        args.base_token, cfg, "base_token", "XHS_FEISHU_BASE_TOKEN", DEFAULT_BASE_TOKEN
    )
    args.notes_table_id = pick_str(
        args.notes_table_id,
        cfg,
        "notes_table_id",
        "XHS_FEISHU_NOTES_TABLE_ID",
        DEFAULT_NOTES_TABLE_ID,
    )
    args.authors_table_id = pick_str(
        args.authors_table_id,
        cfg,
        "authors_table_id",
        "XHS_FEISHU_AUTHORS_TABLE_ID",
        DEFAULT_AUTHORS_TABLE_ID,
    )

    summary = {
        "notes_total": 0,
        "notes_updated": 0,
        "authors_total": 0,
        "authors_updated": 0,
        "errors": [],
    }

    n_fields, n_rows, n_rids = load_table_rows(args.base_token, args.notes_table_id)
    a_fields, a_rows, a_rids = load_table_rows(args.base_token, args.authors_table_id)
    if args.limit > 0:
        n_rows, n_rids = n_rows[: args.limit], n_rids[: args.limit]
        a_rows, a_rids = a_rows[: args.limit], a_rids[: args.limit]

    summary["notes_total"] = len(n_rows)
    for row, rid in zip(n_rows, n_rids):
        rec = row_to_dict(n_fields, row if isinstance(row, list) else [])
        details = build_note_details(rec)
        fields = {
            "details": json.dumps(details, ensure_ascii=False, separators=(",", ":")),
            "作者名字": details["author_name"],
            "博主主页url": details["author_profile_url"],
            "文案": details["note_desc"],
            "标签": "、".join(details["note_tags"]),
            "图片": details["note_cover_url_default"],
        }
        if args.dry_run:
            summary["notes_updated"] += 1
            continue
        try:
            resp = lark_record_upsert(args.base_token, args.notes_table_id, rid, fields)
            if resp.get("ok", False):
                summary["notes_updated"] += 1
            else:
                summary["errors"].append({"table": "notes", "record_id": rid, "resp": resp})
        except Exception as exc:
            summary["errors"].append({"table": "notes", "record_id": rid, "error": str(exc)})

    summary["authors_total"] = len(a_rows)
    for row, rid in zip(a_rows, a_rids):
        rec = row_to_dict(a_fields, row if isinstance(row, list) else [])
        details = build_author_details(rec)
        fields = {
            "details": json.dumps(details, ensure_ascii=False, separators=(",", ":")),
            "赞藏数": details["likes_and_collects"],
            "主页链接": details["profile_url"],
        }
        if args.dry_run:
            summary["authors_updated"] += 1
            continue
        try:
            resp = lark_record_upsert(args.base_token, args.authors_table_id, rid, fields)
            if resp.get("ok", False):
                summary["authors_updated"] += 1
            else:
                summary["errors"].append({"table": "authors", "record_id": rid, "resp": resp})
        except Exception as exc:
            summary["errors"].append({"table": "authors", "record_id": rid, "error": str(exc)})

    print(json.dumps({"ok": len(summary["errors"]) == 0, "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
