# 设备同步 v2 接口契约

所有路由前缀 `/api/sync/v2`，仅使用设备凭据，不能用管理会话或旧匿名设备身份访问。成功包络 `{code:200,msg:"success",data:...}`。`User` 包含 `id,userNo,name,groupId,featureData,photoUrl,status,createTime,updateTime,syncVersion`；照片地址为 `/uploads/{photoUrl}`，下载也携带设备凭据。版本、ID 均为非负 Java long；Android 使用 long。

| 请求 | data |
|---|---|
| `GET /state` | `{epoch,head,retentionFloor,scopeVersion}` |
| `POST /bootstrap`，无 body | `{leaseId,epoch,scopeVersion,startRevision,maxId,expiresAt}`，expiresAt 为 UTC ISO 时间 |
| `GET /bootstrap/{leaseId}/page?afterId=0&limit=200` | `{records:[User],nextAfterId,hasMore}` |
| `POST /bootstrap/{leaseId}/finish`，无 body | `{epoch,scopeVersion,upperRevision}` |
| `GET /changes?epoch=...&scopeVersion=...&afterRevision=...&untilRevision=...&limit=200&leaseId=...` | `{events:[{revision,userId,operation,user}],nextRevision,hasMore,upperRevision}`；leaseId 可省略 |
| `POST /ack`，JSON `{epoch,scopeVersion,revision,leaseId?}` | `{ackRevision}` |

初始化：建立租约获得 L=startRevision、M=maxId；以 afterId=0 开始，按 nextAfterId 分页写入 staging；hasMore=false 后调用 finish 固定 U=upperRevision；携带 leaseId 分页读取 `(L,U]` 变更；所有页面成功后原子切换数据集并持久化 epoch/scopeVersion/U，再 ACK U 并释放租约。重复 finish 返回同一 U。每个设备最多一个租约，新建会使旧租约失效；租约默认 60 分钟，过期后重建。页面只允许 afterId 不超过服务器已扫描水位，支持响应丢失后重试，不能向前跳页。

增量：GET state 固定本轮 untilRevision=head，从本地游标 afterRevision 开始分页；必须持久化响应 nextRevision，不能以最后一个可见事件的 revision 代替。服务器按全局变更扫描，未授权的事件会被过滤，因此 events 为空但 nextRevision 仍可能前进。hasMore=false 时 nextRevision 等于本轮 U。记录 upsert 使用完整 user，delete 的 user 为 null；移动出授权分组会产生 delete 效果，不能只按新分组过滤。事件 revision 是记录版本，客户端拒绝旧版本覆盖新版本；删除也保留版本防止复活。初始化扫描拿到的较新 syncVersion 同样参与此比较。

分页 limit 默认 200、范围 1–200；非法参数 HTTP400。epoch 改变、scopeVersion 改变、历史低于 retentionFloor、租约不存在/过期/失效，均 HTTP409，`errorCode="SNAPSHOT_REQUIRED"`，客户端重新构建 staging，不能清空仍在线的数据。尚未扫描完成调用 finish，或扫描完成后向前跳页，返回 HTTP409/400 明确错误。数据解析异常 HTTP503，不能返回成功空名单。所有失败均不得推进本地确认游标。

ACK 在同一 epoch/授权版本内单调前进；普通增量 ACK 不需要租约。租约 ACK 必须在 finish 后并且 revision=U。ACK 丢失可重试；已释放租约的相同 ACK 幂等成功。服务器只记录确认点，本接口不自动删除历史；保留清理必须遵守活动租约起点及 epoch/floor 的同事务更新约束。

通知仅提示需要拉取。SDK 必须在启动、重连、周期检查及同步期间 pending 被置位后再次追赶，零人结果也要刷新识别内存。v1 完整数组契约保持独立，不能使用本接口的分页结果执行全库 NOT IN 删除。
