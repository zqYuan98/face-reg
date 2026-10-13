# face-eval：对标报告生成

读取 [face-bench](../../android-sdk/face-bench/README.md) 导出的运行目录，按[设计文档 2.1 节](../../docs/superpowers/specs/2026-10-12-offline-face-engine-design.md)的"持平"定义给出活体、识别率、速度三项判定。只用 Python 标准库，Python 3.9 及以上即可运行。

```powershell
python -m faceeval.report <运行目录> --ours legacy_fixed --ref arcsoft
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--far` | `1e-5` 和 `1e-4` | 可重复；第一个是主判定的误识率 |
| `--window` / `--required` | 3 / 2 | 片段内最近几次可判断帧中几次判为活体即接受 |
| `--min-attacks` | 100 | 每类攻击少于这么多片段时只报"样本不足" |
| `--out` | `<运行目录>/report.md` | 同时写出同名 `.json` |

判定有三种：持平或更好、不如对照、样本不足（未能证明持平）。

- **活体**：先取虹软默认阈值 0.5 时的真人拒绝率（BPCER），再在我们的阈值中找真人拒绝率不高于它、攻击放过率最低的一个，按攻击类型逐类比较攻击放过率（APCER）。
- **识别率**：每家用自己的冒名分数定阈值，在相同误识率下比真人通过率（TAR）。冒名对数不足"误识率倒数的 3 倍"时只报样本不足。
- **速度**：比较各阶段耗时的 P50 和 P95，"单帧完整流程"是主比较项。

测试：

```powershell
python -m pytest tools/face-eval/tests
```
