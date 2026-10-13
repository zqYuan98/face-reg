# InsightFace 算法解析

> **资料来源**（2026-10-12 查阅）：
> - [deepinsight/insightface](https://github.com/deepinsight/insightface) 仓库中的以下内容：
>   - `python-package`：README、`docs/model_zoo.md`、`docs/liveness.md`、`model_zoo/scrfd.py`、`model_zoo/arcface_onnx.py`、`model_zoo/landmark.py`、`utils/face_align.py`；
>   - `recognition/arcface_torch`：README、`losses.py`、`partial_fc_v2.py`、`backbones/iresnet.py`；
>   - `detection/scrfd`：README。
>
> **标注约定**：
> - 【代码】：上述源码中能直接看到的实现；
> - 【官方】：README 或文档给出的数据和说明；
> - 【论文】：笔者对相应论文的理解，本次没有逐句核对原文；
> - 【推断】：笔者自己的推断。
>
> 姊妹篇：[虹软离线人脸方案整理](arcsoft-offline-face-pipeline.md)

---

## 0. 先看许可：能不能商用

| 内容 | 许可 |
|---|---|
| 库代码（Python 包、训练代码） | MIT，学术和商业都可以使用【官方】 |
| 预训练模型（无论自动下载还是手动下载） | **"available for non-commercial research purposes only"**【官方】 |
| Cheetah 模型包 | 公开发布的版本附带非商业授权，需要保留随包的 `MODEL.LICENSE`【官方】 |
| 活体插件 `liveness.onnx` | 和基础模型分开发布；文档里没写许可，要去插件发布仓库查看【官方】 |

**结论**：学习和研究可以直接用。商业产品要么自己用训练代码重新训练（同时要注意训练数据集本身的许可），要么联系 InsightFace 获取商业授权（仓库里有 `docs/commercial_evaluation.md`）。

---

## 1. 系统组成

```
图像（BGR）
  │
  ▼
SCRFD 检测 ──► 人脸框 + 置信度 + 5 个关键点（双眼、鼻尖、两个嘴角）
  │
  ├──►（可选）活体插件：按自己的模板对齐到 80×80 ──► live_score
  ├──►（可选）2d106 / 3d68 关键点 ──► 106 点、68 点、姿态角(pitch, yaw, roll)
  ├──►（可选）性别年龄模型
  │
  ▼
5 点相似变换对齐 ──► 112×112 人脸图
  │
  ▼
识别网络（IResNet / MobileFaceNet / ViT）──► 512 维特征
  │
  ▼
L2 归一化 ──► 余弦相似度（1:1 比对，或和底库做矩阵乘法完成 1:N）
```

Python 包的入口是 `FaceAnalysis`，默认使用 `buffalo_l` 模型包【官方】：

```python
from insightface.app import FaceAnalysis
app = FaceAnalysis()                     # 默认 buffalo_l
app.prepare(ctx_id=0)                    # 默认会合并 128×128 和 640×640 两种尺寸的检测结果
faces = app.get(img_bgr)                 # 每个 face 带 bbox / kps / det_score / embedding 等字段
```

---

## 2. 检测：SCRFD

### 2.1 思路【论文】

SCRFD 出自论文 *Sample and Computation Redistribution for Efficient Face Detection*（arXiv 2105.04714，ICLR 2022），是一个单阶段、anchor-based 的人脸检测器。论文有两个核心观点：

- **样本重分配（Sample Redistribution）**：WIDER FACE 里大量是小脸，而小脸主要由步长为 8 的浅层特征图负责检测。训练时扩大随机裁剪的尺度范围，可以让浅层特征图拿到更多小脸样本。
- **计算重分配（Computation Redistribution）**：在固定的 FLOPs 预算下，用随机搜索决定算力在 backbone、neck、head 以及各个 stage 之间怎么分配，而不是照搬分类网络的结构。仓库里对应的是"先只搜 backbone，再以最优配置为模板搜整个网络"的两步流程，选模型的标准是 Hard 子集的 AP【官方】。

### 2.2 推理实现【代码】（`scrfd.py`）

- 输出 9 个张量时，表示 3 个步长 `[8,16,32]`、每个位置 2 个 anchor、带关键点（`use_kps=True`）。
- 框的回归方式：网络输出的是从 anchor 中心到上、下、左、右四条边的距离，乘以步长后还原成 `x1=cx-l, y1=cy-t, x2=cx+r, y2=cy+b`。关键点的解码方式相同，也是相对 anchor 中心的偏移乘以步长。
- 预处理：`(x-127.5)/128.0`，并把 BGR 转成 RGB。图片按原比例缩放后**贴在画布左上角**，其余部分补黑（不是居中补边）。
- 默认阈值：`det_thresh=0.5`，`nms_thresh=0.4`。

### 2.3 精度与速度【官方】（WIDER FACE，VGA 分辨率）

| 模型 | Easy | Medium | Hard | FLOPs | 参数量 (M) | 推理 (ms) |
|---|---|---|---|---|---|---|
| SCRFD_500M_KPS | 90.97 | 88.44 | 69.49 | 500M | 0.57 | 3.6 |
| SCRFD_2.5G_KPS | 93.80 | 92.02 | 77.13 | 2.5G | 0.82 | 4.3 |
| SCRFD_10G_KPS | 95.40 | 94.01 | 82.80 | 10G | 4.23 | 5.0 |
| SCRFD_34G | 96.06 | 94.92 | 85.29 | 34G | 9.80 | 11.7 |

另外两组官方数据：
- SCRFD-34GF 对比 TinaFace（ResNet50）：Hard AP 85.29 对 81.43，计算量 34.13 对 172.95 GFLOPs，耗时 11.7 对 38.9 ms。
- SCRFD-0.5GF 在 Ryzen 9 3950X 单线程上，640×480 输入耗时 28.3ms，320×240 输入耗时 11.4ms。

---

## 3. 对齐：5 点相似变换【代码】（`face_align.py`）

```python
arcface_dst = [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
               [41.5493, 92.3655], [70.7299, 92.2041]]   # 112×112 模板上的左眼、右眼、鼻尖、左嘴角、右嘴角
tform = SimilarityTransform(); tform.estimate(kps, dst)  # 4 个自由度：缩放、旋转、两个方向的平移
aligned = cv2.warpAffine(img, tform.params[:2], (112, 112))
```

- 用相似变换，而不是仿射或透视变换：只消除平面内的旋转、尺度和位置差异，不扭曲脸型，所以身份信息不会被破坏。
- 对齐之后，眼睛、鼻子、嘴总落在固定的像素位置上。这正是识别网络头部敢用全连接层而不用全局池化的前提（见 4.1）。

---

## 4. 识别：ArcFace

### 4.1 骨干网络 IResNet【代码】（`iresnet.py`）

**残差块**（BN 放在卷积前面的"预激活"风格）：

```
x ─► BN ─► Conv3×3(s=1) ─► BN ─► PReLU ─► Conv3×3(stride) ─► BN ─► (+) ─► 输出
│                                                                   ▲
└──────────────── 恒等映射（或 1×1 卷积 + BN 下采样）────────────────┘
```

- 残差相加之后没有激活函数；激活函数用 PReLU。
- **Stem**：一个 3×3、步长 1 的卷积，后面**没有 max-pool**。输入只有 112×112，如果像分类网络那样一开始就 4 倍下采样，细节会丢得太早。
- **4 个 stage**：通道数分别是 64 / 128 / 256 / 512，每个 stage 下采样 2 倍，112 最终变成 7×7。
- **输出头**：`BN → Dropout → FC(512·7·7 → 512) → BN1d`，最后一层 BN1d 的缩放参数固定为 1、不参与训练。
  - 【推断】这里用全连接层而不是全局平均池化，是因为人脸已经对齐，每个空间位置对应固定的五官区域，保留空间信息对区分身份有用。这也是 ArcFace 论文里讨论过的输出结构之一。

每个 stage 的残差块数量：

| 模型 | 每个 stage 的块数 |
|---|---|
| r18 | [2, 2, 2, 2] |
| r50 | [3, 4, 14, 3] |
| r100 | [3, 13, 30, 3] |
| r200 | [6, 26, 60, 6] |

除 IResNet 外，还支持 MobileFaceNet（`mbf`，0.45 GFLOPs）和 ViT-T / S / B / L【官方】。

### 4.2 损失函数：加性角度间隔

**演进路线**：
- Softmax；
- NormFace：把特征和类中心都做 L2 归一化，logit 变成余弦值，再乘一个尺度 s；
- 在目标类的 logit 上加 margin，有三种方式：
  - SphereFace：`cos(m1·θ)`，乘性角度间隔；
  - CosFace：`cos θ − m3`，加性余弦间隔；
  - **ArcFace：`cos(θ + m2)`，加性角度间隔**。

ArcFace 的损失：

$$
L=-\frac1N\sum_{i}\log\frac{e^{s\cos(\theta_{y_i}+m)}}{e^{s\cos(\theta_{y_i}+m)}+\sum_{j\neq y_i}e^{s\cos\theta_j}},\qquad
\cos\theta_j=\frac{W_j^\top x_i}{\|W_j\|\|x_i\|}
$$

- 代码中的默认值是 `s=64`，`m=0.5`（约 28.6°）【代码】。
- **几何意义**：特征都分布在一个超球面上。θ 是样本特征和它所属类中心之间的夹角，而在单位球面上，夹角就是测地距离。要求目标类在角度上额外领先 m，相当于在球面上为每一类的边界都留出一圈固定宽度的间隔，所以类内会更紧凑、类间分得更开。
- **统一写法**：`cos(m1·θ + m2) − m3`。`CombinedMarginLoss` 只实现了两个分支：`m1=1, m3=0` 时是 ArcFace，`m3>0` 时是 CosFace；其他组合会直接 `raise`【代码】。

**一个容易忽略的实现细节**【代码 + 推断】：当前的 `ArcFace.forward` 在 `torch.no_grad()` 下原地执行 `arccos → +m → cos`，然后再乘以 s。

- 这些原地修改没有记录进计算图，所以反向传播时，这一步相当于直通（straight-through）：margin 只影响前向算出来的 softmax 概率，梯度的形式仍然是 `s·(p − onehot)`，不经过 `cos(θ+m)` 的导数。
- 早期实现里为了处理 `θ+m>π` 时函数不单调而引入的 `th` / `mm` 补偿分支，现在只剩下 `__init__` 里算好但没用到的属性。

`interclass_filtering_threshold > 0` 时，非目标类中余弦值高于阈值的 logit 会被置零。【推断】这是为了应对噪声标签：同一个人被标成两个 ID 时，这类"疑似同一人"的负类不应该被强行推开。

### 4.3 Partial FC：百万级身份的训练方法【代码】（`partial_fc_v2.py`）

**问题**：最后一层全连接是 `类别数 × 512` 的矩阵。2900 万个身份时，单这一层就放不进显存。

**做法**：
1. **模型并行**：类中心按连续区间切分到各张 GPU 上；所有 GPU 的特征通过 AllGather 汇总（带梯度）。
2. **采样**：每个 batch 中，本 GPU 负责的正类（batch 里出现过的类）全部保留，再从其余类中均匀随机抽取负类，凑满 `sample_rate × num_local` 个。
   - 实现方式：给每个类一个随机分数，正类的分数设为 2.0，再取 topk，这样正类一定会被选中。
   - 如果正类数量已经超过名额，就只保留正类。
3. **计算 logit**：特征和被选中的类中心都做 L2 归一化，做线性层后得到余弦值，`clamp(-1, 1)`，再交给 margin 函数。
4. **分布式 softmax**：先跨 GPU 做 all_reduce 求最大值（数值稳定），再 all_reduce 求指数和，从而在全部被采样的类上算出完整的交叉熵。

**收益**【官方】（8×V100）：

| 身份数 | 数据并行 | 模型并行 | PFC 0.1 |
|---|---|---|---|
| 140 万 | 1672 样本/s，显存 32.2G | 3043 样本/s，11.2G | 4738 样本/s，6.1G |
| 2900 万 | 显存不足 | 显存不足 | 1855 样本/s，32.3G |

**副作用是更抗噪声**【官方】：r50 在 40% 标签翻转的 WF12M 数据上，IJB-C 从 88.35 提升到 96.11（PFC-0.1）。【推断】每次只抽一部分负类，错标的"负类"很少被抽中，对训练的干扰也就小了。

### 4.4 Sub-center ArcFace【论文】

ECCV 2020 的扩展工作：每个类有 K 个子中心，计算时取样本和这些子中心的最大余弦值。干净的样本会聚到主导子中心上，噪声样本则落到其他子中心，之后可以据此清洗数据。适合在噪声较多的网络爬取数据上训练。

### 4.5 数据比模型更重要【官方】

训练数据集：

| 数据集 | 身份数 | 图片数 |
|---|---|---|
| MS1MV2 | 8.7 万 | 580 万 |
| MS1MV3 | 9.3 万 | 520 万 |
| Glint360K | 36 万 | 1710 万 |
| WebFace42M | 200 万 | 4250 万 |

IJB-C TAR@FAR=1e-4 的结果：

| 骨干 \ 数据 | MS1MV2 | MS1MV3 | Glint360K | WF42M（PFC） |
|---|---|---|---|---|
| mbf | 93.61 | 94.23 | 95.04 | — |
| r50 | 95.97 | 96.37 | 97.16 | 97.48（PFC-0.3） |
| r100 | 96.37 | 96.85 | 97.55 | 97.70（PFC-0.2） |

- 用 Glint360K 训练的 r50（97.16）超过了用 MS1MV2 训练的 r100（96.37）。**数据规模带来的提升大于加深网络。**
- 在 WF42M 上，r18 → r50 → r100 → r200 的 IJB-C 分别是 95.77 / 97.48 / 97.82 / 97.97，增益明显递减。
- ViT-B（11.4 GFLOPs）达到 97.90，和计算量相近的 r100（12.1 GFLOPs）差不多。

---

## 5. 推理细节【代码】（`arcface_onnx.py`）

- 输入：112×112、RGB，预处理为 `(x − 127.5) / 127.5`；旧的 MXNet 转换模型自带归一化层，这时 mean=0、std=1。
- `face.embedding` 是原始 512 维向量，`face.normed_embedding` 是 L2 归一化后的版本。
- `compute_sim` 计算余弦相似度：`dot(f1, f2) / (‖f1‖·‖f2‖)`。
- **阈值没有通用值**：要用自己场景的正负样本对画 ROC 曲线，在目标误识率（比如 FAR=1e-4 或 1e-5）下选阈值。虹软的 0.8 是映射后的分数，不能拿来套用。

---

## 6. 模型包【官方】

| 模型包 | 检测 | 识别（骨干@训练数据） | 关键点 | 性别年龄 | 大小 |
|---|---|---|---|---|---|
| buffalo_l（默认） | SCRFD-10GF | ResNet50@WebFace600K | 2d106 + 3d68 | ✓ | 326MB |
| buffalo_m | SCRFD-2.5GF | ResNet50@WebFace600K | 2d106 + 3d68 | ✓ | 313MB |
| buffalo_s | SCRFD-500MF | MBF@WebFace600K | 2d106 + 3d68 | ✓ | 159MB |
| buffalo_sc | SCRFD-500MF | MBF@WebFace600K | — | — | 16MB |
| antelopev2 | SCRFD-10GF | ResNet100@Glint360K | 2d106 + 3d68 | ✓ | 407MB |

识别精度：

| 模型包 | MR-ALL | 非洲裔 | 白人 | 南亚裔 | **东亚裔** | LFW | CFP-FP | AgeDB-30 | IJB-C(E4) |
|---|---|---|---|---|---|---|---|---|---|
| buffalo_l | 91.25 | 90.29 | 94.70 | 93.16 | **74.96** | 99.83 | 99.33 | 98.23 | 97.25 |
| buffalo_s | 71.87 | 69.45 | 80.45 | 73.39 | **51.03** | 99.70 | 98.00 | 96.58 | 95.02 |

- **在国内落地要特别注意**：在官方的多种族测试中，东亚子集的成绩明显低于其他人种。上线前必须用自己场景的数据重新评估。
- LFW 已经饱和（99.7% 以上），区分不出模型好坏，要看 IJB-C 和 MR-ALL。
- 新的 InsightFace 2.0 模型包：Raccoon（`raccoon_s/l`，用 V2 清单文件声明模型和预处理）、Cheetah（人体检测 + 人体重识别 + 人脸，面向 `PersonAnalysis`）。官方没有公开它们的骨干网络和精度。

**活体插件**【官方】（`docs/liveness.md`）：
- 单帧 RGB 活体：用 5 点关键点对齐到 80×80，输入归一化到 0–1。
- 对齐后的人脸如果缺失超过 30% 的区域，就拒绝判断（返回 `input_rejected`），而不是判为假体。
- 默认阈值 0.8，分数等于阈值也算通过。
- `normal` 模式下只有活体才提特征；`observe` 模式下全部人脸都提特征。
- 官方明确说明这个阈值"不是生产环境的精度保证"。

---

## 7. 和虹软方案逐项对照

| 环节 | 虹软 ArcFace SDK | InsightFace | 用 InsightFace 时要自己补的部分 |
|---|---|---|---|
| 检测 | 自研模型，有 IMAGE 和 VIDEO 两种模式 | SCRFD，只做单帧检测 | — |
| 跟踪 | VIDEO 模式自带 faceId | **没有** | 需要自己加跟踪器：IoU 匹配、SORT 或 ByteTrack |
| 旋转画面 | 可设置检测角度优先级，或全角度检测 | 默认只适配正立人脸 | 根据设备方向先旋转图像 |
| 质量门控 | Demo 在应用层实现（大小、稳定性、区域） | 能提供检测置信度、3d68 姿态角 | 同样在应用层实现；可以加上模糊度检测（例如拉普拉斯方差） |
| 对齐 | 不公开 | 5 点相似变换到 112×112 | — |
| 特征 | 不透明的 `byte[]`，带版本号 | 512 维 float | 自己在底库里记录模型版本 |
| 比对 | 0–1 的映射分数，阈值 0.8 | 余弦相似度 | 在自己的数据上标定阈值 |
| 1:N | 引擎内底库，支持增删改查和 topN | **没有** | 万人以内用 numpy 矩阵乘法即可；更大规模用 FAISS |
| RGB 静默活体 | ✓ | 活体插件（单帧） | — |
| 交互动作活体 | ✓ | **没有** | 用 2d106 关键点算眨眼（眼睛纵横比）、张嘴（嘴部纵横比），用 3d68 姿态角判断转头 |
| 炫光活体 | ✓ | **没有** | 需要自己设计屏幕光序列和判定模型 |
| 属性 | 年龄、性别、3D 角度、额头区域 | 年龄、性别、106 点和 68 点关键点、姿态角 | — |
| 端侧部署 | 原生 so，arm32 和 arm64，约 53MB | ONNX 格式 | 转成 ncnn、MNN、ONNX Runtime Mobile 或 TFLite；量化后要重新评估精度 |
| 商用 | 按设备付费授权 | 代码 MIT，**模型仅限非商业研究** | 自己训练模型，或购买商业授权 |

---

## 8. 用 InsightFace 复刻虹软的架构（示意代码）

下面的代码展示了怎样把虹软 Demo 的几个做法搬过来：跟踪、质量门控、每人只识别一次、有限次重试。**这段代码只是示意，没有实际运行验证。**

```python
from collections import deque
import numpy as np
from insightface.app import FaceAnalysis
from insightface.app.common import Face

app = FaceAnalysis(allowed_modules=["detection", "recognition", "landmark_3d_68"])
app.prepare(ctx_id=0, det_size=(640, 640))
det, rec, lmk3d = app.det_model, app.models["recognition"], app.models["landmark_3d_68"]

SIM_TH, MIN_FACE, MAX_ANGLE, MOVE_PX, MAX_TRIES = 0.45, 80, 30, 10, 3  # SIM_TH 需要在自己的数据上标定
gallery_names: list[str] = []
gallery_feats = np.zeros((0, 512), np.float32)          # 每一行都已 L2 归一化

def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    return inter / ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter + 1e-6)

class Track:
    _next = 0
    def __init__(self, box):
        self.id, Track._next = Track._next, Track._next + 1
        self.box, self.centers = box, deque(maxlen=5)
        self.status, self.tries, self.name = "TO_RETRY", 0, None

tracks: list[Track] = []

def on_frame(img):
    global tracks
    bboxes, kpss = det.detect(img, max_num=0)                 # 每帧只做检测，开销小
    alive = []
    for bbox, kps in zip(bboxes, kpss):
        box = bbox[:4]
        t = max(tracks, key=lambda t: iou(t.box, box), default=None)
        if t is None or iou(t.box, box) < 0.3 or t in alive:
            t = Track(box)                                    # 没匹配上，当作新出现的人
        t.box = box
        t.centers.append(((box[0]+box[2])/2, (box[1]+box[3])/2))
        alive.append(t)
        if t.status in ("SUCCEED", "FAILED"):
            continue                                          # 每个人只识别一次（或已放弃）
        # —— 廉价的质量门控 ——
        if min(box[2]-box[0], box[3]-box[1]) < MIN_FACE or len(t.centers) < 5:
            continue
        c = np.array(t.centers)
        if np.linalg.norm(np.diff(c, axis=0), axis=1).max() > MOVE_PX:
            continue
        face = Face(bbox=box, kps=kps, det_score=bbox[4])
        lmk3d.get(img, face)                                  # 得到 face.pose = [pitch, yaw, roll]
        if np.abs(face.pose[:2]).max() > MAX_ANGLE:
            continue
        # —— 昂贵的部分：活体（略）→ 提特征 → 1:N 搜索 ——
        rec.get(img, face)
        if len(gallery_feats):
            sims = gallery_feats @ face.normed_embedding
            i = int(sims.argmax())
            if sims[i] >= SIM_TH:
                t.status, t.name = "SUCCEED", gallery_names[i]
                continue
        t.tries += 1
        if t.tries >= MAX_TRIES:
            t.status = "FAILED"                               # 也可以像虹软那样延迟一段时间后再重试
    tracks = alive                                            # 离开画面的人自动被清理掉
    return [(t.id, t.box, t.status, t.name) for t in tracks]
```

要真正上线，还需要补上这些：
- 把提特征和活体检测放到有界线程池里异步执行，队列满了就丢帧；
- 在搜索之前加入活体判断（活体插件，或者自己实现的交互和炫光活体）；
- 底库持久化，并在底库里记录模型版本。

---

## 9. 总结：两者各自的长处

- **InsightFace 的长处在算法和训练**：
  - ArcFace 的角度间隔给出了清晰的几何目标；
  - Partial FC 让千万级身份的训练成为可能，还顺带提高了抗噪能力；
  - SCRFD 用搜索的方法为人脸检测重新分配算力；
  - 公开的结果表明，数据规模的作用大于加深网络。
- **虹软的长处在工程化和产品化**：
  - 用跟踪把计算量降到每人一次；
  - 用多层质量门控挡住不合格的输入；
  - 用状态机、背压和重试保证实时性；
  - 活体分层，并且按场景组合；
  - 设备授权、特征版本号这些产品层面的设计。
- 两者结合起来，基本就是一套完整离线人脸系统的蓝图：**算法用 InsightFace 的思路训练或选型，系统架构参考虹软的调度方式。**
