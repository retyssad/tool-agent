"""项目证据采集：scout → 按模块并行 reader → synthesizer。

这是真正需要上下文隔离的地方：每个 reader 只拿一个模块的代码，
synthesizer 只看汇总后的候选证据，不重新读源码。
所有子 agent 只读、只返回结构化结果，落库由 handler 统一完成。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.harness.executor import RunHandle
from app.tools.scan import ModuleInfo, ScanResult, read_module_files

SCOUT_PROMPT = """你是代码库侦察员。根据目录结构、README 与各模块文件清单，判断这个项目的性质，并给每个模块分配阅读优先级。

只输出 JSON：
{
  "project_name": "项目名（来自 README 或目录名）",
  "one_liner": "一句话说明项目做什么，不超过 40 字",
  "tech_stack": ["从文件类型、依赖清单、README 推断出的技术，只写有证据的"],
  "modules": [
    {"name": "目录名", "priority": 1-5, "reason": "为什么值得读", "focus": "读的时候关注什么"}
  ],
  "interview_angles": ["这个项目最可能被面试官追问的 3-6 个方向"]
}

规则：
1. 只依据给出的结构信息，不要猜测未出现的框架或功能。
2. priority 5 表示最能体现工程能力的模块，1 表示配置/脚手架。
3. interview_angles 要具体到技术点，不要写"项目经验"这种空话。
"""

READER_PROMPT = """你是模块代码分析员。只分析给定的一个模块，提取可用于简历与面试的**事实**。

只输出 JSON：
{
  "module": "模块名",
  "purpose": "这个模块负责什么，一句话",
  "evidence": [
    {
      "statement": "一句完整的事实陈述，写清做了什么、怎么做的、解决了什么问题",
      "category": "architecture|feature|quality|data|ops",
      "capabilities": ["体现的能力关键词，如 异步任务/状态机/缓存一致性"],
      "refs": [{"path": "相对路径", "line": 12, "snippet": "该行附近的代码片段，不超过 80 字"}]
    }
  ],
  "notable_patterns": ["值得讲的设计模式或取舍，没有就空数组"],
  "risks": ["代码里能看出的局限或未完成部分，没有就空数组"]
}

硬性规则：
1. 每条 evidence 必须带至少一个 refs，path 与 line 必须来自你实际读到的代码。禁止编造行号。
2. statement 只描述代码里确实存在的行为，不要推测业务规模、QPS、用户量等源码里没有的数据。
3. 最多 8 条 evidence，按对求职的价值排序。
4. 不要写"采用了优秀的设计"这类空话；要写具体机制。
5. 无实质内容的模块返回空 evidence 数组。
"""

SYNTH_PROMPT = """你是项目证据汇总员。把多个模块的分析合并成一份可用于简历和面试的项目事实集。

只输出 JSON：
{
  "project": {
    "name": "",
    "one_liner": "",
    "tech_stack": [],
    "overview": "3-5 句项目概述，覆盖目标、架构、关键实现"
  },
  "evidence": [
    {
      "statement": "句首用动词，写清个人可主张的工作与结果；不得夸大",
      "category": "architecture|feature|quality|data|ops",
      "capabilities": [],
      "refs": [{"path": "", "line": 0, "snippet": ""}],
      "source_modules": []
    }
  ],
  "interview_questions": [
    {"question": "面试官可能怎么问", "angle": "考察点", "hint": "回答应覆盖的技术点，不要写成完整答案"}
  ],
  "weak_spots": ["项目中明显薄弱、面试容易被问倒的地方"]
}

硬性规则：
1. evidence 必须来自输入，禁止新增。合并同一主题时保留全部 refs。
2. 不要编造指标。源码里没有的性能数据、用户量、上线结果一律不写。
3. interview_questions 输出 12-18 条，由浅入深；必须针对具体代码，不要通用八股。
4. weak_spots 要诚实，用于你提前准备，不是给面试官看的。
5. evidence 最多 24 条。
"""

GUIDE_PROMPT = """你是项目导学作者。基于给定的项目事实，写出用于自我复习与面试准备的材料。

输出 Markdown（不要 JSON，不要代码块包裹整篇），结构如下：

# {项目名} 导学

## 一句话定位
## 架构与调用链
（用文字描述主要模块如何协作，标注关键文件）
## 我做了什么（可直接用于简历）
（每条一行，动词开头，只写事实，末尾附 `（path:line）`）
## 核心模块拆解
（每个重要模块：职责、关键实现、设计取舍）
## 面试问答
（12-18 组，每组：**Q：** … / **A 要点：** 3-4 条，必须引用具体文件或机制）
## 可能的追问与薄弱点
（诚实列出，并给出准备方向）

规则：
1. 只能使用输入的事实，不得补充未出现的技术或数据。
2. 问答要能落到代码，避免"谈谈你的理解"这类空题。
3. 不要写"综上所述""总而言之"这类填充句。
"""


async def scout(handle: RunHandle, scan: ScanResult) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("project.scout", SCOUT_PROMPT, "项目侦察")
    module_lines = "\n".join(
        f"- {m.name}（{m.lines} 行，{m.lang}）: "
        + ", ".join(f.path for f in m.files[:6])
        for m in scan.modules
    )
    reply = await handle.call_model(
        [
            {"role": "system", "content": SCOUT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"项目根目录：{scan.root}\n"
                    f"文件数 {scan.file_count}，总行数 {scan.total_lines}\n\n"
                    f"README 摘要：\n{scan.readme[:4000]}\n\n"
                    f"模块清单：\n{module_lines}\n\n"
                    f"git 信息：\n{json.dumps(scan.git, ensure_ascii=False)[:1500]}"
                ),
            },
        ],
        agent="project.scout",
        prompt_name="project.scout",
        prompt_hash=prompt_hash,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return _load(reply.content)


async def read_module(
    handle: RunHandle, module: ModuleInfo, focus: str
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("project.reader", READER_PROMPT, "模块代码分析")
    files = read_module_files(module)
    if not files:
        return {"module": module.name, "purpose": "", "evidence": [], "notable_patterns": [], "risks": []}

    body = "\n\n".join(
        f"### {f['path']}（{f['lines']} 行）\n```\n{f['content']}\n```" for f in files
    )
    try:
        reply = await handle.call_model(
            [
                {"role": "system", "content": READER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"模块：{module.name}\n关注点：{focus or '整体实现'}\n\n"
                        f"代码：\n{body}"
                    ),
                },
            ],
            agent=f"reader.{module.name}",
            prompt_name="project.reader",
            prompt_hash=prompt_hash,
            temperature=0.15,
            response_format={"type": "json_object"},
        )
        data = _load(reply.content)
        data["module"] = module.name
        return data
    except Exception as exc:  # noqa: BLE001 - 单模块失败不拖垮整体
        await handle.log(f"模块 {module.name} 分析失败：{exc}", level="warning")
        return {
            "module": module.name,
            "purpose": "",
            "evidence": [],
            "notable_patterns": [],
            "risks": [],
            "error": str(exc)[:200],
        }


async def synthesize(
    handle: RunHandle, *, scout_data: dict[str, Any], readers: list[dict[str, Any]]
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("project.synth", SYNTH_PROMPT, "项目证据汇总")
    payload = {
        "scout": scout_data,
        "modules": [
            {
                "module": r.get("module"),
                "purpose": r.get("purpose"),
                "evidence": r.get("evidence", []),
                "notable_patterns": r.get("notable_patterns", []),
                "risks": r.get("risks", []),
            }
            for r in readers
            if r.get("evidence")
        ],
    }
    reply = await handle.call_model(
        [
            {"role": "system", "content": SYNTH_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        agent="project.synth",
        prompt_name="project.synth",
        prompt_hash=prompt_hash,
        temperature=0.2,
        response_format={"type": "json_object"},
        stream=True,
    )
    return _load(reply.content)


async def run_pipeline(handle: RunHandle, scan: ScanResult) -> dict[str, Any]:
    """scout → 并行 reader → synth。"""
    await handle.log("阶段 1/3：侦察项目结构")
    try:
        scout_data = await scout(handle, scan)
    except Exception as exc:  # noqa: BLE001
        await handle.log(f"侦察失败，退化为纯结构分析：{exc}", level="warning")
        scout_data = {
            "project_name": scan.root.split("\\")[-1],
            "one_liner": "",
            "tech_stack": sorted({m.lang for m in scan.modules}),
            "modules": [{"name": m.name, "priority": 3, "reason": "", "focus": ""} for m in scan.modules],
            "interview_angles": [],
        }

    priorities = {
        str(item.get("name") or ""): item for item in scout_data.get("modules", [])
    }
    targets = sorted(
        scan.modules,
        key=lambda m: -int(priorities.get(m.name, {}).get("priority", 3)),
    )[:8]

    await handle.log(f"阶段 2/3：并行分析 {len(targets)} 个模块")
    readers = await asyncio.gather(
        *[
            read_module(handle, m, str(priorities.get(m.name, {}).get("focus") or ""))
            for m in targets
        ]
    )

    await handle.log("阶段 3/3：汇总证据")
    merged = await synthesize(handle, scout_data=scout_data, readers=list(readers))
    merged["scout"] = scout_data
    merged["readers"] = list(readers)
    return merged


def _load(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON: {text[:160]}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return data
