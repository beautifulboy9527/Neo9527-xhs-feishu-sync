#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


def to_int(text: Any) -> int:
    s = str(text or "").strip()
    if not s:
        return 0
    s = s.replace(",", "")
    if s.endswith("万"):
        try:
            return int(float(s[:-1]) * 10000)
        except ValueError:
            return 0
    if s.isdigit():
        return int(s)
    return 0


def extract_topics(desc: str, tags: List[str]) -> List[str]:
    topics = [str(t).strip() for t in tags if str(t).strip()]
    if topics:
        return topics[:8]
    found = re.findall(r"#([^#\[\]]+)\[话题\]#", desc or "")
    uniq: List[str] = []
    for t in found:
        x = t.strip()
        if x and x not in uniq:
            uniq.append(x)
    return uniq[:8]


def build_hook(title: str, top_topic: str) -> str:
    base = title.strip() if title else "这个方法"
    if top_topic:
        return f"{top_topic}实测：{base}，3步拿到可复用结果"
    return f"{base}，3步拿到可复用结果"


def classify_note_type(note: Dict[str, Any]) -> str:
    raw_t = str(note.get("note_type", "") or "").lower()
    if raw_t in ("video", "videos"):
        return "video"
    if raw_t in ("normal", "image", "img", "images"):
        return "image"
    # 兜底：有分享数通常更接近视频传播链路
    return "video" if to_int(note.get("share_count")) > 0 else "image"


def top_notes(notes: List[Dict[str, Any]], k: int) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for n in notes:
        like = to_int(n.get("liked_count"))
        col = to_int(n.get("collected_count"))
        cmt = to_int(n.get("comment_count"))
        score = like * 1 + col * 2 + cmt * 3
        x = dict(n)
        x["_score"] = score
        scored.append(x)
    scored.sort(key=lambda x: int(x.get("_score", 0)), reverse=True)
    return scored[:k]


def build_rewrite_entry(note: Dict[str, Any]) -> Dict[str, Any]:
    title = str(note.get("title", "") or "")
    desc = str(note.get("note_desc", "") or "")
    tags = note.get("tags", []) if isinstance(note.get("tags"), list) else []
    topics = extract_topics(desc, tags)
    top_topic = topics[0] if topics else ""
    note_kind = classify_note_type(note)
    if note_kind == "video":
        structure = [
            "0-3秒：冲突感钩子（结果/反常识）",
            "4-15秒：问题场景 + 失败对照",
            "16-35秒：步骤演示（关键参数上屏）",
            "36-50秒：结果展示 + 前后对比",
            "结尾：评论区关键词领取模板",
        ]
        media_strategy = "强节奏口播 + 屏录演示 + 关键参数字幕化"
        cta = "评论区回复“视频模板”领取同款分镜和提示词"
    else:
        structure = [
            "封面：结果导向标题（数字/时间/对比）",
            "第1页：场景痛点 + 目标结果",
            "第2-4页：步骤拆解（每页一个动作）",
            "第5页：避坑清单 + 参数建议",
            "末页：总结 + 评论区互动引导",
        ]
        media_strategy = "封面大字结论 + 多页步骤卡片 + 关键截图标注"
        cta = "评论区回复“图文模板”领取同款排版与文案骨架"
    return {
        "source_note_id": note.get("note_id", ""),
        "source_title": title,
        "source_type": note_kind,
        "author_name": note.get("author_name", ""),
        "note_url": note.get("note_url", ""),
        "engagement": {
            "liked_count": str(note.get("liked_count", "")),
            "collected_count": str(note.get("collected_count", "")),
            "comment_count": str(note.get("comment_count", "")),
        },
        "rewrite_brief": {
            "hook_title": build_hook(title, top_topic),
            "core_topics": topics,
            "content_structure": structure,
            "media_strategy": media_strategy,
            "tone": "实测复盘、低门槛、可直接照做",
            "cta": cta,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从对标笔记生成仿写建议")
    parser.add_argument("--notes", required=True, help="notes_only.json 文件路径")
    parser.add_argument("--out", required=True, help="输出仿写建议 JSON 路径")
    parser.add_argument("--top", type=int, default=5, help="选取互动 TopK 笔记")
    args = parser.parse_args()

    notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    if not isinstance(notes, list):
        raise ValueError("notes 文件必须是数组 JSON")

    selected = top_notes(notes, args.top)
    rewrites = [build_rewrite_entry(n) for n in selected]
    payload = {
        "ok": True,
        "count": len(rewrites),
        "items": rewrites,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_path), "count": len(rewrites)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
