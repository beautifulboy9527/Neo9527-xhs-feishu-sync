---
name: xhs-comment-sync
description: 抓取小红书笔记评论并同步到飞书多维表格，支持根评论合并、自动去重、风险标注。用于评论巡检、线索管理；不执行自动回复或发布。
---

# XHS Comment Sync

## 职责边界

- 负责：抓取指定笔记的评论数据，结构化落盘。
- 负责：批量同步评论到飞书多维表格（支持去重）。
- 负责：自动计算风险等级（低/中/高）。
- 不负责：自动回复、自动发布、账号养号操作。

## ⚠️ 关键经验（踩坑记录）

### 必须使用 `@lucasygu/redbook` CLI

**❌ 错误方式（会 406）：**
```python
# 直接用 httpx/urllib 调用小红书 API
r = httpx.get("https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions", cookies=cookies)
# → 406 {"code":-1,"success":false}
```

**❌ 模拟 TLS 指纹也不行：**
```python
# curl_cffi 模拟 Chrome 指纹
r = cffi_requests.get(url, cookies=cookies, impersonate="chrome124")
# → 仍然 406
```

**✅ 正确方式：**
```bash
# 安装 CLI
npm install -g @lucasygu/redbook

# 采集评论
redbook comments "https://www.xiaohongshu.com/explore/NOTE_ID" --cookie-string "a1=xxx; web_session=xxx" --json
```

**原因**：小红书的风控系统会检测：
1. TLS 指纹（JA3）
2. 请求头特征
3. 浏览器行为模式

`redbook` CLI 内部处理了这些风控，纯 Python HTTP 请求会被拦截。

### Chrome 127+ Windows App-Bound Encryption

Windows 上的 Chrome 127+ 使用 App-Bound Encryption 加密 Cookie，**外部工具无法直接读取**：
- `redbook whoami` 会失败（读不到 Chrome Cookie）
- 必须通过 `--cookie-string` 手动传入 Cookie

### Cookie 获取方式

**正确方式：从 Network 面板复制**
1. Chrome 打开 `xiaohongshu.com`（确保已登录）
2. F12 → Network 标签
3. 刷新页面
4. 点任意 `edith.xiaohongshu.com` 请求
5. Request Headers → 复制 `cookie:` 整行值

**不要从 Application → Cookies 复制**：
- 那里显示的 `web_session` 可能被截断
- Network 请求头里的才是完整的

## 数据结构设计

### 核心原则：根评论 = 一行，子评论合并

**问题**：如果每条评论（包括子评论）都单独一行，数据会很混乱，不同帖子的评论混在一起。

**解决方案**：
- 只有根评论作为独立行
- 子评论格式化后存入「回复摘要」字段
- 通过「笔记ID」「笔记标题」可分组筛选

一篇帖子有 10 条根评论 → 只有 10 行数据，清晰可控。

### 飞书表结构

**目标 Base**: `Ro3EbZ5vLaXCljs651kc8j8Lndh`
**目标表**: `评论数据`（table_id: `tblfKB9QF7xsG1gQ`）

**核心字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 评论ID | Text | 唯一标识（去重键） |
| 笔记ID | Text | 关联的笔记 ID |
| 笔记标题 | Text | 笔记标题（用于分组） |
| 笔记链接 | URL | 笔记页面链接 |
| 评论内容 | Text | 评论正文 |
| 评论者昵称 | Text | 评论者昵称 |
| 评论者ID | Text | 评论者小红书 ID |
| 用户主页 | URL | 评论者主页链接 |
| 评论点赞数 | Number | 评论获得的点赞 |
| 子评论数 | Number | 该评论下的子评论数 |
| IP属地 | Text | 评论者 IP 归属地 |
| 评论时间 | Text | 评论发布时间 |
| 采集时间 | DateTime | 自动填充 |
| 风险等级 | SingleSelect | 低/中/高（自动计算） |
| 用户标签 | MultiSelect | 潜在客户/同行/路人/KOL/竞品/已转化 |
| 线索意向 | SingleSelect | 无意向/了解中/强意向/已转化/已流失 |
| 跟进备注 | Text | 运营人员备注 |
| 原始JSON | Text | 子评论摘要（复用字段） |

**飞书视图建议**：
- 创建「按笔记分组」视图 → 同一篇帖子的评论折叠在一起
- 创建「高价值线索」筛选 → 风险等级=高 或 点赞数>20

## 运行步骤

### 方式一：直接同步（推荐）

```bash
python scripts/sync_comments_to_feishu.py --note-url "https://www.xiaohongshu.com/explore/NOTE_ID" --cookie-string "a1=xxx; web_session=xxx"
```

**特性**：
- ✅ 一条命令完成采集 + 同步
- ✅ 根评论一行，子评论合并
- ✅ 自动去重（基于评论ID）
- ✅ 自动计算风险等级

### 方式二：分步执行

```bash
# 1. 先采集评论
redbook comments "https://www.xiaohongshu.com/explore/NOTE_ID" --cookie-string "a1=xxx; web_session=xxx" --json > tmp/comments.json

# 2. 再同步到飞书
python scripts/sync_comments_to_feishu.py --comments-file tmp/comments.json
```

### 可选参数

- `--notes-file`：批量采集（传入笔记 JSON 文件）
- `--dry-run`：只打印预览，不写入
- `--base-token`：指定飞书 Base token
- `--table-id`：指定目标表 ID

## 风险等级自动计算

| 等级 | 触发条件 |
|------|---------|
| 高 | 点赞数 ≥ 20 或 子评论数 ≥ 10 |
| 中 | 点赞数 ≥ 5 或 子评论数 ≥ 3 |
| 低 | 其他 |

## 配置

脚本自动从 `~/.openclaw/openclaw.json` 读取：
- 飞书凭证：`channels.feishu.appId` / `appSecret`
- Base token：`base_token`
- 表 ID：`comments_table_id`

## 模板链接

飞书多维表格模板：https://my.feishu.cn/base/Ro3EbZ5vLaXCljs651kc8j8Lndh?from=from_copylink

包含：
- 对标账号表
- 对标笔记表
- 评论数据表（本文档）
- 仿写分析表

## 技术栈

- **采集**：`@lucasygu/redbook` npm CLI（必须）
- **同步**：Python + httpx（纯 HTTP，无 lark 依赖）
- **存储**：飞书多维表格
