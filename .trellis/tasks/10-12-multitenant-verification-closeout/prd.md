# 多租户任务树复核与收尾

## Goal

复核 `10-03-multi-tenant-agent-concurrency-storage` 及其子任务的**真实**完成度，
补完其中可自主执行的验收，修掉复核中发现的缺陷，并把仍然开放的工作明确登记为
有 owner、依赖与恢复条件的条目。

## Background

十个未归档任务的 checklist 与 main 实际状态严重脱节：多项标 `[ ]` 的实现早已在
main（circuit breaker、writer epoch、capacity counter、stream quota、PgBouncer/PITR、
真实快照导入、dev 灰度）。这些过期条目有两个实际危害：

1. 父任务与编排任务把已完成的前置当未完成，**阻塞** lexical / pgvector 两个子任务；
2. 后续模型可能重做已完成的工作。

## Requirements

- R1 在真实 PostgreSQL（隔离库）上复跑容量/故障/跨实例/对账四类探针，补完可自主执行的验收。
- R2 复核并修正各任务 checklist，只把**有直接证据**的项标为完成。
- R3 修掉复核中发现的真实缺陷。
- R4 仍然开放的项必须写明「为什么还开着」，不得笼统写「依赖环境」。
- R5 不接触线上；不修改其他 agent 正在处理的任务目录。

## Acceptance Criteria

- [ ] 四个探针在隔离库上 PASS（含反证），证据落盘。
- [ ] 开发库只读对账无差异。
- [ ] 各任务 implement.md 的完成项均有证据引用。
- [ ] 发现的缺陷有回归测试，且有反证证明该测试承重。
- [ ] 全量 pytest 与 mypy 通过。
- [ ] 未触碰 `.trellis/tasks/10-12-agent-tool-registry-parity/`。

## Scope boundaries

- 不改业务语义（唯一例外是 R3 修掉的缺陷）。
- 不做需要真实多实例/尾延迟/线上发布的验收——那些属父任务 PRD 的停止条件。
