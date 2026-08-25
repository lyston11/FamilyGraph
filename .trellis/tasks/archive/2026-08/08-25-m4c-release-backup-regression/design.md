# m4c 技术设计

- app/backup.py：SQLite online backup API（Connection.backup）→ /data/backups/
  familygraph-YYYYmmdd-HHMMSS.db，随后与 uploads 一并 tar 归档
- 恢复演练：restore 后 PRAGMA integrity_check + 行数对比（测试内验证）
- README 补备份说明（禁 cp 主库）+ 迁云清单
