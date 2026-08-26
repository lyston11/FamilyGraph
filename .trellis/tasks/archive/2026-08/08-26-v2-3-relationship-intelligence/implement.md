# V2.3 Relationship Intelligence 实施计划

- [x] 定义 SourceFact 类型、状态、provenance/revision 与迁移；将 v1 结构边映射为新领域命令（仅空库 fixture，无生产回填）。（E1）
- [x] 实现 scoped graph builder、确定性 path/concept resolver、主/替代路径与 evidence hash。（E2，代数概念码 Um-Uf-Bm 等）
- [x] 建立 system/locale/space/personal TermRegistry 与原文存储。（E3）
- [x] 实现 personal correction、term usage、两位确档用户空间建议规则和 DomainEvent。（E3）
- [x] 实现 DerivedFact cache、dirty/invalidation/rebuild 与查询 API。（E2+E3 /api/kinship/resolve）
- [x] 实现 ProfileIntakeExtractor schema、四级 resolution、一次追问与后端候选校验。（E4a + POST /api/kinship/parse）
- [x] 扩展 Assistant tools/UI：三新工具@1（resolve_free_text_relation/get_term_alternatives/record_term_usage 同意门控）+ 前端 RelationLookup/KinshipTermPanel/kinship store。（E4a/b/c）
- [x] 建立大规模关系 fixture 与 property/invariant tests，覆盖多路径、环、冲突、跨空间隔离。（E2/E3/E4a 测试套件）

## 验证

```bash
cd backend && pytest      # 375 passed
cd backend && mypy app    # clean
cd agent && npm run type-check && npm run lint && npm test   # 53 passed
cd frontend && npm run type-check && npm run lint && npm test && npm run build   # 132 passed
```

Compose 真实联调（2026-08-26）：flag 全开 + stub openai-compatible Provider；bootstrap→建档 4 人→容器内确认 SourceFact×3→resolve 黄金用例 Um-Uf-Bm/舅爷爷(locale)→parse「奶奶的兄弟」determined、「叔叔」supported 提案不写事实→个人纠正即时生效→两位确档用户同词晋升 promoted:true→sidecar 租约+上下文+Pi 会话构造(12 工具)+stub 模型 settle succeeded+SSE 重放→撤销 fact 后 resolve 立即 found=false（AC-KI8）。docker-compose.yml 已加 RELATIONSHIP_INTELLIGENCE_ENABLED 与 AGENT_PROVIDER_CLOUD_* 透传。

固定 golden cases 至少包括：爷爷/外公系、奶奶兄弟、父母未知 sibling、收养/继亲、配偶父母、伴侣未披露、不同空间不同词、两个用户同词晋升与撤销。

## 回滚

SourceFact 迁移、resolver、TermRegistry、Extractor、UI 分提交；关闭 relationship-intelligence flag 后 Assistant 回退到 V2.2 结构路径解释，不删除 SourceFact 真源。
