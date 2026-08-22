# TASK-2026-001 局域网单网卡开放设计

## 目标

将已验收的候选算法容器 `face-algorithm-task-2026-001` 从仅回环地址
`127.0.0.1:18002` 改为仅监听服务器业务网卡地址
`<server-37>:18002`，供当前 Windows 局域网客户端
`<lan-host>/24` 直接访问。

## 决策与边界

采用 Docker 的指定宿主机地址端口映射：

```text
<server-37>:18002 -> container:8000
```

不采用以下方案：

- 不绑定 `0.0.0.0`，避免同时暴露到服务器的 `<lan-host>` 网卡。
- 不修改原服务 `face-algorithm` 的 `18001` 端口或运行状态。
- 不修改模型、1024 维特征契约、阈值、算法配置或 API 响应。
- 本次不新增反向代理、TLS、鉴权或主机防火墙规则。

指定目标地址只限制服务在哪个网卡地址接收连接，不等同于来源网段 ACL。
凡是能够路由到 `<server-37>` 的主机，理论上都可以访问当前未统一鉴权的
v2 API。若后续需要严格限制为 `192.168.0.0/24`，应由管理员增加防火墙规则，
或在带鉴权的反向代理后提供服务。

`<server-37>` 当前由 NetworkManager 的 `auto` 连接提供，内核将其标记为
`dynamic`。本次切换只保证当前地址有效期间的容器和局域网访问；若要保证主机
重启或 DHCP 续租后地址不变，需要网络管理员配置 DHCP 地址保留或静态地址。
执行用户 `yzq` 没有管理员权限，本次不能增加来源网段防火墙规则。管理员后续
实施过滤时，需要结合 Docker/nftables 转发路径在 `DOCKER-USER` 等适当链路或
受控反向代理上配置并实测，不能只假定普通 UFW INPUT 规则会约束发布端口。

## 切换方式

Docker 不能原地修改已创建容器的端口映射，因此使用可回滚重建：

### 切换前置检查

1. 确认 `<server-37>/24` 仍属于 `ens11f1` 且网卡为 UP。
2. 确认除当前候选容器外，没有进程占用 `<server-37>:18002`。
3. 确认临时名称 `face-algorithm-task-2026-001-loopback-rollback` 未被占用，
   且本次候选容器 cidfile 不存在。
4. 检查活动连接、部署目录和已知服务配置，确认没有仍依赖
   `127.0.0.1:18002` 的本机调用方或监控；若存在，先迁移到新地址。
5. 确认镜像标签 `face-algorithm:task-2026-001-6200689` 仍精确指向已验收镜像
   `sha256:a1cab59178acca7b4ef18128fa6fb43d0a96b81582bccc66c45785ba2124b3bf`。
6. 保存候选容器完整 inspect 和日志；保存原服务 `face-algorithm` 的容器 ID、
   `StartedAt`、`RestartCount`、端口映射和 `/docs` HTTP 状态作为连续性基线。

### 精确重建配置

当前候选容器 ID 必须是切换开始时记录的值。停止后将其重命名为
`face-algorithm-task-2026-001-loopback-rollback`。为确保即使启动失败或 SSH 中断
也能可靠识别新容器，使用 `docker create` 与 `docker start` 两阶段执行，并把
容器 ID 持久化到 cidfile：

```bash
docker create \
  --cidfile /home/<user>/deployments/face-algorithm-task-2026-001/runtime/deployment-audit/lan-candidate.cid \
  --name face-algorithm-task-2026-001 \
  --user facealgo \
  --workdir /app \
  --network bridge \
  --restart unless-stopped \
  --read-only \
  --cpus 4 \
  --memory 6g \
  --memory-swap 6g \
  --pids-limit 512 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /app/tmp:rw,noexec,nosuid,size=2g,uid=10001,gid=10001,mode=0700 \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e GOTO_NUM_THREADS=1 \
  -e BLIS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e NUMEXPR_NUM_THREADS=1 \
  -e VECLIB_MAXIMUM_THREADS=1 \
  -e OPENCV_FOR_THREADS_NUM=1 \
  -e OMP_WAIT_POLICY=PASSIVE \
  -p <server-37>:18002:8000 \
  --mount type=bind,src=/home/<user>/deployments/face-algorithm-task-2026-001/runtime/algorithm.yaml,dst=/app/config/algorithm.yaml,readonly \
  --mount type=bind,src=/home/<user>/deployments/face-algorithm-task-2026-001/runtime/checkpoints/modnet_photographic_portrait_matting.ckpt,dst=/app/checkpoints/modnet_photographic_portrait_matting.ckpt,readonly \
  sha256:a1cab59178acca7b4ef18128fa6fb43d0a96b81582bccc66c45785ba2124b3bf

new_id=$(cat /home/<user>/deployments/face-algorithm-task-2026-001/runtime/deployment-audit/lan-candidate.cid)
docker inspect "$new_id" >/dev/null
docker start "$new_id"
```

必须在 `docker start` 前核对 cidfile、容器名称和实际 `.Image` 均符合预期。
镜像内置命令、健康检查和基础环境变量不覆盖。新旧容器的规范化 inspect 必须
逐字段比较，以下项目必须完全一致：实际 image ID、用户、工作目录、
Entrypoint、Cmd、Healthcheck、完整排序后的环境变量、两个挂载的源/目标/只读属性、
网络模式、重启策略、只读根文件系统、CPU/内存/swap/PID 限制、CapDrop、
SecurityOpt、tmpfs、日志驱动和轮转选项。允许变化的只有容器 ID、创建/启动时间，
端口映射的 HostIp 从 `127.0.0.1` 改为 `<server-37>`，以及 `.Config.Image`
引用文本从 tag 改为不可变 image ID；实际 `.Image` 必须仍精确为
`sha256:a1cab59178acca7b4ef18128fa6fb43d0a96b81582bccc66c45785ba2124b3bf`，HostPort
和容器端口仍分别为 `18002` 和 `8000/tcp`。

等待 Docker 健康检查及 readiness 成功后，从服务器和 Windows 客户端验证。
全部验收通过后才删除临时回滚容器，并更新部署验收记录。

线程环境变量必须完整保留，避免 96 核服务器再次出现视频解码子进程线程膨胀：

```text
OPENBLAS_NUM_THREADS=1
GOTO_NUM_THREADS=1
BLIS_NUM_THREADS=1
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1
OPENCV_FOR_THREADS_NUM=1
OMP_WAIT_POLICY=PASSIVE
```

## 失败处理与回滚

任何强制验收项失败，均不得接受新容器并必须执行回滚，包括创建/启动失败、
端口不可监听、健康或 readiness 失败、配置或环境 diff、安全参数、LAN 正向访问、
容器重启或旧服务连续性检查失败。

1. 从受保护的 cidfile 读取新容器 ID；只有当名称精确为
   `face-algorithm-task-2026-001`、实际 image ID 符合预期且容器 ID 等于 cidfile
   记录时，才停止并删除该新容器。若 `docker create` 报错且存在无法由 cidfile
   可靠归属的同名残留，不自动删除，先保存 inspect 并人工核对创建时间和 image ID。
2. 确认原名称已释放，将
   `face-algorithm-task-2026-001-loopback-rollback` 恢复为原名称并启动。
3. 核对恢复容器的原 image ID、完整配置和 `127.0.0.1:18002` 绑定，验证
   `/health/live`、`/health/ready` 及原 `face-algorithm:18001` 服务。
4. 若失败项是旧服务连续性异常，候选容器回滚后仍保留全部审计资料，停止清理并
   升级调查；不得将候选回滚误报为已修复旧服务异常。

在 LAN 正向测试、inspect 的次网卡无发布检查、规范化配置比较、线程变量、
安全参数、容器重启测试和旧服务连续性检查全部通过前，不删除回滚容器。仅当
测试端具备到 `<lan-host>` 的有效路由时，额外的次网卡负向 TCP 测试才是删除
回滚容器的强制门槛。

## 验收标准

- 以 `docker inspect` 为权威配置证据：`HostIp=<server-37>`、
  `HostPort=18002`、容器端口 `8000/tcp`；不存在第二条 HostIp 映射。
- `ss -lnt` 仅作为辅助证据，因为 Docker 是否显示 userland-proxy 监听取决于
  宿主机配置；不得用它替代 `docker inspect` 判定。
- 从服务器请求 `http://<server-37>:18002/health/live` 和 `/health/ready`；
  前者返回 HTTP 200 与 `{"status":"live"}`，后者返回 HTTP 200、
  `status=healthy`、8 个模型 ready、4 项能力 ready。
- 从源地址为 `<lan-host>/24` 的 Windows 客户端请求同样两个精确路径，
  均必须 HTTP 200；不能用根路径的 404 作为成功证据。
- `docker inspect` 证明没有发布到 `<lan-host>`、`0.0.0.0` 或
  `127.0.0.1`。在当前 Windows 客户端具备到 `<lan-host>` 路由时，额外执行
  负向 TCP 测试；若无路由，只将失败记为辅助证据，不替代 inspect。
- 规范化 inspect 除已声明的容器身份/时间和 HostIp 外无差异。
- 在容器配置和容器内分别逐项验证 9 个线程变量，并比较完整环境变量集合，
  确认没有丢失镜像内置环境变量。
- 容器仍为只读根文件系统、丢弃全部 capabilities、启用
  `no-new-privileges`，并保留 CPU、内存、swap、PID、tmpfs、挂载和日志限制。
- 重启容器后局域网地址仍可访问。
- 原 `face-algorithm:18001` 容器的 ID、StartedAt、RestartCount 和
  `0.0.0.0:18001 -> 8000/tcp` 映射与切换前一致，且 `/docs` 前后均 HTTP 200。
- 验收记录明确注明 `<server-37>` 当前为 DHCP/auto 地址，未把容器重启测试
  误写为主机重启后地址稳定性证明。
