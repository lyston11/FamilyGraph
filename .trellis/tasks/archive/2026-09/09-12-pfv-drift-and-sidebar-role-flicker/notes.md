# 交付与部署提示

修复覆盖服务重启后存量 `pfv-v1`/旧 policy 视图的自愈路径。上线后仍需在服务器执行 `systemctl --user restart familygraph-api` 使新代码生效；无需直接修改 `personal_family_views`，周期 integrity scan 会在后续周期重建漂移行。
