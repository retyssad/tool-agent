# 多智能体只用于上下文隔离与角色隔离

除以下三处，所有功能都是单 agent 顺序流程 + 确定性工具：

- **项目证据采集**：scout 扫目录 → 按模块并行 reader → synthesizer 输出带 `file:line` 的项目证据。开子 agent 的唯一理由是仓库上下文过大，需要隔离。
- **简历评价**：四个 critic 并行（JD 匹配、成果量化、技术深度、表达与篇幅），各自独立上下文，避免互相锚定。
- **模拟面试**：interviewer 与 evaluator 分离，避免同一上下文自问自评。

JD 解析、简历改写、导学内容生成用单 agent。PDF 导出、模板内联、账本校验、PDF 文本抽取是纯代码。**所有子 agent 只读、只返回结构化结果，不写数据库**；落库由 orchestrator 统一执行。

## Rejected

agent 网络/自由 handoff：行为不可预测、成本不可控、无法回放。OpenAI Agents SDK 只用来做 agent 定义与 run 执行，不用它做编排拓扑。
