# Neo9527/xhs-feishu-sync

> 小红书 → 飞书多维表格 全链路同步 | 一辉|AI自动化

<p align="center">
  <b>一句话告诉 Agent，自动完成采集、去重、同步</b><</p>

---

## 🎯 这是什么？

**一句话搞定小红书数据采集和同步**：

```
帮我采集「AI编程」相关的小红书笔记和评论，同步到飞书
```

Agent 会自动：
- 📝 采集笔记（标题、内容、点赞收藏数、封面图）
- 👤 采集博主信息（粉丝数、简介、主页）
- 💬 采集评论（根评论一行，子评论合并）
- 🔄 去重后同步到飞书多维表格

---

## 🚀 快速开始

### 第一步：安装

**OpenClaw 用户**：
```
git clone https://github.com/beautifulboy9527/xhs-feishu-sync.git ~/.openclaw/skills/
```

**Claude Code 用户**：
```
git clone https://github.com/beautifulboy9527/xhs-feishu-sync.git ~/.claude/skills/
```

### 第二步：安装依赖

```bash
npm install -g @lucasygu/redbook
```

### 第三步：配置飞书

**方式一：飞书 CLI（推荐）**

```bash
npm install -g @larkschool/cli
lark login
lark bitable create "小红书运营数据"
```

**方式二：手动配置**

编辑 `~/.openclaw/openclaw.json`：

```json
{
  "channels": {
    "feishu": {
      "appId": "你的App ID",
      "appSecret": "你的App Secret"
    }
  },
  "base_token": "你的Base Token",
  "notes_table_id": "笔记表ID",
  "authors_table_id": "博主表ID",
  "comments_table_id": "评论表ID"
}
```

### 第四步：开始使用

直接告诉 Agent：

```
帮我采集小红书上关于「AI编程」的10篇热门笔记
```

---

## 📦 包含的 Skills

| Skill | 功能 | 使用示例 |
|-------|------|---------|
| **notes** | 笔记采集同步 | "采集「AI编程」相关的10篇笔记" |
| **authors** | 博主采集同步 | "采集这些笔记对应的博主信息" |
| **comments** | 评论采集同步 | "采集这篇笔记的评论" |

---

## 🗂️ 数据结构

### 飞书多维表格模板

一键复制：[飞书多维表格模板](https://my.feishu.cn/base/Ro3EbZ5vLaXCljs651kc8j8Lndh?from=from_copylink)

包含表格：
- 📝 **对标笔记** - 笔记标题、内容、数据、封面
- 👤 **对标账号** - 博主信息、粉丝数、简介
- 💬 **评论数据** - 评论内容、用户、风险等级
- ✍️ **仿写分析** - 内容复用和二次创作

### 视图建议

创建以下视图提升效率：
- **按关键词分组** - 同主题笔记折叠在一起
- **高价值筛选** - 点赞数 > 1000 的爆款
- **待跟进评论** - 风险等级 = 高 且 未备注

---

## ⚠️ 踩坑记录

### ❌ 不要直接调 HTTP API

```python
# 错误 - 会 406
httpx.get("https://edith.xiaohongshu.com/api/...", cookies=cookies)
```

小红书风控会检测 TLS 指纹，纯 Python 请求会被拦截。

### ✅ 必须用 redbook CLI

```bash
redbook search "AI编程" --json --cookie-string "a1=xxx; web_session=xxx"
```

`redbook` CLI 内部处理了风控，是唯一可靠的方式。

### Cookie 获取方式

**正确**：Chrome → F12 → Network → 点任意 edith 请求 → 复制 cookie 整行
**错误**：Application → Cookies → 逐个复制（web_session 可能被截断）

---

## 🔒 安全审查

- ✅ 无硬编码 API Key
- ✅ 无硬编码 Token  
- ✅ Cookie 从参数读取，不存储
- ✅ 飞书凭证从配置文件读取

---

## 👤 作者

- **小红书**: 搜索 Neo1_9527
- **X/Twitter**: [@neo1_95](https://x.com/neo1_95)
- **GitHub**: [beautifulboy9527](https://github.com/beautifulboy9527)

---

<p align="center">
  <sub>Made with ❤️ by 一辉|AI自动化</sub>
</p>
