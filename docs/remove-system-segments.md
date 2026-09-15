# 系统线段功能移除

系统自动线段算法、存储读写、API 字段、主图图层、命中区域及特征序列证据已移除。
保留行情、笔、中枢、走势、人工修正、普通手动画线及历史研究文档。
层级算法中连续单位的分组（segment）和几何线段距离计算不属于缠论系统线段，保持不变。

## 已有数据库迁移

先停止正在访问该数据库的服务，再执行：

```sh
.venv/bin/python scripts/migrate_remove_segments.py --db data/chant_agent.db
.venv/bin/python scripts/migrate_remove_segments.py --db data/chant_agent.db --execute
```

迁移默认只读预览；执行时先通过 SQLite backup API 创建独立备份。
迁移事务仅删除 `period_segments` 表及其索引，不修改任何其他表。
提交前比较所有其他表的行数、完整内容 SHA-256 和结构，并检查数据库完整性；异常则回滚。
备份保留原有数据用于恢复，不清理历史备份或日志。

代码指纹发生变化后，服务按原有机制在需要时生成新的结构快照；不删除历史快照。
前端旧的线段图层设置不再参与显示，不清空用户的其他浏览器设置。
