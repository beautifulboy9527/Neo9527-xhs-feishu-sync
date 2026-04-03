#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from xhs_comment_config import load_config, pick

# ⚠️ 不要硬编码 token，从 config.json 或参数读取
DEFAULT_NOTES_TABLE_ID = "tbll9Qr2IwBHaN9n"
FIELD_ALIASES = {
    "comment_uid": ["评论ID", "评论UID", "评论唯一ID"],
    "user_name": ["评论用户", "用户", "用户名"],
    "content": ["评论正文", "评论内容", "内容"],
    "time": ["评论时间", "时间", "通知时间"],
    "note_id": ["帖子ID", "笔记ID", "noteId"],
    "note_url": ["帖子链接", "笔记链接", "链接"],
    "note_record_id": ["对标笔记记录ID", "关联对标笔记ID"],
    "risk_flags": ["风险标签", "风险关键词", "命中关键词"],
    "risk_level": ["风险等级", "风险级别"],
    "raw_json": ["原始数据", "原始JSON"],
}


def cli() -> str:
    return shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"


def run_json(args: List[str]) -> Dict[str, Any]:
    p = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout)
    return json.loads(p.stdout)


def resolve_existing_field_map(base: str, table: str) -> Dict[str, str]:
    data = run_json(
        [
            cli(),
            "base",
            "+field-list",
            "--base-token",
            base,
            "--table-id",
            table,
            "--as",
            "user",
        ]
    ).get("data", {})
    names = [str(x.get("field_name") or x.get("name") or "") for x in data.get("items", [])]
    out: Dict[str, str] = {}
    existing = set(names)
    for k, aliases in FIELD_ALIASES.items():
        candidates = [k, *aliases]
        hit = next((n for n in candidates if n in existing), "")
        if hit:
            out[k] = hit
    return out


def list_rows(base: str, table: str, field_map: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    uid_field = field_map.get("comment_uid", "comment_uid")
    offset = 0
    limit = 200
    while True:
        data = run_json(
            [
                cli(),
                "base",
                "+record-list",
                "--base-token",
                base,
                "--table-id",
                table,
                "--limit",
                str(limit),
                "--offset",
                str(offset),
                "--as",
                "user",
            ]
        ).get("data", {})
        fields = data.get("fields", [])
        rows = data.get("data", [])
        rids = data.get("record_id_list", [])
        if uid_field not in fields:
            break
        idx = fields.index(uid_field)
        for i, row in enumerate(rows):
            if i < len(rids) and isinstance(row, list) and idx < len(row):
                uid = str(row[idx] or "").strip()
                if uid:
                    out[uid] = str(rids[i])
        if len(rows) < limit:
            break
        offset += limit
    return out


def risk_level(flags: List[str]) -> str:
    if not flags:
        return "low"
    if any(x in ("骗", "返现", "代运营") for x in flags):
        return "high"
    return "medium"


def upsert(base: str, table: str, record: Dict[str, Any], record_id: str = "") -> bool:
    payload_path = Path.cwd() / ".tmp_comment_upsert.json"
    payload_path.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        cmd = [
            cli(),
            "base",
            "+record-upsert",
            "--base-token",
            base,
            "--table-id",
            table,
            "--as",
            "user",
            "--json",
            "@./.tmp_comment_upsert.json",
        ]
        if record_id:
            cmd.extend(["--record-id", record_id])
        res = run_json(cmd)
        return bool(res.get("ok"))
    finally:
        payload_path.unlink(missing_ok=True)


def map_record_keys(record: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
    mapped: Dict[str, Any] = {}
    for k, v in record.items():
        mapped[field_map.get(k, k)] = v
    return mapped


def build_note_url_to_record_id(base: str, notes_table: str) -> Dict[str, str]:
    """Build a map from note_url to record_id for linking comments to notes."""
    data = run_json(
        [
            cli(),
            "base",
            "+record-list",
            "--base-token",
            base,
            "--table-id",
            notes_table,
            "--limit",
            "500",
            "--as",
            "user",
        ]
    ).get("data", {})
    fields = data.get("fields", [])
    rows = data.get("data", [])
    rids = data.get("record_id_list", [])
    
    # 优先级：note_url 字段 > 中文别名
    url_candidates = ("note_url", "对标笔记链接", "笔记链接", "帖子链接")
    url_field = next((x for x in url_candidates if x in fields), "")
    if not url_field:
        return {}
    idx = fields.index(url_field)
    out: Dict[str, str] = {}
    for i, row in enumerate(rows):
        if i >= len(rids) or not isinstance(row, list) or idx >= len(row):
            continue
        u = str(row[idx] or "").strip()
        if u:
            # 清理 URL 中的查询参数（xsec_token 等），提高匹配成功率
            clean_url = u.split("?")[0] if "?" in u else u
            out[clean_url] = str(rids[i])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="把 mentions.json 同步到飞书评论通知表")
    ap.add_argument("--config", default="")
    ap.add_argument("--base-token", default="")
    ap.add_argument("--table-id", default="")
    ap.add_argument("--notes-table-id", default=DEFAULT_NOTES_TABLE_ID)
    ap.add_argument("--mentions", required=True)
    ap.add_argument("--limit", type=int, default=5, help="最多同步多少条，默认 5")
    args = ap.parse_args()

    cfg = load_config(args.config.strip() or None)
    base = pick(args.base_token, cfg, "base_token", "XHS_COMMENT_BASE_TOKEN", DEFAULT_BASE)
    table = args.table_id.strip() or cfg.get("comment_table_id", "").strip()
    if not table:
        raise ValueError("missing table id: pass --table-id or set comment_table_id in config.json")

    data = json.loads(Path(args.mentions).read_text(encoding="utf-8"))
    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise ValueError("mentions file invalid: items must be list")
    if args.limit > 0:
        items = items[: args.limit]

    field_map = resolve_existing_field_map(base, table)
    existing = list_rows(base, table, field_map)
    note_url_map = build_note_url_to_record_id(base, args.notes_table_id.strip())
    add_ok = 0
    upd_ok = 0
    for x in items:
        if not isinstance(x, dict):
            continue
        uid = str(x.get("comment_uid") or "").strip()
        if not uid:
            continue
        flags = x.get("risk_flags", [])
        flags_text = ",".join([str(i) for i in flags]) if isinstance(flags, list) else str(flags)
        rec = {
            "comment_uid": uid,
            "user_name": str(x.get("user_name") or ""),
            "content": str(x.get("content") or ""),
            "time": str(x.get("time") or ""),
            "note_id": str(x.get("note_id") or ""),
            "note_url": str(x.get("note_url") or ""),
            "note_record_id": note_url_map.get(str(x.get("note_url") or "").strip(), ""),
            "risk_flags": flags_text,
            "risk_level": risk_level(flags if isinstance(flags, list) else []),
            "raw_json": json.dumps(x.get("raw", {}), ensure_ascii=False),
        }
        rid = existing.get(uid, "")
        ok = upsert(base, table, map_record_keys(rec, field_map), rid)
        if ok and rid:
            upd_ok += 1
        elif ok:
            add_ok += 1

    print(
        json.dumps(
            {
                "ok": True,
                "add_ok": add_ok,
                "update_ok": upd_ok,
                "total_input": len(items),
                "field_map": field_map,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
