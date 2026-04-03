#!/usr/bin/env python3
"""
Sync XHS comment data to Feishu Bitable using httpx (no lark-cli dependency).

Usage:
    python sync_to_feishu.py --mentions "../tmp/mentions.json"
    python sync_to_feishu.py --mentions "../tmp/mentions.json" --table-id "tblXXXX"

Reads feishu credentials from:
  1. CLI args --app-id / --app-secret
  2. Config file (config.json in parent dir)
  3. OpenClaw config (~/.openclaw/openclaw.json -> channels.feishu)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ─── Defaults ───────────────────────────────────────────────────────────────
# ⚠️ 不要硬编码 token/table_id，从 config.json 或参数读取
FEISHU_API = "https://open.feishu.cn/open-apis"

# ─── Feishu field mapping ──────────────────────────────────────────────────
# Maps JSON keys from fetch_mentions.py / redbook comment JSON -> Feishu field names
FIELD_MAP = {
    "comment_id": "评论ID",
    "comment_uid": "评论ID",
    "id": "评论ID",
    "user_name": "评论者昵称",
    "user_name_str": "评论者昵称",
    "nickname": "评论者昵称",
    "content": "评论内容",
    "content_str": "评论内容",
    "sub_comment_content": "评论内容",
    "time": "评论时间",
    "create_time": "评论时间",
    "create_time_str": "评论时间",
    "note_id": "笔记ID",
    "note_url": "笔记链接",
    "target_note_url": "笔记链接",
    "note_title": "笔记标题",
    "ip_location": "IP属地",
    "ip": "IP属地",
    "like_count": "评论点赞数",
    "sub_comment_count": "子评论数",
    "at_count": "子评论数",
    "user_id": "评论者ID",
    "user_id_str": "评论者ID",
    "is_author": "是否贴主",
    "is_sub_comment": "是否主评论",
    "parent_comment_id": "父评论ID",
    "root_comment_id": "根评论ID",
    "reply_to_user": "回复谁",
    "user_avatar": "用户头像",
    "user_homepage": "用户主页",
    "level": "层级",
    "risk_level": "风险等级",
}

RISK_KEYWORDS = ["骗", "返现", "代运营", "加微信", "免费送", "私信我"]


def load_feishu_creds() -> tuple[str, str]:
    """Load Feishu app credentials from OpenClaw config."""
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        ch = d.get("channels", {}).get("feishu", {})
        app_id = ch.get("appId", "")
        app_secret = ch.get("appSecret", "")
        if app_id and app_secret:
            return app_id, app_secret
    raise RuntimeError(
        "Feishu credentials not found. Set --app-id and --app-secret, "
        "or configure feishu channel in ~/.openclaw/openclaw.json"
    )


def get_tenant_token(client: httpx.Client, app_id: str, app_secret: str) -> str:
    """Get tenant_access_token from Feishu API."""
    resp = client.post(
        f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("tenant_access_token", "")
    if not token:
        raise RuntimeError(f"Failed to get token: {data}")
    return token


def api_request(
    client: httpx.Client,
    token: str,
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    """Make an authenticated Feishu API request."""
    url = f"{FEISHU_API}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if method == "GET":
        resp = client.get(url, headers=headers, params=body)
    else:
        resp = client.request(method, url, headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()


def list_existing_records(
    client: httpx.Client, token: str, base_token: str, table_id: str
) -> dict[str, str]:
    """Get all existing record IDs indexed by 评论ID for dedup."""
    comment_id_to_record: dict[str, str] = {}
    page_token = ""
    while True:
        body: dict[str, Any] = {"page_size": 500}
        if page_token:
            body["page_token"] = page_token
        data = api_request(
            client, token, "POST",
            f"/bitable/v1/apps/{base_token}/tables/{table_id}/records/search",
            body,
        ).get("data", {})
        # Use list instead of search if search not available
        if not data.get("items") and not data.get("total"):
            break
        items = data.get("items", [])
        for item in items:
            fields = item.get("fields", {})
            cid = fields.get("评论ID", "")
            if cid:
                comment_id_to_record[str(cid)] = item.get("record_id", "")
        page_token = data.get("page_token", "")
        if not page_token or not items:
            break
    return comment_id_to_record


def list_records_simple(
    client: httpx.Client, token: str, base_token: str, table_id: str
) -> dict[str, str]:
    """Simple list-all approach for getting existing 评论ID -> record_id map."""
    comment_id_to_record: dict[str, str] = {}
    page_token = ""
    while True:
        body: dict[str, Any] = {"page_size": 500}
        if page_token:
            body["page_token"] = page_token
        result = api_request(
            client, token, "GET",
            f"/bitable/v1/apps/{base_token}/tables/{table_id}/records",
            body,
        )
        data = result.get("data", {})
        items = data.get("items", [])
        for item in items:
            fields = item.get("fields", {})
            cid = fields.get("评论ID", "")
            if cid:
                comment_id_to_record[str(cid)] = item.get("record_id", "")
        page_token = data.get("page_token", "")
        if not page_token or not items:
            break
    return comment_id_to_record


def compute_risk_level(content: str) -> str:
    """Return risk level based on content keywords."""
    if not content:
        return "低"
    if any(kw in content for kw in RISK_KEYWORDS):
        return "高"
    return "低"


def normalize_comment(raw: dict) -> dict[str, Any]:
    """Normalize a raw comment JSON into a flat dict matching Feishu fields."""
    out: dict[str, Any] = {}

    # Direct mapping
    for json_key, feishu_field in FIELD_MAP.items():
        val = raw.get(json_key)
        if val is not None and val != "":
            out[feishu_field] = val

    # Handle special cases
    # is_author / is_sub_comment -> SingleSelect
    for field, true_val in [("is_author", "是"), ("is_sub_comment", "否")]:
        v = raw.get(field)
        if isinstance(v, bool):
            out[FIELD_MAP[field]] = true_val if v else "否"
        elif isinstance(v, (int, str)):
            out[FIELD_MAP[field]] = true_val if str(v) in ("1", "True", "true") else "否"

    # like_count / sub_comment_count -> Number (ensure numeric)
    for field in ["like_count", "sub_comment_count", "at_count"]:
        v = raw.get(field)
        if v is not None:
            try:
                out[FIELD_MAP[field]] = int(float(v))
            except (ValueError, TypeError):
                pass

    # level -> Number
    v = raw.get("level")
    if v is not None:
        try:
            out["层级"] = int(float(v))
        except (ValueError, TypeError):
            pass

    # Compute risk_level
    content_text = out.get("评论内容", "") or ""
    out["风险等级"] = compute_risk_level(content_text)

    # Handle nested structures
    # Some APIs wrap user info in a sub-object
    user_info = raw.get("user_info", raw.get("user", {}))
    if isinstance(user_info, dict) and user_info:
        for k in ["user_id", "user_id_str", "nickname", "user_name", "user_avatar"]:
            if k in user_info and FIELD_MAP.get(k) not in out:
                out[FIELD_MAP[k]] = user_info[k]

    # note info
    note_info = raw.get("note_info", raw.get("note", {}))
    if isinstance(note_info, dict) and note_info:
        for k in ["note_id", "note_title"]:
            if k in note_info and FIELD_MAP.get(k) not in out:
                out[FIELD_MAP[k]] = note_info[k]

    # Build note URL from note_id if missing
    if "笔记链接" not in out and "笔记ID" in out:
        nid = str(out["笔记ID"]).strip()
        if nid:
            out["笔记链接"] = f"https://www.xiaohongshu.com/explore/{nid}"

    # Fix URL fields: Feishu URL type requires {"link": url, "text": display}
    for url_field in ["笔记链接", "用户主页", "用户头像"]:
        if url_field in out and isinstance(out[url_field], str):
            url_val = out[url_field].strip()
            if url_val:
                out[url_field] = {"link": url_val, "text": url_val}

    # Normalize boolean fields to SingleSelect strings
    for f in ["是否贴主", "是否主评论"]:
        if f in out and not isinstance(out[f], str):
            out[f] = "是" if out[f] in (True, 1, "True", "true", "是") else "否"

    # Ensure 评论时间 is string
    ct = out.get("评论时间")
    if isinstance(ct, (int, float)) and ct > 0:
        # timestamp in milliseconds
        import datetime as _dt
        ts = ct / 1000 if ct > 1e12 else ct
        dt = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone(_dt.timedelta(hours=8)))
        out["评论时间"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    # 评论时间 is text type in feishu, ensure string
    if "评论时间" in out and not isinstance(out["评论时间"], str):
        out["评论时间"] = str(out["评论时间"])

    return out


def create_record(
    client: httpx.Client,
    token: str,
    base_token: str,
    table_id: str,
    fields: dict[str, Any],
) -> str | None:
    """Create a record and return the record_id."""
    # Feishu API expects fields wrapped in "fields" key
    body = {"fields": fields}
    result = api_request(
        client, token, "POST",
        f"/bitable/v1/apps/{base_token}/tables/{table_id}/records",
        body,
    )
    record = result.get("data", {}).get("record", {})
    return record.get("record_id")


def batch_create_records(
    client: httpx.Client,
    token: str,
    base_token: str,
    table_id: str,
    records: list[dict[str, Any]],
    batch_size: int = 50,
) -> tuple[int, list[str]]:
    """Batch create records (up to 500 per request)."""
    added = 0
    errors: list[str] = []

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        body = {"records": batch}
        try:
            result = api_request(
                client, token, "POST",
                f"/bitable/v1/apps/{base_token}/tables/{table_id}/records/batch_create",
                body,
            )
            created = result.get("data", {}).get("records", [])
            added += len(created)
        except Exception as e:
            errors.append(f"Batch {i//batch_size + 1} failed: {e}")

    return added, errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync XHS comments to Feishu Bitable")
    ap.add_argument("--mentions", required=True, help="Path to mentions JSON file")
    ap.add_argument("--base-token", default=DEFAULT_BASE_TOKEN)
    ap.add_argument("--table-id", default=DEFAULT_TABLE_ID)
    ap.add_argument("--app-id", default="")
    ap.add_argument("--app-secret", default="")
    ap.add_argument("--limit", type=int, default=0, help="Max comments to sync (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be synced without writing")
    args = ap.parse_args()

    # Load credentials
    app_id = args.app_id or os.environ.get("FEISHU_APP_ID", "")
    app_secret = args.app_secret or os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        app_id, app_secret = load_feishu_creds()

    # Read mentions JSON
    mentions_path = Path(args.mentions)
    if not mentions_path.exists():
        print(f"Error: {mentions_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(mentions_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalize input - support both {items: [...]} and plain list
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data
    else:
        print("Error: invalid JSON format", file=sys.stderr)
        sys.exit(1)

    if args.limit > 0:
        items = items[:args.limit]

    # Normalize all comments
    normalized = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        rec = normalize_comment(item)
        if not rec.get("评论ID") and not rec.get("评论内容"):
            skipped += 1
            continue
        normalized.append(rec)

    # Build note_url -> note_id mapping for dedup
    # First pass: ensure all records have 评论ID (use hash as fallback)
    for rec in normalized:
        if not rec.get("评论ID"):
            import hashlib
            raw_str = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            rec["评论ID"] = hashlib.md5(raw_str.encode()).hexdigest()[:16]

    if args.dry_run:
        print(f"[DRY RUN] Would sync {len(normalized)} comments:")
        for i, rec in enumerate(normalized[:5]):
            print(f"  {i+1}. [{rec.get('评论者昵称', '?')}] {rec.get('评论内容', '')[:50]}")
        if len(normalized) > 5:
            print(f"  ... and {len(normalized) - 5} more")
        return

    # Connect to Feishu
    with httpx.Client(timeout=30) as client:
        token = get_tenant_token(client, app_id, app_secret)

        # Dedup: get existing 评论ID -> record_id map
        print(f"Fetching existing records from table {args.table_id}...")
        existing = list_records_simple(client, token, args.base_token, args.table_id)
        print(f"  Found {len(existing)} existing records")

        # Filter out already-synced comments
        new_records = []
        for rec in normalized:
            cid = str(rec.get("评论ID", ""))
            if cid and cid in existing:
                continue
            new_records.append(rec)

        if not new_records:
            print("All comments already synced. Nothing to do.")
            print(f"Total input: {len(normalized)}, Skipped (dup): {len(normalized) - len(new_records)}, New: 0")
            return

        # Convert to Feishu API format
        feishu_records = [{"fields": rec} for rec in new_records]

        # Batch write
        print(f"Syncing {len(feishu_records)} new comments to Feishu...")
        added, errors = batch_create_records(
            client, token, args.base_token, args.table_id, feishu_records
        )

    # Summary
    print(json.dumps({
        "ok": True,
        "total_input": len(items),
        "normalized": len(normalized),
        "skipped_invalid": skipped,
        "existing_deduped": len(normalized) - len(new_records),
        "new_synced": added,
        "errors": errors,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
