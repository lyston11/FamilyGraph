# 全局架构规范

> 路由器：只用于按任务发现独立架构合同，不复制合同正文。

## 适用范围

- 涉及身份、可见性、空间状态、授权、数据权利、数据库约束、管理员平台或附件安全的任务。
- 只读取与当前行为边界直接相关的叶文件；不要默认读取全部架构合同。

## 合同叶文件

- [0--0-v2-2026-08-26-v1](0--0-v2-2026-08-26-v1.md)
- [0-10-0-10-2026-09-05-09-05-family-account-provisioning](0-10-0-10-2026-09-05-09-05-family-account-provisioning.md)
- [0-8-0-8-2026-08-31-bootstrap-12-12-1-space_admin](0-8-0-8-2026-08-31-bootstrap-12-12-1-space_admin.md)
- [0-9-0-9-2026-09-01](0-9-0-9-2026-09-01.md)
- [10--10-ad-8-v1-handoff](10--10-ad-8-v1-handoff.md)
- [11--11-personalfamilyview-bridge-2026-09-01](11--11-personalfamilyview-bridge-2026-09-01.md)
- [12--12-listener-2026-09-04-0-8-bootstrap](12--12-listener-2026-09-04-0-8-bootstrap.md)
- [12-1-12-1-admin-api-v1-2026-09-05](12-1-12-1-admin-api-v1-2026-09-05.md)
- [13--13-production-deployment-2026-09-20](13--13-production-deployment-2026-09-20.md)
- [2--2-ad-2](2--2-ad-2.md)
- [3--3-ad-3](3--3-ad-3.md)
- [4--4-ad-4](4--4-ad-4.md)
- [5--5](5--5.md)
- [6--6-visibility-py](6--6-visibility-py.md)
- [7--7-m1-m4-ad-5](7--7-m1-m4-ad-5.md)
- [8--8-wal-ad-6](8--8-wal-ad-6.md)
- [9--9-ad-7](9--9-ad-7.md)

## 验证入口

- 按叶文件中的 Required validation 执行对应后端/前端测试。
- 涉及跨层数据流时，补读 `.trellis/spec/guides/cross-layer-thinking-guide.md`。
