// InsightFace pipeline (SCRFD detector with 5 keypoints, ArcFace template alignment, ArcFace
// recognizer), mirroring tools/engine-eval/insightface_ref.py. The pretrained weights are licensed
// for non-commercial research only: evaluation builds load them from a local path, never the repo.
#ifndef FACE_ENGINE_INSIGHTFACE_MODELS_H
#define FACE_ENGINE_INSIGHTFACE_MODELS_H

#include <opencv2/core/mat.hpp>
#include <memory>
#include <vector>
#include "ort_model.h"
#include "v1_models.h"

namespace face {

struct ScrfdFace {
    DetectedBox box;
    Landmarks5 kps;
};

/** SCRFD with keypoints (det_500m / det_10g): strides 8/16/32, 2 anchors each. */
class Scrfd {
public:
    explicit Scrfd(std::unique_ptr<NetSession> model);
    /** Faces sorted by score, in {@code bgr} coordinates; square {@code input_size}, multiple of 32. */
    std::vector<ScrfdFace> Detect(const cv::Mat &bgr, int input_size, float threshold = 0.5f, float nms = 0.4f);
    /**
     * Video frames: the long side scaled to {@code long_side}, the short side padded only up to the
     * next multiple of 32 (SCRFD is fully convolutional), so a portrait frame costs ~40% less.
     */
    std::vector<ScrfdFace> DetectLongSide(const cv::Mat &bgr, int long_side, float threshold = 0.5f, float nms = 0.4f);

private:
    std::vector<ScrfdFace> Run(const cv::Mat &bgr, int nw, int nh, float scale, int in_w, int in_h, float threshold, float nms);

    std::unique_ptr<NetSession> model_;
    std::vector<float> input_;
};

/** utils/face_align.py norm_crop: Umeyama similarity to the 112x112 ArcFace template. */
cv::Mat NormCrop112(const cv::Mat &bgr, const Landmarks5 &kps);

/** ArcFace ONNX: RGB, (x - 127.5) / 127.5, L2-normalised output (512-d for w600k models). */
class ArcFace {
public:
    explicit ArcFace(std::unique_ptr<NetSession> model);
    std::vector<float> Extract(const cv::Mat &aligned112);

private:
    std::unique_ptr<NetSession> model_;
    std::vector<float> input_;
};

}  // namespace face

#endif  // FACE_ENGINE_INSIGHTFACE_MODELS_H
