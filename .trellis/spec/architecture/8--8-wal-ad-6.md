# 8. 备份恢复（修正 WAL 直接复制缺陷）`[AD-6]`

- 备份命令：`python -m app.backup`（容器内执行），使用 **SQLite online backup API**（`Connection.backup`）产出一致性快照至 `/data/backups/familygraph-YYYYmmdd-HHMMSS.db`，随后与 `/data/uploads` 一同 tar 归档。
- 恢复演练是 M4 出口条件：restore 后 `PRAGMA integrity_check` 通过 + 用户数/关系数与源库一致。
- README 写明：**禁止**运行期直接 cp 主库文件。