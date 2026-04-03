#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent


def resolve_config_path(explicit: Optional[str]) -> Optional[Path]:
    if explicit and str(explicit).strip():
        p = Path(str(explicit).strip()).expanduser()
        return p if p.is_file() else None
    env = os.environ.get("XHS_COMMENT_CONFIG", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    default = SKILL_ROOT / "config.json"
    return default if default.is_file() else None


def load_config(explicit_path: Optional[str] = None) -> Dict[str, str]:
    p = resolve_config_path(explicit_path)
    if not p:
        return {}
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k in ("base_token", "comment_table_id", "comment_table_name", "xhs_cookie", "xhs_cookie_file"):
        v = data.get(k)
        if v is not None and str(v).strip():
            out[k] = str(v).strip()
    return out


def pick(cli: str, cfg: Dict[str, str], cfg_key: str, env_key: str, default: str) -> str:
    if cli and str(cli).strip():
        return str(cli).strip()
    ev = os.environ.get(env_key, "").strip()
    if ev:
        return ev
    if cfg.get(cfg_key):
        return cfg[cfg_key]
    return default
