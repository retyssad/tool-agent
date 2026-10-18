# skill库

求职工作台使用的 Agent Skill 资产库，取自 [ASu-skills](https://github.com/Hisn00w/ASu-skills)（MIT）。

- 上游提交：`8d93501a55ec7b9143536a50e8184b269ebd2e2f`（2026-10-13）
- 保留原始目录语义：`skills/` 与 `assets/`、`scripts/` 同级，因此各 `SKILL.md` 里的 `assets/...`、`scripts/...` 相对路径**不需要改**

## 保留的 4 个

| skill | 对应功能 | 在工作台里的角色 |
|---|---|---|
| `make-resume` | 简历制作 | 渲染层：18 套 HTML 模板 + ASu 默认模板，导出 PDF / LaTeX |
| `great-resume` | 简历制作 | 内容层：把真实经历改写成岗位相关的 bullet、HR 开场白 |
| `project-guide` | 模拟面试 | 基于项目源码生成项目面经、STAR 题与追问材料 |
| `interview` | 模拟面试 | 从简历 Claim 生成面试契约、逐轮追问、评分与复练 |

## 已删除

| skill | 原因 |
|---|---|
| `job-match` | 岗位匹配分析由工作台自己的 JD schema + 解析/对比流程承担 |
| `evidence-recap` | 项目证据复盘，与 `project-guide` 重叠 |
| `contributor` | 开源贡献发现与 PR 提交，不在四个功能内 |
| `job-apply` | 浏览器自动填表，且依赖未复制的 Kimi WebBridge 脚本 |
| `offer` | 生成 HTML 进度表，状态本来就在数据库里 |

`skills/*/agents/openai.yaml` 也已删除（编辑器插件元数据）。

已清理：这几个 SKILL.md 里原本指向已删除 skill 的文字路由（`/job-match`、`/contributor`、`/offer`、`/job-apply`）已全部改写或移除，只保留四个现有入口之间的路由。

## 目录结构

```
skill库/
├── skills/
│   ├── make-resume/        SKILL.md + references/
│   ├── great-resume/       SKILL.md + references/（含主张—证据账本规范）
│   ├── project-guide/      SKILL.md + references/（课程/面经规范）
│   └── interview/          SKILL.md + references/（契约、评分、复练）
├── assets/
│   ├── asu-resume/         默认模板内容壳
│   ├── templates-html/     18 套模板壳（设计稿，不可直接交付）
│   ├── frame/              共享样式/工具栏/编辑器（含 asu/ 扩展）
│   ├── latex-resume/       3 套 LaTeX 母版 + 公共导言区
│   ├── icons/ logos/       联系方式图标、公司 Logo
│   ├── asu-resume-template.html      已构建好的 ASu 模板
│   ├── resume-template-editable.html 旧版可编辑模板
│   ├── resume-data-template.json     简历结构化数据模板
│   ├── career-claim-ledger-template.json 主张—证据账本模板
│   ├── fictional-resume-photo.png    虚构示例证件照
│   └── template-overview.jpg
├── scripts/
│   ├── build-asu-resume.mjs    ASu 内容壳 → 自包含 HTML
│   ├── inline-template.mjs     任意模板壳 → 自包含 HTML
│   ├── build-latex-resume.mjs  简历 JSON → .tex
│   ├── export-resume-pdf.mjs   HTML → PDF（需要本机 Edge/Chrome）
│   ├── project_guide.py        项目导学面经的确定性校验/提示构建
│   └── validate_claim_ledger.py 证据账本校验
└── LICENSE
```

## 已验证

在本目录执行过：

```bash
node scripts/build-asu-resume.mjs --check                  # 产物一致
node scripts/build-asu-resume.mjs assets/asu-resume/template.html out.html
node scripts/inline-template.mjs assets/templates-html/01-大厂极简简历模板.html out.html
node scripts/build-latex-resume.mjs assets/resume-data-template.json out.tex
node scripts/export-resume-pdf.mjs out.html --out out.pdf   # 实际导出成功
python scripts/validate_claim_ledger.py assets/career-claim-ledger-template.json
python scripts/project_guide.py --help
```

18 套模板全部内联成功，文档内部相对链接无失效。运行时依赖：Node ≥ 22.19、Python ≥ 3.10（脚本零第三方依赖）。

## 接入工作台的建议

1. **SKILL.md 是提示词资产，不是可执行代码。** 按 frontmatter 切成 `name / description / body` 存进 `prompts` 表并打版本号；run 记录里存 `skill@hash`，才能回放和评测。
2. **确定性部分包装成 FastAPI 工具。** `build-asu-resume.mjs`、`inline-template.mjs`、`build-latex-resume.mjs`、`export-resume-pdf.mjs`、`project_guide.py`、`validate_claim_ledger.py` 都零第三方依赖且可重复执行，直接 `subprocess` 调用并作为 Tool 暴露，入参出参用 Pydantic 定义。
3. **简历事实基线用 `career-claim-ledger`。** `已确认 / 待确认 / 已过期 / 不采用` 正好对应“哪些内容能进最终 PDF”，建议建表而不是只当 JSON 文件。
4. **模板壳不能直接交付。** 必须过 `inline-template.mjs` / `build-asu-resume.mjs`。工作台里把“内联构建”设为简历导出流水线的固定一步。
5. **JD 解析要自己写。** 现在库里的 skill 都假设已有结构化 JD；从 Boss 复制的原始 JD → 岗位卡片（职责 / 硬性要求 / 技能栈 / 级别 / 薪资 / 地点）需要工作台自己定义 schema 和 prompt，产物再喂给 `interview`、`great-resume`。
