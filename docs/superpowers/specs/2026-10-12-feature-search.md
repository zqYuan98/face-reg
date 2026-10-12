# 纯特征 1:N 接口设计

用户确认新增与 1:1 配套的纯特征接口，最多约 1000 个候选，需要连续调用。现有 Java 人员库搜索保持不变。

`POST /api/search_feature` 接收 UTF-8 JSON：必填 `probe_feature`、`probe_version`、`reference_version`、`candidates`，可选 `top_k` 默认 1。候选为 `{id, feature}`，ID 为 1–128 字符的非空白字符串且本次请求内唯一；特征固定 1024 维，版本均为 `recognizer_v1_1024`。候选数量 1–1000，top_k 为整数 1–N。严格拒绝缺失/多余字段、重复 JSON 键、布尔/字符串数值、非有限值、float32 溢出和零范数；任何无效候选使整批失败。

沿用 float32 输入转换、float64 L2 归一化及点积的 `l2_dot_v1` 算法。查询只归一化一次，候选分块批量计算。排序按分数降序，同分按输入顺序；不设置身份通过阈值、不保存特征库，不返回输入向量。成功对象包含 success、matches（id、similarity_score）、candidate_count、top_k、feature_dimension、feature_version、matcher_version、processing_time_ms。负分也属于合法结果；全量完成后才返回，不提供部分排名。

初始上限为 JSON 32 MiB、从接收开始的 5 秒绝对期限、每进程一个独立 1:N 工作槽，无等待队列。准入在读取大请求体前，JSON 解析/校验和计算放到一个轻量子进程。请求超时/断开后，只有实际后台任务完成才释放槽。忙返回 429 FEATURE_SEARCH_BUSY；其他错误采用 1:1 的结构：400 INVALID_FEATURE、413 FEATURE_PAYLOAD_TOO_LARGE、500 INFERENCE_ERROR、503 FEATURE_SEARCH_UNAVAILABLE、504 ALGORITHM_TIMEOUT。该工作槽不占图像推理槽，也不增加图片模型实例。以目标环境压测确认这些限制，而非承诺吞吐量。

服务版本更新为 1.3.0。独立临时容器验证后沿用已有上线流程同步远端；仅变更算法容器，保留原模型和图像处理。Word 在用户桌面 v1.3 基础上输出 v1.4，保留已有 3 张截图，补充参数、响应、错误、容量与调用示例。验证包含与 1:1 的数值对照、稳定排名、全部错误边界、慢流/超时/断开/并发槽、1000 候选串行及混合图像负载、峰值内存和模型回归。
