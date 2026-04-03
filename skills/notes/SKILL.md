---
name: xhs-feishu-sync
description: 按关键词采集小红书笔记与博主信息，并同步到飞书多维表格。用于“采集-去重-写入”数据链路，不负责评论互动与发布自动化。
---

# XHS Feishu Sync

## 技能职责（保持单一）

- 负责：`xhs_fetch.py -> dedup.py -> feishu_sync.py` 的数据同步链路。
- 负责：首次飞书配置（`config.json` / 表格 URL 拆解）、字段与公式排查（lark-cli）。
- 不负责：评论管理、互动回复、自动发布（这些属于其他技能）。

## 用户输入要求

- 每次运行至少提供：`关键词`。
- 可选参数：`total`、`sort`、`type`。
- 首次使用需提供飞书多维表格 URL 或填写 `config.json`。

## 快速流程

```bash
cd "{baseDir}/scripts"
python xhs_fetch.py notes --keyword "AI coding" --sort popular --type all --total 5 --out-notes "../tmp/notes_only.json"
python dedup.py --mode notes --notes "../tmp/notes_only.json" --out-dir "../tmp/notes_flow"
python feishu_sync.py --plan "../tmp/notes_flow/sync_plan.json"
```

博主流程同理，把 `notes` 改成 `authors`。

Cookie（comet/Chrome 均可）：

- 支持 `--cookie-string`（优先级最高）
- 支持 `--cookie-file`
- 支持环境变量 `XHS_COOKIE`、`XHS_COOKIE_FILE`
- 支持最小参数：`XHS_A1`、`XHS_WEB_SESSION`（可再加 `XHS_ID_TOKEN`）
- 支持在 `config.json` 写 `xhs_cookie`、`xhs_cookie_file`

示例：

```bash
python xhs_fetch.py --cookie-file "../tmp/xhs_cookie.txt" notes --keyword "AI coding" --out-notes "../tmp/notes_only.json"
```

最省事示例（只传关键字段）：

```bash
python xhs_fetch.py --a1 "你的a1" --web-session "你的web_session" notes --keyword "AI coding" --out-notes "../tmp/notes_only.json"
```

排障速记：

- 报 `at least 'a1' and 'web_session'`：说明缺少关键 cookie 字段。
- 报 `code=-104`：说明 cookie 无效或权限不足，通常是字段不成对或已过期；请在同一次登录会话重新复制 `a1 + web_session`（建议连 `id_token` 一并复制）。
- `a1` 只填值本体，不要额外拼接引号、换行或无关分隔符。

## 配置优先级

1. 命令行参数
2. 环境变量（`XHS_FEISHU_*`）
3. `config.json`
4. 脚本默认值（仅示例）

参考文档：

- `references/feishu-setup-first-run.md`
- `references/feishu-cli-bitable.md`
- `references/field-mapping.md`

## 可选脚本（非主链路）

- `backfill_details.py`：历史行补 details。
- `feishu_setup_rewrite_table.py` / `backfill_rewrite_links.py`：仿写表辅助维护。
- `rewrite_brief.py` / `render_dashboard.py`：本地分析与看板产物。

## 备注

- 本技能默认不把“评论互动”写入飞书；若未来有需求，建议拆成独立 `xhs-comment-sync` 技能，避免职责混杂。
