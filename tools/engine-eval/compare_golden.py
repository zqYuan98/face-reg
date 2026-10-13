import sys, numpy as np
def parse(path):
    out, cur = {}, None
    for line in open(path):
        t = line.split()
        if not t: continue
        if t[0] == "IMG": cur = t[1]; out[cur] = {}
        else: out[cur][t[0]] = [float(x) for x in t[1:]] if t[0] not in ("NOFACE", "ERR") else True
    return out
a, b = parse(sys.argv[1]), parse(sys.argv[2])
box_d, lm_d, cos, align_eq, det_mismatch = [], [], [], 0, 0
for k in a:
    ra, rb = a[k], b.get(k, {})
    if ("FEAT" in ra) != ("FEAT" in rb): det_mismatch += 1; continue
    if "FEAT" not in ra: continue
    box_d.append(np.abs(np.array(ra["BOX"][:4]) - rb["BOX"][:4]).max())
    lm_d.append(np.abs(np.array(ra["LM"]) - rb["LM"]).max())
    align_eq += ra["ALIGNSUM"] == rb["ALIGNSUM"]
    cos.append(float(np.dot(ra["FEAT"], rb["FEAT"])))
cos = np.array(cos)
print(f"faces {len(cos)}  detection disagreements {det_mismatch}")
print(f"box max abs diff px: median {np.median(box_d):.2e} max {np.max(box_d):.2e}")
print(f"landmark max abs diff px: median {np.median(lm_d):.2e} max {np.max(lm_d):.2e}  identical {np.mean(np.array(lm_d)==0):.0%}")
print(f"aligned crop identical (checksum): {align_eq}/{len(cos)}")
print(f"feature cosine: min {cos.min():.6f} p1 {np.percentile(cos,1):.6f} median {np.median(cos):.6f}")
