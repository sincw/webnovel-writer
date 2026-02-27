---
name: webnovel-write
description: Writes webnovel chapters (3000-5000 words). Use when the user asks to write a chapter or runs /webnovel-write. Runs context, drafting, review, polish, and data extraction.
allowed-tools: Read Write Edit Grep Bash Task
---

# Chapter Writing Skill

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.webnovel/state.json`）
- 若当前目录不存在该文件，先询问用户项目路径并 `cd` 进入
- 进入后设置变量：`$PROJECT_ROOT = (Resolve-Path ".").Path`

## Default flow

1. Context Agent → 创作任务书
2. 写作 → 3000-5000字正文
3. 审查 → 核心4个Checker
4. 润色 → 修复问题
5. Data Agent → 提取数据
6. Git → 备份

## Modes

- `/webnovel-write`：标准流程（含核心4个Checker）
- `/webnovel-write --fast`：跳过 Step 2B，其余同标准
- `/webnovel-write --minimal`：跳过 Step 2B，仅运行 consistency + continuity + ooc（不产出追读力数据）
- `/webnovel-write --test`：仅执行 Step 1 + Step 2，跳过 Step 3/4/5/6，输出到 `正文/第{NNNN}章-TEST.md`
- `/webnovel-write --external`：Step 1 由 Claude 执行，Step 2 交给外部模型（默认 DeepSeek），Step 3-6 继续由 Claude 执行
- `/webnovel-write --external --mode full`：同上，但传递设定集核心文件给外部模型
- `/webnovel-write --external --profile qwen`：指定外部模型 profile

## Step 1: Context Agent

使用 Task 工具调用 `context-agent`：

```
调用 context-agent，参数：
- chapter: {chapter_num}
- project_root: {PROJECT_ROOT}
- storage_path: .webnovel/
- state_file: .webnovel/state.json
```

**缺失处理**：大纲或 state.json 不存在时，提示用户先初始化。

**要求**：创作任务书必须包含“反派层级”（从大纲/章纲提取）。

### Step 1.5: Contract v2 Guidance 注入

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_chapter_context.py" --chapter {chapter_num} --project-root "{PROJECT_ROOT}" --format json
```

- 必须读取：`writing_guidance.guidance_items`
- 推荐读取：`reader_signal` 与 `genre_profile.reference_hints`

## Step 2: 写作

- 遵循三大原则：大纲即法律 / 设定即物理 / 新实体需记录。
- 输出纯正文到 `正文/第{NNNN}章.md`（`--test` 模式输出到 `正文/第{NNNN}章-TEST.md`）。
- 章节内容需体现本章“反派层级”要求（无反派层级时标注“无”）。
- 开写前加载核心约束：

```bash
cat "${CLAUDE_PLUGIN_ROOT}/skills/webnovel-write/references/core-constraints.md"
```

- 加载项目写作人格（优先项目级，回退模板）：

```bash
# 优先加载项目级
if [ -f "${PROJECT_ROOT}/.webnovel/style-persona.md" ]; then
  cat "${PROJECT_ROOT}/.webnovel/style-persona.md"
else
  cat "${CLAUDE_PLUGIN_ROOT}/skills/webnovel-write/references/style-persona-template.md"
fi
```

场景写作与风格参考按需加载（见 `references/workflow-details.md`）。

### Step 2 External 模式（--external）

当使用 `--external` 时，Step 2 改为调用外部模型脚本：

```bash
cd "${CLAUDE_PLUGIN_ROOT}/scripts"
python external_writer.py \
  --chapter {chapter_num} \
  --project-root "{PROJECT_ROOT}" \
  --mode {compact|full} \
  --profile {profile_name}
```

- 脚本自动读取 Step 1 产出的 context_snapshot，组装 prompt 并调用外部 API
- `--mode compact`（默认）：创作任务书 + 核心约束
- `--mode full`：额外附加设定集核心文件
- `--profile`：选择模型（deepseek/qwen/openai），配置见 `scripts/external_writer_config.json`
- 输出到 `正文/第{NNNN}章.md`，后续 Step 3-6 正常执行

## Reference Loading Levels (strict, lazy)

- L0: 不加载额外参考，直到当前 step 明确。
- L1: 仅加载本 step 的最小必需文件。
- L2: 仅在触发条件满足时加载扩展参考。

### L1 (minimum)
- Step 2 写作前：`.webnovel/style-persona.md`（回退 `references/style-persona-template.md`）
- Step 4 润色前：`references/polish-guide.md`（已内嵌 Patch B 引用）

### L2 (conditional)
- 仅当 Step 1.5 需要风格/体裁细化时加载：
  - `references/style-variants.md`
  - `.claude/references/reading-power-taxonomy.md`
  - `.claude/references/genre-profiles.md`
  - `references/writing/genre-hook-payoff-library.md`（电竞/直播文/克苏鲁优先）
- 仅当需要模板细节时加载：
  - `references/workflow-details.md`
  - `references/writing/typesetting.md`

## Step 3: 审查

**默认核心4个 Checker**：
- `consistency-checker`
- `continuity-checker`
- `ooc-checker`
- `reader-pull-checker`（用于写入追读力数据）

**关键章/卷末/用户明确要求**：额外运行
- `high-point-checker`
- `pacing-checker`

审查汇总表格与审查指标 JSON 模板见 `references/workflow-details.md`。

## Step 4: 润色

加载润色参考：

```bash
cat "${CLAUDE_PLUGIN_ROOT}/skills/webnovel-write/references/polish-guide.md"
cat "${CLAUDE_PLUGIN_ROOT}/skills/webnovel-write/references/writing/typesetting.md"
```

先修复 critical/high，再处理 medium/low。

## Step 5: Data Agent

使用 Task 工具调用 `data-agent`：

```
调用 data-agent，参数：
- chapter: {chapter_num}
- chapter_file: "正文/第{NNNN}章.md"
- review_score: {overall_score from Step 3}
- project_root: {PROJECT_ROOT}
- storage_path: .webnovel/
- state_file: .webnovel/state.json
```

债务利息计算默认关闭，仅在开启债务追踪或用户明确要求时执行（见 `references/workflow-details.md`）。

## Step 6: Git 备份

```bash
git add . && git commit -m "Ch{chapter_num}: {title}"
```

## 详细流程与模板（按需加载）

需要以下内容时，读取：
- `references/workflow-details.md`
  - Step 1.5 章节设计（钩子/爽点/微兑现/差异化）
  - 审查汇总表格 + 审查指标 JSON 模板
  - 润色硬规则 + 检查清单
  - 债务/Override 处理与利息开关

Step 1.5 需要时可直接加载（避免深层引用）：
- `references/style-variants.md`
- `.claude/references/reading-power-taxonomy.md`
- `.claude/references/genre-profiles.md`
