# 本地验证与照片只读巡检

`test-backend.ps1` 使用 `JAVA_HOME` 和 PATH 上的 Maven；当前开发机也有已知的 JBR/Maven 回退路径。普通回归：

```powershell
& ./tools/test-backend.ps1 -MavenArguments @('test')
node --test ./tools/tests/admin-frontend.test.cjs
```

`MySql*Test` 仅在 `FACE_TEST_DB_URL` 指向 `jdbc:mysql://127.0.0.1:<独立端口>/face_test` 时启用，且在变更 fixture 前再次检查实际库名。它们会重置合成数据；必须使用专用临时实例和测试账号，设置 `FACE_TEST_DB_USER`、`FACE_TEST_DB_PASSWORD`，不要把业务库代理到该地址。没有环境变量时这些用例跳过，不代表 MySQL 验证通过。

容量工具均只接受 loopback 地址，服务默认 `http://127.0.0.1:28080`：

- `verify-capacity.py`：需 `FACE_VERIFY_PASSWORD`（测试 admin 密码），十万合成人员、200 个组、1024 维特征；验证 20 并发下四类管理页、字段和旧接口边界。
- `verify-sync-capacity.py`：需 `FACE_VERIFY_DEVICE_TOKEN`（合成的 allGroups 设备凭据），默认预期十万行；逐对象解析 v1、验证缓存文件一致、跑完整 v2 初始化和 ACK。它会建立/释放测试设备租约，但不修改人员。`FACE_VERIFY_EXPECTED_ROWS` 可调整预期数量。
- `verify-slow-sync.py`：需上述密码及设备凭据；占用两个慢下载，确认第三个请求返回 429、50 次管理页仍响应，并在默认 60 秒下载期限后检查连接结束和许可恢复。运行约 80 秒，需要足够大的合成快照，不能用小文件代替慢写场景。

所有容量脚本用 `FACE_VERIFY_OUTPUT` 指定 JSON 输出路径；它们不负责建库/灌数。灌数、探针和 fixture 测试不能同时运行。容量测试后，应先停止探针/服务并单独重建隔离测试库，再运行小数据 fixture；否则 fixture 的全表 DELETE 可能被正确配置的 5 秒 SQL 时限取消，不应为此放宽生产时限。具体本次数据与结果见实施验证报告，不能据本机结果直接承诺现场容量。

## PhotoOrphanAudit（永不删除）

需要 JDK 17+、MySQL Connector/J，以及只读数据库账号。先确认目标、目录、巡检时段；通过进程环境提供 `FACE_DB_URL`、`FACE_DB_USER`、`FACE_DB_PASSWORD`、`FACE_PHOTO_AUDIT_DIR`，不要把密码写在命令行或提交到仓库。先完成本次数据库迁移，否则历史照片列不存在时应停止巡检。

Windows 示例（替换编译目录和驱动路径）：

```powershell
javac -d <巡检类目录> ./tools/PhotoOrphanAudit.java
java -cp '<巡检类目录>;<mysql-connector-j.jar完整路径>' PhotoOrphanAudit > photo-audit.csv
```

工具只遍历照片目录第一层的普通文件，不跟随符号链接、不进入 `.staging` / `.cleanup` 子目录。不支持的旧文件名会单独报告；八天宽限内不判孤儿；其余查询当前人员与保留事件引用。输出 `orphan_candidate_review_required` 仅是人工核查候选，不能作为删除清单直接执行。大小写不敏感的数据库引用检查偏保守，可能多保留文件。

这是显式离线巡检：每个超过宽限的文件最多两次查询，十万文件最多二十万次查询，整体没有总时限、分页续扫或自动调度。单条查询上限 5 秒，驱动连接/读超时 2/10 秒；遇错中止，不隐瞒不完整结果。应在副本或维护时段运行并保留错误输出，结果文件也需按人员照片元数据保护。
