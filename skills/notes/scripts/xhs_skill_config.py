#!/usr/bin/env python3
"""Optional config.json + env for Feishu targets. Skill root = parent of scripts/."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def resolve_config_path(explicit: Optional[str]) -> Optional[Path]:
    if explicit and str(explicit).strip():
        p = Path(str(explicit).strip()).expanduser()
        return p if p.is_file() else None
    env = os.environ.get("XHS_FEISHU_CONFIG", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    default = SKILL_ROOT / "config.json"
    return default if default.is_file() else None


def load_feishu_user_config(explicit_path: Optional[str] = None) -> Dict[str, str]:
    """Load file config; caller should apply env overrides for each key."""
    out: Dict[str, str] = {}
    p = resolve_config_path(explicit_path)
    if not p:
        return out
    data = _read_json(p)
    for key in (
        "base_token",
        "notes_table_id",
        "authors_table_id",
        "rewrite_table_id",
        "xhs_cookie",
        "xhs_cookie_file",
        "xhs_a1",
        "xhs_web_session",
        "xhs_id_token",
    ):
        v = data.get(key)
        if v is not None and str(v).strip():
            out[key] = str(v).strip()
    return out


def pick_str(
    cli: str,
    cfg: Dict[str, str],
    cfg_key: str,
    env_key: str,
    default: str,
) -> str:
    """Priority: non-empty CLI > env > cfg file > default."""
    if cli and str(cli).strip():
        return str(cli).strip()
    ev = os.environ.get(env_key, "").strip()
    if ev:
        return ev
    if cfg.get(cfg_key):
        return cfg[cfg_key]
    return default
