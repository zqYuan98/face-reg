# 模型文件

服务运行时不联网下载模型。所有模型必须由运维离线放入本目录，并通过
`models.manifest.json` 中的文件大小、SHA-256、后端、Provider 和输出契约校验。

已跟踪的七个 ONNX 模型：

- `faceboxesv2-640x640.onnx`：人脸检测；
- `face_landmarker_pts5_net1.onnx`、`face_landmarker_pts5_net2.onnx`：5 点关键点；
- `face_recognizer.onnx`：输出固定为 1024 维，协议为 `recognizer_v1_1024`；
- `model_gray_mobilenetv2_rotcls.onnx`：方向校正；
- `fsanet-var.onnx`、`fsanet-conv.onnx`：人脸姿态。

白底照另需 MODNet checkpoint。按照以下命令离线准备并校验：

```powershell
python scripts/prepare_modnet.py --help
python scripts/verify_model_manifest.py --manifest models.manifest.json
```

`*.ckpt` 被 `.dockerignore` 排除，禁止把下载模型或客户数据打入代码镜像。生产部署应把
已经校验的 checkpoint 以只读卷挂载到 `/app/checkpoints`。缺少必需 ONNX 时 readiness
为 `unhealthy`；未启用或缺少可选 MODNet 时为 `degraded`，白底照接口返回
`MODEL_UNAVAILABLE`，不会伪降级为普通裁剪。
