#include "pipeline_v1.h"

namespace face {

PipelineV1::PipelineV1(std::unique_ptr<NetSession> landmark1, std::unique_ptr<NetSession> landmark2,
                       std::unique_ptr<NetSession> recognizer, std::unique_ptr<NetSession> detector)
    : landmarker_(std::move(landmark1), std::move(landmark2)), recognizer_(std::move(recognizer)),
      detector_(detector ? new FaceBoxes(std::move(detector)) : nullptr) {}

Landmarks5 PipelineV1::Points(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode) {
    if (mode != LandmarkMode::kTta)
        return landmarker_.Detect(bgr, box.x1, box.y1, box.x2, box.y2, mode == LandmarkMode::kSubpixel);
    // Same jitter set as tools' LFW evaluation: centre, +-3% horizontal, +-3% vertical with +-4% scale.
    static const float jitter[5][3] = {{0, 0, 0}, {0.03f, 0, 0}, {-0.03f, 0, 0}, {0, 0.03f, 0.04f}, {0, -0.03f, -0.04f}};
    const float w = box.x2 - box.x1, h = box.y2 - box.y1;
    const float cx = (box.x1 + box.x2) / 2, cy = (box.y1 + box.y2) / 2;
    Landmarks5 sum{};
    for (const auto &j : jitter) {
        const float jx = cx + j[0] * w, jy = cy + j[1] * h, hw = w * (1 + j[2]) / 2, hh = h * (1 + j[2]) / 2;
        Landmarks5 p = landmarker_.Detect(bgr, jx - hw, jy - hh, jx + hw, jy + hh, true);
        for (int k = 0; k < 5; ++k) sum[k] += p[k];
    }
    for (auto &p : sum) p *= 1.0 / 5;
    return sum;
}

ExtractResult PipelineV1::FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode) {
    ExtractResult result;
    result.box = box;
    if (box.x2 <= box.x1 || box.y2 <= box.y1) return result;
    result.landmarks = Points(bgr, box, mode);
    cv::Mat aligned = AlignSeeta256(bgr, result.landmarks);
    if (!aligned.empty()) result.feature = recognizer_.Extract(aligned);
    return result;
}

ExtractResult PipelineV1::FromImage(const cv::Mat &bgr, LandmarkMode mode) {
    if (!detector_) return {};
    std::vector<DetectedBox> boxes = detector_->Detect(bgr);
    if (boxes.empty()) return {};
    return FromBox(bgr, boxes[0], mode);
}

}  // namespace face
