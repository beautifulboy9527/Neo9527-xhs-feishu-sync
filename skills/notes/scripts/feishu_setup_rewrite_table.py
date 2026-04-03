#!/usr/bin/env python3
"""
Idempotent: add linkage + formula columns on a rewrite/analysis table (optional workflow).
Requires: lark-cli logged in, base:field:create / update scopes.

Override with env: XHS_FEISHU_BASE_TOKEN, XHS_FEISHU_REWRITE_TABLE_ID, XHS_FEISHU_NOTES_TABLE_ID
"""
import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Set

from xhs_skill_config import load_feishu_user_config, pick_str

DEFAULT_BASE = "Ro3EbZ5vLaXCljs651kc8j8Lndh"
DEFAULT_REWRITE = "tblvslt9i2FcVvYP"
DEFAULT_NOTES = "tbll9Qr2IwBHaN9n"
BASE = DEFAULT_BASE
REWRITE_TABLE = DEFAULT_REWRITE
NOTES_TABLE = DEFAULT_NOTES


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


def field_names() -> Set[str]:
    data = run_json(
        [
            cli(),
            "base",
            "+field-list",
            "--base-token",
            BASE,
            "--table-id",
            REWRITE_TABLE,
            "--as",
            "user",
        ]
    )
    items = data.get("data", {}).get("items", [])
    return {str(x.get("field_name") or x.get("name")) for x in items}


def field_list_items() -> List[Dict[str, Any]]:
    data = run_json(
        [
            cli(),
            "base",
            "+field-list",
            "--base-token",
            BASE,
            "--table-id",
            REWRITE_TABLE,
            "--as",
            "user",
        ]
    )
    return list(data.get("data", {}).get("items", []))


def field_id_by_name(name: str) -> str:
    for x in field_list_items():
        n = str(x.get("field_name") or x.get("name") or "")
        if n == name:
            return str(x.get("field_id") or x.get("id"))
    raise KeyError(name)



def create_field(payload: Dict[str, Any]) -> None:
    run_json(
        [
            cli(),
            "base",
            "+field-create",
            "--base-token",
            BASE,
            "--table-id",
            REWRITE_TABLE,
            "--as",
            "user",
            "--json",
            json.dumps(payload, separators=(",", ":")),
        ]
    )


def create_formula(name: str, expression: str) -> None:
    run_json(
        [
            cli(),
            "base",
            "+field-create",
            "--base-token",
            BASE,
            "--table-id",
            REWRITE_TABLE,
            "--as",
            "user",
            "--i-have-read-guide",
            "--json",
            json.dumps({"field_name": name, "type": "formula", "expression": expression}, separators=(",", ":")),
        ]
    )


def update_formula(field_id: str, field_display_name: str, expression: str) -> None:
    run_json(
        [
            cli(),
            "base",
            "+field-update",
            "--base-token",
            BASE,
            "--table-id",
            REWRITE_TABLE,
            "--field-id",
            field_id,
            "--as",
            "user",
            "--i-have-read-guide",
            "--json",
            json.dumps(
                {"type": "formula", "name": field_display_name, "expression": expression},
                separators=(",", ":"),
            ),
        ]
    )


def main() -> None:
    apply_runtime_config()
    names = field_names()
    if "note_id" not in names:
        create_field({"field_name": "note_id", "type": "text"})
    if "note_url" not in names and "对标笔记链接" not in names:
        create_field({"field_name": "note_url", "type": "text"})
    if "关联对标笔记" not in names:
        create_field({"field_name": "关联对标笔记", "type": "link", "link_table": NOTES_TABLE})

    fid_note = field_id_by_name("note_id")
    fid_title = field_id_by_name("笔记标题")
    fid_body = field_id_by_name("笔记文案")
    # 飞书公式不能引用「排在公式列右侧」的字段；当前表里「对标笔记链接」常在「仿写输入」右侧，
    # 若把 url 写进 CONCATENATE 会导致整列为空。URL 请用独立列「对标笔记链接」或把「仿写输入」列拖到最右侧后再自行加 url 段。
    expr = (
        f'CONCATENATE("note_id:",{{{fid_note}}},CHAR(10),'
        f'"title:",{{{fid_title}}},CHAR(10),'
        f'"body:",{{{fid_body}}})'
    )

    if "仿写输入" not in names:
        create_formula("仿写输入", expr)
    else:
        fid_formula = field_id_by_name("仿写输入")
        disp = next(
            (
                str(x.get("field_name") or x.get("name") or "仿写输入")
                for x in field_list_items()
                if str(x.get("field_id") or x.get("id")) == fid_formula
            ),
            "仿写输入",
        )
        update_formula(fid_formula, disp, expr)

    print(json.dumps({"ok": True, "message": "rewrite table fields ensured"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
