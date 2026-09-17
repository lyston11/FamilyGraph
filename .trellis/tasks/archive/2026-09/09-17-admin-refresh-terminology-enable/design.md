# 设计

## 依据与最小边界
已复现 main.ts 安装 router 后发起 restoreSession，guard 看到 restoring=true 跳过等待后重定向 login，随后恢复成功也不离开登录页。当前 restoreSession 直接 requestRefresh，tryRefresh 才有单飞，两路径未共享。

修复位于管理员启动、路由与 auth store：使用既有单飞轮换承接所有恢复入口；路由必须等待在途恢复。选择单一启动恢复责任（优先路由），避免重复发起；初始导航完成后挂载。添加真实延迟而非立即 resolved 的回归。具体最小改动经实现者核对所有调用点后确定。

保持登录响应验证、失败清会话、首改密门禁及域隔离。仅修改必要的管理员入口/guard/store/tests 和相关规范。

## 运维
只读核实 env、platform、space、service unit；安全备份部署 env（0600、不输出敏感值），原子修改唯一 STEWARD_ASSIST_TERMINOLOGY=1，重启实际消费该配置的 API 服务。保留空间 2 已有 enabled/cloud_allowed/assist_terminology=1，不写其他空间许可。使用只读 DB session 和已加载部署环境核实生效逻辑、Provider/策略与作业观测。正常 worker 自主消费，不往生产植入验收人物或强制写投影。

失败回退仅本次 env 键并重启，不整体覆盖用户同时发生的配置更改。启用是否有效与模型是否产生改善独立记录；当前不保证存在改善候选。
