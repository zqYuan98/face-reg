# 管理会话与设备身份迁移

管理端与设备端使用不同身份。管理接口需要服务器会话；设备 Cookie 不能调用管理 API，管理员 Cookie 也不能代替设备凭据。

## 管理端

设置 `FACE_ADMIN_USERNAME`（缺省 `admin`）及 `FACE_ADMIN_PASSWORD_HASH`（BCrypt 完整哈希）。不再存在演示密码。哈希未设置时登录返回 `503 ADMIN_NOT_CONFIGURED`；启用 `prod` 或 `production` profile 时直接拒绝启动。哈希工作因子支持 4–16，生产建议在部署硬件测量后选 10–12；不要将明文口令或真实哈希写进仓库。

客户端顺序：

1. `GET /api/csrf` 获取 `data.token` 和 `data.headerName`，保留同源 Cookie。
2. `POST /api/login` 发送 JSON `{username,password}` 和 `X-CSRF-TOKEN`。
3. 登录后再次获取 `/api/csrf`，因为登录会更换会话 ID 并使旧 CSRF 失效。
4. 管理修改请求都附带新 CSRF；`GET /api/me` 验证会话，`POST /api/logout` 带 CSRF 注销。

`FACE_SESSION` 为 HttpOnly、SameSite=Lax、30 分钟空闲过期，默认 Secure。正式环境使用 HTTPS；仅本地 HTTP 验证设置 `FACE_SESSION_COOKIE_SECURE=false`。身份不会存入 localStorage/sessionStorage，重启服务后原内存会话失效。不存在跨域凭据通配许可；页面与 API 由同源服务提供。

登录每个直接连接 IP 最多每五分钟 5 次失败窗口内尝试，密码校验最多并发 2 次；错误返回 401，限流返回 429。此内存保护按服务实例生效，重启会清空。

## 新设备

通过部署密钥管理工具生成至少 32 字符高熵随机秘密，字符限 `A-Z a-z 0-9 _ -`。设备 token 格式为 `deviceSn.secret`，服务器只保存完整 token 的 SHA-256（64 位十六进制），设备发送 `Authorization: Bearer <token>`。WebSocket 握手也需该请求头；不得把 token 放在 URL。

`FACE_DEVICE_CREDENTIALS_JSON` 是配置数组，例如结构：

```json
[{"deviceSn":"kiosk-01","tokenSha256":"<64位SHA-256>","groupIds":[7],"allGroups":false,"scopeVersion":1}]
```

SN 限字母、数字、下划线、连字符，最长 64 字符。组 ID 为正整数，最多 100 个；全库授权必须显式 `allGroups:true`、`groupIds:[]`。`scopeVersion` 为正数；**任何组授权变化必须增加该版本**并让 v2 设备完整重建。删除条目并重启撤销凭据，轮换秘密后旧 token 失效。当前配置不是动态设备凭据管理界面。

`/api/devices/register` 的 `deviceSn` 必须与认证身份一致，不能凭 token 注册其他设备。分组授权的 token 不得调用 v1 `/api/sync/users`，因为旧完整名单接口没有分组契约；改用支持权限范围的 v2。照片读取同样受范围约束。v2 控制器必须使用 `DevicePrincipal.current()` 取得服务端配置范围，不能采信请求中的 groupIds。

## 未升级设备的临时通道

默认关闭。只有所有输入均具备且没有过期才启用：

- `FACE_LEGACY_DEVICE_ENABLED=true`。
- `FACE_LEGACY_DEVICE_EXPIRES_AT`：绝对 UTC 时间，例如 ISO-8601 的 `...Z`，不能无限期开放。
- `FACE_LEGACY_DEVICE_NETWORKS`：经现场确认的设备网络 CIDR，逗号分隔，禁止 `/0`。
- `FACE_LEGACY_DEVICE_INVENTORY_JSON`：`[{"deviceSn":"old-01","networks":["192.0.2.10/32"]}]`；IPv6 使用 `/128`。库存必须逐台绑定独立、真实的网络源地址。

程序仅识别实际连接的 `getRemoteAddr()`，不直接采信 `X-Forwarded-For`。部署时保持 `server.forward-headers-strategy=none`，不能启用将任意客户端转发头改写为远端地址的代理处理。专用网关须隔离管理入口并保证可信源地址；多设备共用同一 NAT 地址导致身份歧义时本实现拒绝接入，不能通过把网段绑定成一台设备规避。

兼容通道仅开放 `/api/sync/users`、`/api/devices/register`、`/ws/sync` 及正式照片，不开放管理或 v2 路由。它是限定网络内的过渡访问控制，**不是旧设备已具有强身份认证**。旧 v1 仍是完整名单：不得返回分页片段或在异常时返回成功空数组。上线前核实设备列表、源 IP、网络防火墙、到期时间，并准备升级、关闭通道的操作人。

## 文件和上线验证

`/uploads` 只允许正式图片直接子文件名，支持历史中文、空格及字面 `+`；路径只做一次 URI 解码，不按表单规则把 `+` 变为空格。拒绝 `.staging`、`.cleanup`、隐藏文件、子目录、解码后残留百分号、分隔符、冒号、控制字符及符号链接，不允许借双重编码访问暂存文件。

分组授权设备可读取当前仍属于其分组的正式照片。同步期间人员换照、删除或移出分组后，设备固定版本 U 中的照片可能仍需下载，因此变更日志同时保留变更前/后的照片名与相应分组。历史照片只允许事件版本高于该设备有效初始化租约起点，或高于其当前已确认游标；租约/游标必须匹配当前 epoch 与设备 scopeVersion，租约未过期，游标更新时间在 `face.sync.retention-days` 内（默认 7 天，1–365）。一旦 ACK 跨过换照/移出事件，该历史照片授权即结束；无租约和有效 ACK 的新凭据不能读取历史照片。每次查验使用有索引的单张照片存在性查询、LIMIT 1 和 3 秒 JDBC 时限，不加载日志 postimage/特征。

显式全库 `allGroups=true` 凭据及受限网络内旧设备继续保留正式照片完整访问范围，以兼容没有 v2 租约/ACK 的 v1 同步模式。照片实际回收仍遵守文件宽限期及保留日志引用；历史访问控制不会放宽到其他分组或隐藏目录。

上线按顺序验证：公共登录页可达；匿名管理 401；缺失 CSRF 403；登录后会话正常；退出后 401；设备 token 不能读管理列表；错误 SN/组照片被拒绝；关闭、到期、未列入库存的旧设备拒绝。实际设备升级前先在专用预发网络验证握手、同步和图片；本次本地改动不自动开启旧通道或修改生产网络。

参考：[Spring Security 会话持久化](https://docs.spring.io/spring-security/reference/6.5/servlet/authentication/session-management.html)、[CSRF 登录/退出后的刷新规则](https://docs.spring.io/spring-security/reference/servlet/exploits/csrf.html)。
