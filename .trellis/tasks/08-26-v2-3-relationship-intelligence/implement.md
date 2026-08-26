# V2.3 Relationship Intelligence 实施计划

- [ ] 定义 SourceFact 类型、状态、provenance/revision 与迁移；将 v1 结构边映射为新领域命令（仅空库 fixture，无生产回填）。
- [ ] 实现 scoped graph builder、确定性 path/concept resolver、主/替代路径与 evidence hash。
- [ ] 建立 system/locale/space/personal TermRegistry 与原文存储。
- [ ] 实现 personal correction、term usage、两位确档用户空间建议规则和 DomainEvent。
- [ ] 实现 DerivedFact cache、dirty/invalidation/rebuild 与查询 API。
- [ ] 实现 ProfileIntakeExtractor schema、四级 resolution、一次追问与后端候选校验。
- [ ] 扩展 Assistant tools/UI，展示路径、词源、替代称谓、事实状态和纠正入口。
- [ ] 建立大规模关系 fixture 与 property/invariant tests，覆盖多路径、环、冲突、跨空间隔离。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd agent && npm run type-check && npm run lint && npm test
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

固定 golden cases 至少包括：爷爷/外公系、奶奶兄弟、父母未知 sibling、收养/继亲、配偶父母、伴侣未披露、不同空间不同词、两个用户同词晋升与撤销。

## 回滚

SourceFact 迁移、resolver、TermRegistry、Extractor、UI 分提交；关闭 relationship-intelligence flag 后 Assistant 回退到 V2.2 结构路径解释，不删除 SourceFact 真源。
