#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from xhs_comment_config import load_config, pick

RISK_KEYWORDS = [
    "加我",
    "私信你",
    "带你赚",
    "返现",
    "vx",
    "微信",
    "链接",
    "下载",
    "傻",
    "骗",
    "垃圾",
    "滚",
    "代运营",
    "涨粉神器",
    "秒变现",
]
XHS_MENTIONS_API = "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor="


def run_cmd(cmd: List[str], cwd: Path) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p.stdout


def parse_json_from_stdout(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    marker = "GET_NOTIFICATION_MENTIONS_RESULT:"
    i = text.find(marker)
    if i >= 0:
        text = text[i + len(marker) :].strip()
    j = text.find("{")
    if j >= 0:
        text = text[j:]
    return json.loads(text)


def pick_val(d: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def infer_note_id(item: Dict[str, Any]) -> str:
    for key in ("note_id", "target_note_id", "item_id", "id"):
        v = item.get(key)
        if v:
            s = str(v)
            if re.fullmatch(r"[0-9a-f]{16,32}", s):
                return s
    blob = json.dumps(item, ensure_ascii=False)
    m = re.search(r"[0-9a-f]{24}", blob)
    return m.group(0) if m else ""


def infer_note_url(item: Dict[str, Any], note_id: str) -> str:
    url = pick_val(item, "note_url", "url", "jump_url", "target_url")
    if url:
        return url
    if note_id:
        return f"https://www.xiaohongshu.com/explore/{note_id}"
    return ""


def risk_flags(text: str) -> List[str]:
    s = text or ""
    return [k for k in RISK_KEYWORDS if k in s]


def normalize_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    arr: List[Any] = []
    for k in ("message_list", "items", "mentions", "list"):
        v = data.get(k)
        if isinstance(v, list):
            arr = v
            break
    out: List[Dict[str, Any]] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        user_name = pick_val(it, "nickname", "user_name", "name", "from_user_name")
        content = pick_val(it, "content", "text", "comment_content", "message")
        t = pick_val(it, "time", "create_time", "timestamp")
        nid = infer_note_id(it)
        uid_seed = f"{user_name}|{content}|{t}|{nid}"
        comment_uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()[:20]
        out.append(
            {
                "comment_uid": comment_uid,
                "user_name": user_name,
                "content": content,
                "time": t,
                "note_id": nid,
                "note_url": infer_note_url(it, nid),
                "risk_flags": risk_flags(content),
                "raw": it,
            }
        )
    return out


def default_xhs_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "XiaohongshuSkills"


def read_cookie_from_file(path_text: str) -> str:
    p = Path(path_text).expanduser()
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def fetch_mentions_via_cookie(cookie: str, timeout: float = 25.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        XHS_MENTIONS_API,
        method="GET",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie,
            "Referer": "https://www.xiaohongshu.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        body = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("mentions api returned non-dict payload")
    code = payload.get("code")
    if code not in (0, "0", None):
        raise RuntimeError(f"mentions api error code={code}, msg={payload.get('msg')}")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取小红书评论和@通知并结构化输出")
    ap.add_argument("--config", default="")
    ap.add_argument("--xhs-skill-dir", default=str(default_xhs_dir()))
    ap.add_argument("--wait-seconds", type=float, default=18)
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--account", default="")
    ap.add_argument("--reuse-existing-tab", action="store_true")
    ap.add_argument("--cookie", default="", help="小红书 Cookie 字符串（优先级最高）")
    ap.add_argument("--cookie-file", default="", help="包含 Cookie 的文本文件路径")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config.strip() or None)
    cookie_cli = (args.cookie or "").strip()
    cookie_file = pick(args.cookie_file, cfg, "xhs_cookie_file", "XHS_COOKIE_FILE", "")
    cookie_cfg_or_env = pick("", cfg, "xhs_cookie", "XHS_COOKIE", "")
    cookie = cookie_cli or (read_cookie_from_file(cookie_file) if cookie_file else "") or cookie_cfg_or_env

    payload: Dict[str, Any]
    try:
        if cookie:
            payload = fetch_mentions_via_cookie(cookie, timeout=max(8.0, float(args.wait_seconds) + 7.0))
        else:
            xhs_dir = Path(args.xhs_skill_dir)
            script = xhs_dir / "scripts" / "cdp_publish.py"
            if not script.is_file():
                raise FileNotFoundError(f"cdp_publish.py not found: {script}")

            cmd = [sys.executable, str(script)]
            if args.host:
                cmd.extend(["--host", args.host])
            if args.port:
                cmd.extend(["--port", str(args.port)])
            if args.account:
                cmd.extend(["--account", args.account])
            if args.reuse_existing_tab:
                cmd.append("--reuse-existing-tab")
            cmd.extend(["get-notification-mentions", "--wait-seconds", str(args.wait_seconds)])
            raw = run_cmd(cmd, cwd=xhs_dir)
            payload = parse_json_from_stdout(raw)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "NOT_LOGGED_IN" in msg:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "NOT_LOGGED_IN",
                        "hint": "小红书登录态失效。可直接通过 --cookie / --cookie-file / XHS_COOKIE 注入 cookie，或先在 Chrome 登录后重试。",
                    },
                    ensure_ascii=False,
                )
            )
            raise SystemExit(2) from e
        if "code=-100" in msg or "登录" in msg or "403" in msg:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "COOKIE_INVALID",
                        "hint": "提供的 cookie 已失效或权限不足。请重新抓取 cookie 后重试。",
                    },
                    ensure_ascii=False,
                )
            )
            raise SystemExit(3) from e
        raise
    items = normalize_items(payload)

    result = {
        "ok": True,
        "total": len(items),
        "high_risk_count": sum(1 for x in items if x.get("risk_flags")),
        "items": items,
        "raw_payload": payload,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "total": len(items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
