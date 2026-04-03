#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def top_authors(authors: List[Dict[str, Any]], k: int = 5) -> List[Dict[str, Any]]:
    def fans_num(x: Dict[str, Any]) -> int:
        raw = str(x.get("fans", "")).replace(",", "").strip()
        if raw.endswith("万"):
            try:
                return int(float(raw[:-1]) * 10000)
            except ValueError:
                return 0
        return int(raw) if raw.isdigit() else 0

    return sorted(authors, key=fans_num, reverse=True)[:k]


def render_notes(notes: List[Dict[str, Any]]) -> str:
    cards: List[str] = []
    for n in notes[:8]:
        cards.append(
            f"""
            <div class="card">
              <div class="title">{esc(n.get("title"))}</div>
              <div class="meta">类型：{esc(n.get("note_type"))}</div>
              <div class="meta">作者：{esc(n.get("author_name"))}</div>
              <div class="meta">赞 {esc(n.get("liked_count"))} · 藏 {esc(n.get("collected_count"))} · 评 {esc(n.get("comment_count"))}</div>
              <div class="desc">{esc(str(n.get("note_desc", ""))[:120])}</div>
            </div>
            """
        )
    return "\n".join(cards)


def render_authors(authors: List[Dict[str, Any]]) -> str:
    cards: List[str] = []
    for a in top_authors(authors):
        cards.append(
            f"""
            <div class="card">
              <div class="title">{esc(a.get("nick_name"))}</div>
              <div class="meta">粉丝：{esc(a.get("fans"))} · 赞藏：{esc(a.get("likes_and_collects"))}</div>
              <div class="desc">{esc(str(a.get("desc", ""))[:120])}</div>
            </div>
            """
        )
    return "\n".join(cards)


def render_rewrites(rewrites: Dict[str, Any]) -> str:
    items = rewrites.get("items", []) if isinstance(rewrites, dict) else []
    cards: List[str] = []
    for it in items[:5]:
        rb = it.get("rewrite_brief", {})
        topics = rb.get("core_topics", []) if isinstance(rb.get("core_topics"), list) else []
        cards.append(
            f"""
            <div class="card">
              <div class="title">{esc(rb.get("hook_title"))}</div>
              <div class="meta">仿写类型：{esc(it.get("source_type"))}</div>
              <div class="meta">来源：{esc(it.get("source_title"))}</div>
              <div class="meta">主题：{esc(' / '.join([str(x) for x in topics[:4]]))}</div>
              <div class="desc">{esc(rb.get("cta"))}</div>
            </div>
            """
        )
    return "\n".join(cards)


def main() -> None:
    parser = argparse.ArgumentParser(description="渲染 XHS 数据看板 HTML")
    parser.add_argument("--notes", required=True)
    parser.add_argument("--authors", required=True)
    parser.add_argument("--rewrites", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    authors = json.loads(Path(args.authors).read_text(encoding="utf-8"))
    rewrites = json.loads(Path(args.rewrites).read_text(encoding="utf-8"))

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>XHS 对标分析看板</title>
  <style>
    body {{ margin: 0; background: #0b1020; color: #eaf0ff; font-family: "Microsoft YaHei", sans-serif; }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 20px; }}
    h1 {{ margin: 0 0 16px; font-size: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .panel {{ background: #121a33; border: 1px solid #27345c; border-radius: 12px; padding: 12px; }}
    .panel h2 {{ margin: 0 0 10px; font-size: 16px; color: #9fc2ff; }}
    .card {{ border: 1px solid #2a3b6b; border-radius: 10px; padding: 10px; margin-bottom: 10px; background: #0f1730; }}
    .title {{ font-size: 14px; font-weight: 700; margin-bottom: 6px; }}
    .meta {{ color: #adc0ea; font-size: 12px; margin-bottom: 4px; }}
    .desc {{ color: #d6e2ff; font-size: 12px; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>XHS 对标分析看板（采集/仿写一体）</h1>
    <div class="grid">
      <section class="panel"><h2>爆款笔记</h2>{render_notes(notes if isinstance(notes, list) else [])}</section>
      <section class="panel"><h2>对标账号</h2>{render_authors(authors if isinstance(authors, list) else [])}</section>
      <section class="panel"><h2>仿写建议</h2>{render_rewrites(rewrites)}</section>
    </div>
  </div>
</body>
</html>
"""
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
