#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

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


def lark_api(method: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    cmd = [resolve_bin("lark-cli"), "api", method, path, "--data", json.dumps(body, ensure_ascii=False, separators=(",", ":"))]
    raw = run_cmd(cmd)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 返回非 JSON: {raw[:500]}") from exc


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
    raw = run_cmd(cmd)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"+record-list 返回非 JSON: {raw[:500]}") from exc


def extract_existing_map(
    base_token: str, table_id: str, field_name: str, field_text_key: str
) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    limit = 100
    offset = 0
    while True:
        resp = lark_record_list(base_token, table_id, limit=limit, offset=offset)
        payload = resp.get("data", {})
        fields = payload.get("fields", [])
        rows = payload.get("data", [])
        record_ids = payload.get("record_id_list", [])
        if field_name not in fields:
            break
        key_idx = fields.index(field_name)
        for idx, row in enumerate(rows):
            if idx >= len(record_ids):
                continue
            record_id = str(record_ids[idx])
            if not isinstance(row, list) or key_idx >= len(row):
                continue
            value = row[key_idx]
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    text = first.get(field_text_key, "")
                    if text:
                        mapping[str(text)] = record_id
                elif isinstance(first, str):
                    mapping[first] = record_id
            elif isinstance(value, str):
                mapping[value] = record_id
        if len(rows) < limit:
            break
        offset += limit
    return mapping


def split_records(
    records: List[Dict[str, Any]], key: str, existing_map: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    add_list: List[Dict[str, Any]] = []
    update_list: List[Dict[str, Any]] = []
    for r in records:
        rid = str(r.get(key, ""))
        if not rid:
            continue
        if rid in existing_map:
            with_record_id = dict(r)
            with_record_id["record_id"] = existing_map[rid]
            update_list.append(with_record_id)
        else:
            add_list.append(r)
    return add_list, update_list


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书去重分流（新增/更新）")
    parser.add_argument("--notes", default="")
    parser.add_argument("--authors", default="")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--mode", choices=["notes", "authors", "both"], default="both")
    parser.add_argument(
        "--config",
        default="",
        help="config.json 路径；留空则用 XHS_FEISHU_CONFIG 或技能目录下 config.json",
    )
    parser.add_argument("--base-token", default="", help="覆盖配置文件与环境变量")
    parser.add_argument("--notes-table-id", default="")
    parser.add_argument("--authors-table-id", default="")
    parser.add_argument("--notes-key-field", default="note_id")
    parser.add_argument("--authors-key-field", default="user_id")
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

    notes: List[Dict[str, Any]] = []
    authors: List[Dict[str, Any]] = []
    if args.mode in ("notes", "both"):
        if not args.notes:
            raise ValueError("notes 模式必须提供 --notes 文件")
        notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    if args.mode in ("authors", "both"):
        if not args.authors:
            raise ValueError("authors 模式必须提供 --authors 文件")
        authors = json.loads(Path(args.authors).read_text(encoding="utf-8"))

    existing_note_map: Dict[str, str] = {}
    existing_user_map: Dict[str, str] = {}
    if args.mode in ("notes", "both"):
        existing_note_map = extract_existing_map(args.base_token, args.notes_table_id, args.notes_key_field, "text")
    if args.mode in ("authors", "both"):
        existing_user_map = extract_existing_map(args.base_token, args.authors_table_id, args.authors_key_field, "text")

    notes_add, notes_update = split_records(notes, "note_id", existing_note_map) if notes else ([], [])
    authors_add, authors_update = split_records(authors, "user_id", existing_user_map) if authors else ([], [])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "base_token": args.base_token,
        "notes_table_id": args.notes_table_id,
        "authors_table_id": args.authors_table_id,
        "mode": args.mode,
        "notes_add": notes_add,
        "notes_update": notes_update,
        "authors_add": authors_add,
        "authors_update": authors_update,
        "summary": {
            "notes_total": len(notes),
            "authors_total": len(authors),
            "notes_add": len(notes_add),
            "notes_update": len(notes_update),
            "authors_add": len(authors_add),
            "authors_update": len(authors_update),
        },
    }
    plan_path = out_dir / "sync_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "plan": str(plan_path), "summary": plan["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
