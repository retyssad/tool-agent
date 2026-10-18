# resume.json 是生成简历的唯一事实源，PDF 只是派生产物

生成简历以结构化 JSON 存储（`data/resumes/generated/{version_id}/resume.json`），HTML 与 PDF 由它渲染，可随时用任意模板重建。理由：只有结构化数据才能支持岗位定向评价、与证据账本逐条比对、模板随时切换、版本间 diff。若把 HTML 或 PDF 当事实源，这三件事都会退化成文本比对。

## 证据门禁

每个 bullet 强制携带非空 `claim_ids`，指向证据账本中状态为「已确认」的条目。导出前校验：引用不存在、指向待确认/已过期/不采用条目、或 bullet 无 claim 引用，一律阻断导出并显示原因。模型只允许在证据上重组措辞，不允许引入新事实。

## Consequences

导入简历走另一条路径，不转成 `resume.json`：只保留原始 PDF 与抽取文本。用户手动编辑 UI 直接修改 `resume.json`，不绕过门禁；"手动覆盖"若要放开，必须显式记录为一条人工证据条目，而不是留一个后门。
