# anki-exam-forge

[English](README.md)

一套面向高记忆密度学科备考的**全闭环 Anki 记忆卡片工程化插件、MCP Server 与跨平台通用 Agent Skill**：打通 **历年真题提取 → 考点考频统计 → 稳定 GUID `.apkg` 制卡 → 老师课件 PPT 增量查漏补缺 → 卡壳卡片记忆法回写** 四个阶段，零第三方 Python / Node.js 运行时依赖即可构建可直接导入 Anki 的卡片包。

## 核心闭环与设计特点

1. **历年真题摄取与确定性考频统计**
   - `extract` 支持 `.pdf`（自动文本提取 + 扫描件按页渲染 PNG 供多模态视觉识图）、`.pptx`（纯标准库解析幻灯片文本、讲者备注与插图）、`.docx`（段落、表格与图片）、`.md`/`.txt` 与图片目录。
   - `freq` 由 Agent 归一化 `concept_key`，再由 Python 引擎确定性计数归并：精确汇总出现次数、来源年份、同类问法变体，生成 **Markdown 考频排行榜与分章提纲**，并直接产出带 `freq::high` / `freq::medium` / `freq::low` 标签与分级子牌组的 `deck.json`。
2. **零外部依赖 `.apkg` 构建与高保真反解**
   - 纯 Python 标准库（`sqlite3` + `zipfile`）生成标准 Anki v11 `.apkg`，内置 3 套适配日/夜间模式的牌组模板：
     - `ExamForge-Basic`（问答题）
     - `ExamForge-MCQ`（选择题，正面支持轻量化点选交互，背面高亮答案与干扰项解析）
     - `ExamForge-Cloze`（多挖空填空题，自动解析 `{{c1::...}}`、`{{c2::...}}` 展开为独立卡片）
   - 自动解析并打包附图媒体，按 `past_exam` 优先、考频降序排列 `cards.due`，导入即先刷高频必背考点。
   - 每张卡拥有基于 `card_id` 的稳定 `guid`，配合 `inspect`（自动优先读取 `collection.anki21` 并提示新格式重导出说明）可无缝读写现有 `.apkg`。
3. **老师课件 PPT / 讲义增量补卡（零重复、不丢进度）**
   - `diff` 基于规范化考点名与混合 bigram 相似度算法，将课件候选题与现有 `deck.json` 或 `.apkg` 逐条比对：跳过完全重复、标出高度相似题（可选 `--merge-near` 原地补充解析）、仅新增真正缺漏的知识点到 `04-课件补充`，**原卡片 `guid` 100% 保持不变**。
4. **卡壳 / Leech 卡片记忆法回写**
   - `inspect --tag leech --min-lapses 3` 精准捞出反复遗忘的硬骨头卡片；结合内置 [记忆法手册](skills/anki-exam-forge/references/mnemonic-playbook.md) 生成助记提示后，通过 `mnemonic` 定向写入原卡片的 `Mnemonic` 独立字段并导出轻量更新 `.apkg`（保留原 `guid`），再次导入 Anki **原地更新、不覆盖复习进度**。

> **核心理念**：记忆法解决“初次记住”，Anki 间隔重复保持“长期可提取性”。

## 8 个工具清单（MCP & CLI 通用）

| 工具名（MCP） | CLI 子命令 | 作用 |
|---|---|---|
| `anki_doctor` | `doctor` | 检查 Python、SQLite、PPTX/DOCX 与 PDF 渲染后端状态 |
| `anki_extract_source` | `extract` | 提取 PDF（含扫描页渲染）、PPTX、DOCX、文本与图片素材 |
| `anki_analyze_frequency` | `freq` | 确定性统计历年考点出现频次，输出 Markdown 报告与优先牌组 JSON |
| `anki_validate_deck` | `validate` | 校验卡片结构、`{{c1::}}` 挖空语法、MCQ 选项、ID/GUID 唯一性与媒体路径 |
| `anki_build_apkg` | `build` | 编译生成支持日/夜双主题、带媒体与考频子牌组的 `.apkg` |
| `anki_inspect_apkg` | `inspect` | 反解检查 `.apkg`（优先 `collection.anki21`），按标签/牌组/关键词/`lapses` 筛选 |
| `anki_diff_supplement` | `diff` | 将课件补卡候选集与现有牌组或 `.apkg` 去重比对并安全合并 |
| `anki_apply_mnemonics` | `mnemonic` | 将记忆法助记写回匹配卡片的 `Mnemonic` 字段并导出原地更新 `.apkg` |

## 环境要求

- **Python `>= 3.9`**（核心制卡、频次统计、`.pptx`/`.docx` 解析与 `.apkg` 构建仅用标准库）
- **Node.js `>= 20`**（运行 MCP Server、Node CLI 与跨平台安装脚本）
- **可选 PDF 渲染增强**：`pymupdf`（`pip install pymupdf`）或系统 `pdftoppm`（仅处理 `.pdf` 扫描件/渲染页时需要）

## 快速安装与接入

```bash
# 1. 安装为 Antigravity 全局插件（~/.gemini/config/plugins/anki-exam-forge/）
node scripts/install.mjs --antigravity-global

# 2. 或安装到指定工作区（<workspace>/.agents/plugins/anki-exam-forge/）
node scripts/install.mjs --antigravity-workspace /path/to/workspace

# 3. 或仅安装跨平台独立 Skill（含内置 Python 引擎，适合 Claude Code / Codex / OpenCode 等纯 Skill 环境）
node scripts/install.mjs --skill-only ~/.agents/skills

# 4. 或打印适配 Claude Code / Cursor / Gemini CLI / Codex 的 MCP JSON 配置
node scripts/install.mjs --print-mcp
```

运行环境自检：

```bash
node server/cli.mjs doctor
# 或直接使用纯 Python CLI：
python engine/cli.py doctor
```

## 端到端示例（使用 `examples/` 临床医学样本）

```bash
# 第一步：对历年真题样本做确定性考频统计
node server/cli.mjs freq examples/sample-medical-exams.json --preset clinical-medicine --report-out out/freq.md --deck-out out/deck_v1.json

# 第二步：校验并打包生成 v1 .apkg
node server/cli.mjs validate out/deck_v1.json
node server/cli.mjs build out/deck_v1.json --out out/med_v1.apkg

# 第三步：拿老师课件补充卡片与已有 .apkg 做去重 Diff，仅追加新考点
node server/cli.mjs diff --base out/med_v1.apkg --candidates examples/sample-ppt-supplement.json --merge-near --out-deck out/deck_v2.json
node server/cli.mjs build out/deck_v2.json --out out/med_v2.apkg

# 第四步：反解筛选难卡并回写记忆法助记，生成原地更新补丁包
node server/cli.mjs inspect out/med_v2.apkg --query 垂腕
node server/cli.mjs mnemonic --base out/med_v2.apkg --patches examples/sample-mnemonics.json --out-deck out/deck_v3.json --out-apkg out/med_mnemonic_patch.apkg
```

## 运行测试

```bash
npm test
# 分别运行：
npm run test:py   # Python 核心引擎单测与端到端闭环验证
npm run test:js   # MCP stdio JSON-RPC、Node CLI 与多模式安装器验证
```

## 配套手机刷卡 App：玖记 (`anki-forge-app`)

产出的 `.apkg` 与 `deck.json` 除了可直接导入电脑/手机版 Anki，还配有专门打造的开源离线安卓刷卡伴侣 **[玖记（anki-forge-app）](https://github.com/nonuiucode/anki-forge-app)**：手机系统点击 `.apkg` 直接打开导入、内置 FSRS-5 与考频冲刺排序、选择题点选判分、解剖图缩放，以及一键把「卡壳本」分享回电脑给本插件配助记。

## 开源协议

本项目基于 **MIT** 协议开源，详见 [LICENSE](LICENSE)。
