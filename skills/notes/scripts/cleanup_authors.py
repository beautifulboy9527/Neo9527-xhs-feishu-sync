#!/usr/bin/env python3
import argparse
import json
import subprocess
import shutil
from typing import Dict, List, Tuple


def resolve_bin(name: str) -> str:
    return shutil.which(name) or shutil.which(f"{name}.cmd") or name


def run_cmd(cmd: List[str]) -> str:
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout


def list_records(base: str, table: str, limit: int, offset: int) -> Dict:
    cmd = [
        resolve_bin("lark-cli"),
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
    return json.loads(run_cmd(cmd)).get("data", {})


def is_dirty(row: List, idx: Dict[str, int]) -> Tuple[bool, List[str], str]:
    user_id = str(row[idx["user_id"]]) if "user_id" in idx and idx["user_id"] < len(row) else ""
    profile_url = str(row[idx["主页链接"]]) if "主页链接" in idx and idx["主页链接"] < len(row) else ""
    details = str(row[idx["details"]]) if "details" in idx and idx["details"] < len(row) else ""

    reasons: List[str] = []
    if not user_id or user_id == "None":
        reasons.append("missing_user_id")
    if not profile_url or profile_url == "None":
        reasons.append("missing_profile_url")
    if user_id and profile_url and user_id not in profile_url:
        reasons.append("user_id_profile_mismatch")

    if details and details != "None":
        try:
            obj = json.loads(details)
            nested_uid = ""
            if isinstance(obj, dict):
                nested_uid = str(obj.get("user_id") or "")
                raw = obj.get("raw", {})
                if not nested_uid and isinstance(raw, dict):
                    note_card = raw.get("note_card", {})
                    if isinstance(note_card, dict):
                        user = note_card.get("user", {})
                        if isinstance(user, dict):
                            nested_uid = str(user.get("user_id") or "")
            if nested_uid and user_id and nested_uid != user_id:
                reasons.append("details_user_id_mismatch")
        except Exception:
            reasons.append("details_invalid_json")

    return len(reasons) > 0, reasons, user_id


def main() -> None:
    parser = argparse.ArgumentParser(description="清理博主表脏数据")
    parser.add_argument("--base-token", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    offset = 0
    limit = 200
    dirty: List[Tuple[str, str, List[str]]] = []
    total = 0
    while True:
        payload = list_records(args.base_token, args.table_id, limit, offset)
        rows = payload.get("data", [])
        fields = payload.get("fields", [])
        record_ids = payload.get("record_id_list", [])
        if not rows:
            break
        idx = {k: i for i, k in enumerate(fields)}
        for i, row in enumerate(rows):
            total += 1
            rid = record_ids[i] if i < len(record_ids) else ""
            bad, reasons, uid = is_dirty(row, idx)
            if bad and rid:
                dirty.append((rid, uid, reasons))
        if len(rows) < limit:
            break
        offset += limit

    deleted = 0
    if args.execute:
        for rid, _, _ in dirty:
            cmd = [
                resolve_bin("lark-cli"),
                "base",
                "+record-delete",
                "--base-token",
                args.base_token,
                "--table-id",
                args.table_id,
                "--record-id",
                rid,
                "--yes",
                "--as",
                "user",
            ]
            run_cmd(cmd)
            deleted += 1

    print(
        json.dumps(
            {
                "total_records": total,
                "dirty_records": len(dirty),
                "deleted_records": deleted,
                "sample_dirty": dirty[:10],
                "executed": args.execute,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
