# chant_agent 仅保留现行数据与可用脚本清理计划

## 1. 用户确认与目标

用户要求只保留最新的数据和最新可用的脚本，删除历史离线数据和失效脚本。2026-09-23 的补充确认明确：

- 只删除离线归档，保留生产库中的完整历年行情；这些行情是分型、笔和中枢重新计算的输入。
- 保留最新一份可用回滚备份，删除更早的备份。
- 保留用户绘图及当前活动结构运行；不停止或清空正在提供服务的生产库。

本计划取代原走势下线计划中“保留 v28/v29 发布归档”的约定，但不改变已经上线的笔、中枢和 L1→L2 计算契约。

## 2. 保留边界

- `data/chant_agent.db`、`data/chant_agent.db-wal`、`data/chant_agent.db-shm`：共同组成运行中的生产数据库，均不得删除、移动或单独复制。
- 生产库中的 `market_bars` 全量历史、`drawing_objects`、活动笔、中枢、晋级候选、段证明和父子关系。
- `data/backups/pen-center-l2-20260923T053834Z/`：最新一份经 `quick_check` 与 `foreign_key_check` 验证的数据库/WAL/SHM 回滚备份。
- `data/imports/`、`data/index_catalog_20260910.csv`：不属于已确认删除的离线版本归档，暂不处理。
- 现行脚本：`scripts/audit_structure.py`、`scripts/recalculate_structure.py`、`scripts/migrate_pen_center_l2.py`、`scripts/migrate_realtime_period_keys.py`。
- `docs/plans/`、`docs/reports/` 中的文字记录，以及与本次清理无关的用户改动。

## 3. 删除边界

删除忽略于 Git 的旧离线版本归档：

- `data/v28-release-20260919/`
- `data/v29-candidate-20260920/`
- `data/v30-candidate-20260920/`

删除早于最新回滚备份的三个备份目录：

- `data/backups/directional-v28-20260919/`
- `data/backups/chant_agent-before-realtime-refresh-20260922-104012/`
- `data/backups/pen-center-l2-pre-cutover-20260923T053743Z/`

删除只服务旧版本发布、旧架构比较或旧冻结基线的脚本：

- `scripts/compare_directional_runs.py`
- `scripts/publish_directional_runs.py`
- `scripts/rebuild_v25_database.py`
- `scripts/recalculate_frozen_baseline.py`
- `scripts/validate_v30_replay.py`
- `scripts/verify_z_wave_refactor.py`

## 4. 依赖调整

- 删除 `tests/test_migration.py` 中针对已退役 v25 重建工具的测试；现行迁移由 `tests/test_pen_center_l2_migration.py` 覆盖。
- 将 `tests/test_v31_candidate_ownership.py` 中依赖 v30 隔离数据库且在数据库缺失时静默跳过的测试，改写为纯序位合成笔测试，保留候选所有权覆盖而不保留历史行情归档。
- 搜索 `app/`、`scripts/`、`tests/`，确认没有指向已删除脚本或归档的运行时依赖。历史文档中的描述保留为文字记录，不作为可执行入口。
- 不触碰同时进行的实时性能计划及其未提交改动。

## 5. 执行顺序

1. 核对 `git status`、归档实际路径、目录大小和文件打开状态；确认只命中上述十二个待删路径和两个待调整测试。
2. 对最新保留备份的主库、WAL、SHM 执行 `quick_check` 与 `foreign_key_check`，记录活动运行、行情和绘图数量。
3. 使用 `apply_patch` 删除旧脚本、旧测试，并更新 v31 测试；先运行相关测试以发现依赖断裂。
4. 确认归档目录无打开文件后，删除明确列出的三个归档和三个旧备份目录；不使用通配符删除生产数据库或 `data/backups` 整体。
5. 再次核对 `app/`、`scripts/`、`tests/` 的引用，执行后端完整测试、前端测试与构建。
6. 检查生产 `/api/health`、所有周期 API 审计、数据库 `quick_check` 和 `foreign_key_check`，确认现行服务没有因清理受到影响。
7. 报告实际删除路径、保留路径、释放空间、测试结果和任何未验证内容；不提交 Git。

## 6. 停止与回滚

- 若保留备份损坏、旧归档仍被进程打开、清理范围超出上述清单，或测试/API/数据库校验失败，停止后续删除。
- 已删除的 Git 跟踪脚本可从版本控制恢复；已删除的 Git 忽略归档不保证可恢复，不能把生产数据库的回滚备份误称为这些归档的备份。
- 生产数据库三件套始终不在本次删除集合中；任何新的生产库迁移、行情裁剪或用户绘图删除均需另行明确授权。
