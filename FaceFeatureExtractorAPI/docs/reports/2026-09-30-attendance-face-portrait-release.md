# TASK-2026-001 — 白底人脸照 2.3.0 发布记录

日期：2026-09-30。当前状态：实现及本地完整回归完成，待固定提交构建和候选真实验收，尚未切换线上服务。

## 兼容性

保留 `POST /api/v2/portrait/generate`、multipart `image`、全部响应字段及 480×640 JPEG。
新增 `PORTRAIT_SOURCE_LIMITED` / `PORTRAIT_UPSCALE_LIMITED` 提醒枚举；成功有图可为 REVIEW，
业务端须用 `success=true && image_base64` 判断出图，不应仅接受 PASS。
默认 `face_archive` 不强制完整头顶、颈部或肩部，仅使用真实输入像素；有效脸部几何和抠图检查保留。
旧 YAML 缺 mode 仍为严格 `head_shoulders`，本次部署显式迁移 mode/version。

## 已核对的升级前基线

当前线上 2.2.0，发布代码 `d78e723b8fb8737b09e12859e57aa9856a640db6`；18002/18003 healthy。
运行配置 SHA256 `c0d27189eb153cedb0b4d81890b18e2b99a88f5cedec0ba2a63785a882be0287`。
presence 为 configured、threshold=0.5、threshold_id=TASK-2026-001-presence-trial-050-20260928；MODNet enabled=true。
本轮只迁移 portrait.mode/version，保护所有其他配置、旧容器和 18001。

本地修改前基线：1266 passed / 1 skipped；无既有测试失败。
真实旧版已复现：参考照片和无肩特写 SHOULDERS_NOT_VISIBLE、局部头顶 SUBJECT_CROPPED、小脸 FACE_TOO_SMALL。
真实媒体、派生图片和逐例报告仅留工作区忽略目录，不进入 Git。

## 本地验收

- 完整 `pytest -q tests tools/visual-console/tests`：1590 passed / 1 skipped，120.15 秒；仅 4 个既有依赖弃用提示。
- Node 测试：33 passed。API 新测试固定原 image 请求和全部响应字段，成功 REVIEW JPEG 可解码。
- 唯一跳过为 Windows 下 Linux TIME_WAIT 测试；已在 37 临时回环端口独立验证 TIME_WAIT 可复用、活动监听仍受保护。
- 算法与 API/测试台已交叉需求及代码质量审查；发布脚本补齐旧 2.2 模型就绪检查后才记录回滚成功。
- 旧版 seed=20260930 随机 20 张中出图 12 张；其余 NO_FACE 3、SUBJECT_CROPPED 1、PORTRAIT_GEOMETRY_INVALID 4。
  另 6 个针对输入：5 个可用人像因肩部/头顶/模板小脸限制失败，1 个空白 NO_FACE。候选将用完全相同输入复测。
