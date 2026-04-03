#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from typing import Any, Dict, List

from xhs_comment_config import load_config, pick

# ⚠️ 不要硬编码 token，从 config.json 或参数读取
DEFAULT_TABLE_NAME = "评论通知"
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


def list_tables(base_token: str) -> List[Dict[str, Any]]:
    data = run_json([cli(), "base", "+table-list", "--base-token", base_token, "--as", "user"])
    return list(data.get("data", {}).get("items", []))


def ensure_table(base_token: str, table_name: str) -> str:
    for t in list_tables(base_token):
        if str(t.get("table_name") or t.get("name") or "") == table_name:
            tid = str(t.get("table_id") or t.get("id") or "").strip()
            if tid:
                return tid
    c = run_json(
        [
            cli(),
            "base",
            "+table-create",
            "--base-token",
            base_token,
            "--name",
            table_name,
            "--as",
            "user",
        ]
    )
    data = c.get("data", {}) if isinstance(c, dict) else {}
    tid = str(
        (data.get("table", {}) or {}).get("table_id")
        or data.get("table_id")
        or ""
    ).strip()
    if not tid:
        # Some lark-cli versions don't return created id reliably; re-list by name.
        for t in list_tables(base_token):
            if str(t.get("table_name") or t.get("name") or "") == table_name:
                tid = str(t.get("table_id") or t.get("id") or "").strip()
                if tid:
                    return tid
        raise RuntimeError("cannot resolve table_id after create")
    return tid


def field_names(base_token: str, table_id: str) -> List[str]:
    data = run_json(
        [
            cli(),
            "base",
            "+field-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--as",
            "user",
        ]
    )
    return [str(x.get("field_name") or x.get("name") or "") for x in data.get("data", {}).get("items", [])]


def create_field(base_token: str, table_id: str, payload: Dict[str, Any]) -> None:
    last_err = ""
    for i in range(5):
        try:
            run_json(
                [
                    cli(),
                    "base",
                    "+field-create",
                    "--base-token",
                    base_token,
                    "--table-id",
                    table_id,
                    "--as",
                    "user",
                    "--json",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ]
            )
            return
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(1.2 + i * 0.8)
    raise RuntimeError(last_err or f"create field failed: {payload.get('field_name')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="创建/修复评论通知表结构")
    ap.add_argument("--config", default="")
    ap.add_argument("--base-token", default="")
    ap.add_argument("--table-id", default="")
    ap.add_argument("--table-name", default="")
    args = ap.parse_args()

    cfg = load_config(args.config.strip() or None)
    base = pick(args.base_token, cfg, "base_token", "XHS_COMMENT_BASE_TOKEN", DEFAULT_BASE)
    table_name = pick(args.table_name, cfg, "comment_table_name", "XHS_COMMENT_TABLE_NAME", DEFAULT_TABLE_NAME)
    table_id = args.table_id.strip() or cfg.get("comment_table_id", "").strip()
    if not table_id:
        table_id = ensure_table(base, table_name)

    names = set(field_names(base, table_id))
    wanted = [
        "评论ID",
        "评论用户",
        "评论正文",
        "评论时间",
        "帖子ID",
        "帖子链接",
        "对标笔记记录ID",
        "风险标签",
        "风险等级",
        "原始数据",
    ]
    alias_hits: Dict[str, str] = {}
    for k, aliases in FIELD_ALIASES.items():
        hit = next((x for x in aliases if x in names), "")
        if hit:
            alias_hits[k] = hit

    created: List[str] = []
    for n in wanted:
        if n not in names:
            create_field(base, table_id, {"field_name": n, "type": "text"})
            created.append(n)

    print(
        json.dumps(
            {
                "ok": True,
                "base_token": base,
                "table_id": table_id,
                "table_name": table_name,
                "created_fields": created,
                "alias_hits": alias_hits,
                "note": "alias_hits 表示已存在的中文/历史字段，脚本不会删除它们；同步脚本会自动优先映射到已存在字段名。",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
