# Implement — Steward 后续能力：授权知识、个人路径解释与地区称谓

## 开始条件

- [ ] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [ ] 确认依赖：`09-11-steward-release-observability`。
- [ ] 取得最新实施摘要的明确批准再 task.py start；当前仅规划。

## 有序执行

- [ ] 1. 在六个修复任务验收后收集匿名/合成用例，逐能力做价值对照。
- [ ] 2. 盘点现有 shared RAG/TermRegistry/PFV 解释接口，不重复已有功能。
- [ ] 3. 编写权限、撤销、输入证据及质量 fixture 的研究样例，保持产品代码不变。
- [ ] 4. 用明确样本询问首批资料/地区/展示需求，再拆可执行子任务并重新评审。
- [ ] 5. 若未达到增益或容量门槛，notes 记录“不实施”理由，不能为完成任务盲目上线。

## 改动边界与重用位置

- `backend/app/services/memory_rag.py`
- `backend/app/services/terms.py`
- `backend/app/services/personal_family_view.py`
- `backend/app/services/steward_assist.py`

## 验证计划

本任务研究阶段只核验文档/证据/场景，不跑生产匹配或外发请求。将来实现另立验证任务。

## 回滚与交接

- [ ] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [ ] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [ ] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
