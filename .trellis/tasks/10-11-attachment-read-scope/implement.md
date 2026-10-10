# 实施计划

- [x] task.py start 并进入任务 worktree；把主检出新建任务产物带入任务分支。
- [x] 后端 visibility 暴露来源，附件两个 GET 统一空间与类型授权。
- [x] 前端列表/媒体透传同一空间；空间切换与卸载清理、拒绝迟到响应。
- [x] API 测试覆盖空间覆盖、类型组合、缺失上下文、非成员/pending；组件测试覆盖空间切换迟到响应。
- [x] 跑受影响 pytest/vitest、改动文件 lint/format 与类型检查；修复本次引入的失败。
- [ ] 更新附件 spec、记录验证；任务分支提交并推送，串行集成归档后清理 worktree/分支。

## 验证范围

backend: tests/test_m3a_attachments.py、test_disclosure_scoped_api.py、既有授权/最小节点相关回归；mypy app；修改文件 ruff。
frontend: attachmentMedia.spec.ts、AttachmentsSection.media.spec.ts、ProfileDrawer 相关测试；type-check、改动文件 lint。
不改动上传删除与其它业务路径，不为本任务跑无关全量回归。

## 验证结果

- 附件/逐空间披露 pytest：23 passed。
- system_admin_boundary/custody/space_access_boundary：29 passed。
- attachmentMedia/AttachmentsSection.media/ProfileDrawer 两组 vitest：21 passed。
- mypy app：228 source files 通过；vue-tsc --noEmit 通过。
- 后端三处改动文件 ruff check/format 通过，前端四处改动文件 eslint 通过。
- 未运行全量后端/前端测试与线上 smoke：本次限定附件读取，未启动或修改运行环境。无迁移。
- ProfileDrawer 当前只有测试引用；本次修正其附件组件的上下文行为，不新增产品页面入口，不宣称已在线部署。
