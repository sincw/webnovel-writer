#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
External Writer - 外部模型写作适配器

将创作任务书 + 写作约束组装为 prompt，调用外部模型 API 生成章节正文。
支持 OpenAI 兼容接口（DeepSeek / Qwen / GPT 等）。

两档模式：
- compact: 创作任务书 + 核心约束（适用于大部分章节）
- full:    额外附加设定集核心文件（关键章/新角色/战斗章）

用法：
  cd .claude/scripts
  python external_writer.py --chapter 1 --project-root ../../游戏三国-只有我知道剧情
  python external_writer.py --chapter 1 --project-root ... --mode full
  python external_writer.py --chapter 1 --project-root ... --profile qwen
  python external_writer.py --chapter 1 --project-root ... --brief path/to/brief.md
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "external_writer_config.json"
PLUGIN_ROOT = SCRIPT_DIR.parent  # .claude/

# 写作参考文件（相对于 PLUGIN_ROOT）
REF_CORE_CONSTRAINTS = "skills/webnovel-write/references/core-constraints.md"
REF_ANTI_AI_GUIDE = "skills/webnovel-write/references/anti-ai-guide.md"
REF_STYLE_PERSONA_TEMPLATE = "skills/webnovel-write/references/style-persona-template.md"

# 设定集核心文件（相对于项目根目录，full 模式加载）
SETTINGS_CORE_FILES = [
    "设定集/主角卡.md",
    "设定集/女主卡.md",
    "设定集/力量体系.md",
    "设定集/世界观.md",
    "设定集/金手指设计.md",
    "设定集/反派设计.md",
]


def load_config(profile_name: Optional[str] = None) -> dict:
    """加载模型配置"""
    if not CONFIG_PATH.exists():
        print(f"[ERROR] 配置文件不存在: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    name = profile_name or config.get("active_profile", "deepseek")
    profiles = config.get("profiles", {})

    if name not in profiles:
        print(f"[ERROR] profile '{name}' 不存在，可用: {list(profiles.keys())}", file=sys.stderr)
        sys.exit(1)

    profile = profiles[name]
    # 读取 API key：支持环境变量名或直接填写 key
    api_key_env = profile.get("api_key_env", "")
    if api_key_env.startswith("sk-") or api_key_env.startswith("sk_"):
        # 直接填写了 API key
        profile["api_key"] = api_key_env
    else:
        profile["api_key"] = os.environ.get(api_key_env, "")
        if not profile["api_key"]:
            print(f"[WARN] 环境变量 {api_key_env} 未设置", file=sys.stderr)

    profile["_name"] = name
    return profile


def read_file_safe(path: Path) -> Optional[str]:
    """安全读取文件，不存在返回 None"""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def find_brief(chapter: int, project_root: Path, brief_path: Optional[str] = None) -> str:
    """查找创作任务书。优先使用指定路径，否则查找 context_snapshots。"""
    if brief_path:
        p = Path(brief_path)
        content = read_file_safe(p)
        if content:
            return content
        print(f"[ERROR] 指定的创作任务书不存在: {p}", file=sys.stderr)
        sys.exit(1)

    # 尝试 context_snapshots
    snapshot = project_root / ".webnovel" / "context_snapshots" / f"ch{chapter:04d}.json"
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        # snapshot 的 payload 就是组装好的上下文
        return json.dumps(data, ensure_ascii=False, indent=2)

    print(f"[ERROR] 未找到第{chapter}章的创作任务书。", file=sys.stderr)
    print("  请先运行 Step 1 (context-agent) 生成任务书，或用 --brief 指定路径。", file=sys.stderr)
    sys.exit(1)


def assemble_prompt(
    chapter: int,
    project_root: Path,
    mode: str,
    brief_content: str,
) -> list[dict]:
    """组装 messages 列表。返回 OpenAI 格式的 messages。"""

    # --- system prompt ---
    system_parts = [
        "你是一位资深网文作家，擅长写出节奏紧凑、代入感强的网络小说。",
        "请严格遵循以下写作约束，输出纯正文（无元数据、无注释、无标记）。",
        "字数要求：3000-5000字。",
    ]

    # L1: 核心约束（始终加载）
    core = read_file_safe(PLUGIN_ROOT / REF_CORE_CONSTRAINTS)
    if core:
        system_parts.append(f"\n## 核心写作约束\n{core}")

    anti_ai = read_file_safe(PLUGIN_ROOT / REF_ANTI_AI_GUIDE)
    if anti_ai:
        system_parts.append(f"\n## 降AI味指南\n{anti_ai}")

    # 写作人格：优先项目级，回退模板
    persona = read_file_safe(project_root / ".webnovel" / "style-persona.md")
    if not persona:
        persona = read_file_safe(PLUGIN_ROOT / REF_STYLE_PERSONA_TEMPLATE)
    if persona:
        system_parts.append(f"\n## 写作风格人格\n{persona}")

    # full 模式：附加设定集
    if mode == "full":
        settings_parts = []
        for rel in SETTINGS_CORE_FILES:
            content = read_file_safe(project_root / rel)
            if content:
                name = Path(rel).stem
                settings_parts.append(f"### {name}\n{content}")
        if settings_parts:
            system_parts.append("\n## 设定集参考\n" + "\n\n".join(settings_parts))

    system_msg = "\n".join(system_parts)

    # --- user prompt ---
    user_msg = f"请根据以下创作任务书，写出第{chapter:04d}章的正文。\n\n{brief_content}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def call_model(messages: list[dict], profile: dict) -> str:
    """调用 OpenAI 兼容 API，返回生成的正文。"""
    url = profile["base_url"].rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": profile["model"],
        "messages": messages,
        "temperature": profile.get("temperature", 0.85),
        "max_tokens": profile.get("max_tokens", 8192),
        "top_p": profile.get("top_p", 0.95),
    }

    headers = {
        "Content-Type": "application/json",
    }
    if profile.get("api_key"):
        headers["Authorization"] = f"Bearer {profile['api_key']}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[ERROR] API 返回 {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERROR] 网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)

    choices = result.get("choices", [])
    if not choices:
        print(f"[ERROR] API 返回无 choices: {json.dumps(result, ensure_ascii=False)}", file=sys.stderr)
        sys.exit(1)

    content = choices[0].get("message", {}).get("content", "")

    # 打印 token 用量
    usage = result.get("usage", {})
    if usage:
        print(f"[INFO] tokens: prompt={usage.get('prompt_tokens', '?')}, "
              f"completion={usage.get('completion_tokens', '?')}, "
              f"total={usage.get('total_tokens', '?')}", file=sys.stderr)

    return content


def main():
    parser = argparse.ArgumentParser(description="外部模型写作适配器")
    parser.add_argument("--chapter", type=int, required=True, help="章节号")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--mode", choices=["compact", "full"], default="compact",
                        help="compact=精简(默认), full=附加设定集")
    parser.add_argument("--profile", default=None, help="模型 profile 名称")
    parser.add_argument("--brief", default=None, help="创作任务书文件路径（可选）")
    parser.add_argument("--output", default=None, help="输出路径（默认: 正文/第NNNN章.md）")
    parser.add_argument("--dry-run", action="store_true", help="只组装 prompt，不调用 API")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not (project_root / ".webnovel").exists():
        print(f"[ERROR] 项目目录无效（缺少 .webnovel/）: {project_root}", file=sys.stderr)
        sys.exit(1)

    profile = load_config(args.profile)
    print(f"[INFO] 模型: {profile['_name']} ({profile['model']})", file=sys.stderr)
    print(f"[INFO] 模式: {args.mode}", file=sys.stderr)

    # 查找创作任务书
    brief = find_brief(args.chapter, project_root, args.brief)

    # 组装 prompt
    messages = assemble_prompt(args.chapter, project_root, args.mode, brief)

    # 统计 prompt 长度
    total_chars = sum(len(m["content"]) for m in messages)
    print(f"[INFO] prompt 总字符数: {total_chars}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return

    # 调用模型
    print(f"[INFO] 正在调用 {profile['_name']} ...", file=sys.stderr)
    content = call_model(messages, profile)

    if not content.strip():
        print("[ERROR] 模型返回空内容", file=sys.stderr)
        sys.exit(1)

    # 写入文件
    if args.output:
        output_path = Path(args.output)
    else:
        chapters_dir = project_root / "正文"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        output_path = chapters_dir / f"第{args.chapter:04d}章.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    word_count = len(content)
    print(f"[OK] 已写入: {output_path} ({word_count}字)", file=sys.stderr)


if __name__ == "__main__":
    main()
