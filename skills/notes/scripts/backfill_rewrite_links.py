#!/usr/bin/env python3
"""
按 note_id 将仿写表「关联对标笔记」指向笔记表记录；可选补全 URL 文本列。
依赖：lark-cli 已登录。

Override with env: XHS_FEISHU_BASE_TOKEN, XHS_FEISHU_REWRITE_TABLE_ID, XHS_FEISHU_NOTES_TABLE_ID
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from xhs_skill_config import load_feishu_user_config, pick_str

DEFAULT_BASE = "Ro3EbZ5vLaXCljs651kc8j8Lndh"
DEFAULT_REWRITE = "tblvslt9i2FcVvYP"
DEFAULT_NOTES = "tbll9Qr2IwBHaN9n"
BASE = DEFAULT_BASE
REWRITE_TABLE = DEFAULT_REWRITE
NOTES_TABLE = DEFAULT_NOTES
PAGE_SIZE = 500


def apply_runtime_config() -> None:
    global BASE, REWRITE_TABLE, NOTES_TABLE
    cfg = load_feishu_user_config(None)
    BASE = pick_str("", cfg, "base_token", "XHS_FEISHU_BASE_TOKEN", DEFAULT_BASE)
    NOTES_TABLE = pick_str("", cfg, "notes_table_id", "XHS_FEISHU_NOTES_TABLE_ID", DEFAULT_NOTES)
    REWRITE_TABLE = (
        os.environ.get("XHS_FEISHU_REWRITE_TABLE_ID", "").strip()
        or (cfg.get("rewrite_table_id") or "").strip()
        or DEFAULT_REWRITE
    )


def cli() -> str:
    return shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"


def run_json(args: List[str]) -> Dict[str, Any]:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def field_id_by_name(table_id: str, name: str) -> str:
    data = run_json(
        [
            cli(),
            "base",
            "+field-list",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            "--as",
            "user",
        ]
    )
    for x in data.get("data", {}).get("items", []):
        n = str(x.get("field_name") or x.get("name") or "")
        if n == name:
            return str(x.get("field_id") or x.get("id"))
    raise KeyError(name)


def list_records_page(table_id: str, offset: int) -> Dict[str, Any]:
    return run_json(
        [
            cli(),
            "base",
            "+record-list",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            "--limit",
            str(PAGE_SIZE),
            "--offset",
            str(offset),
            "--as",
            "user",
        ]
    )


def collect_all_rows(table_id: str) -> Tuple[List[str], List[str], List[List[Any]]]:
    """返回 (fields 列名, record_id_list, data 行)."""
    offset = 0
    all_fields: Optional[List[str]] = None
    all_ids: List[str] = []
    all_data: List[List[Any]] = []
    while True:
        chunk = list_records_page(table_id, offset)
        inner = chunk.get("data", {})
        fields = inner.get("fields") or []
        rids = inner.get("record_id_list") or []
        rows = inner.get("data") or []
        if all_fields is None:
            all_fields = [str(x) for x in fields]
        elif fields and all_fields != [str(x) for x in fields]:
            raise RuntimeError("field order changed mid-pagination")
        all_ids.extend(rids)
        all_data.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    if all_fields is None:
        all_fields = []
    return all_fields, all_ids, all_data


def col_index(fields: List[str], name: str) -> int:
    try:
        return fields.index(name)
    except ValueError as e:
        raise KeyError(name) from e


def upsert_record(table_id: str, record_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return run_json(
        [
            cli(),
            "base",
            "+record-upsert",
            "--base-token",
            BASE,
            "--table-id",
            table_id,
            "--record-id",
            record_id,
            "--as",
            "user",
            "--json",
            json.dumps(payload, separators=(",", ":")),
        ]
    )


def main() -> None:
    fill_url = "--fill-url" in sys.argv
    apply_runtime_config()

    notes_fields, notes_rids, notes_rows = collect_all_rows(NOTES_TABLE)
    ni = col_index(notes_fields, "note_id")
    url_i = col_index(notes_fields, "note_url")

    note_to_rec: Dict[str, str] = {}
    note_to_url: Dict[str, str] = {}
    for row, rid in zip(notes_rows, notes_rids):
        if ni >= len(row):
            continue
        nid = row[ni]
        if not nid:
            continue
        s = str(nid).strip()
        note_to_rec[s] = rid
        if url_i < len(row) and row[url_i]:
            note_to_url[s] = str(row[url_i]).strip()

    rw_fields, rw_rids, rw_rows = collect_all_rows(REWRITE_TABLE)
    rw_note_i = col_index(rw_fields, "note_id")
    link_fid = field_id_by_name(REWRITE_TABLE, "关联对标笔记")

    url_col_name: Optional[str] = None
    url_fid: Optional[str] = None
    if fill_url:
        for cand in ("对标笔记链接", "note_url"):
            try:
                url_fid = field_id_by_name(REWRITE_TABLE, cand)
                url_col_name = cand
                break
            except KeyError:
                continue
        if not url_fid:
            fill_url = False

    linked = 0
    url_filled = 0
    skipped = 0
    missing_note = 0

    for row, rid in zip(rw_rows, rw_rids):
        nid: Optional[str] = None
        if rw_note_i < len(row) and row[rw_note_i]:
            nid = str(row[rw_note_i]).strip()
        if not nid:
            missing_note += 1
            continue
        target = note_to_rec.get(nid)
        if not target:
            skipped += 1
            continue
        payload: Dict[str, Any] = {link_fid: [target]}
        if fill_url and url_fid and url_col_name and nid in note_to_url:
            u = note_to_url[nid]
            ci = col_index(rw_fields, url_col_name)
            cur = row[ci] if ci < len(row) else None
            if not cur or not str(cur).strip():
                payload[url_fid] = u
                url_filled += 1
        upsert_record(REWRITE_TABLE, rid, payload)
        linked += 1

    print(
        json.dumps(
            {
                "ok": True,
                "linked_rows": linked,
                "url_filled": url_filled if fill_url else None,
                "rewrite_rows_missing_note_id": missing_note,
                "skipped_no_matching_note": skipped,
                "notes_distinct_ids": len(note_to_rec),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
