#include "pipeline_insightface.h"

#include <algorithm>

namespace face {

namespace {
const int kCropInput = 320;   // SCRFD input for the crop around a tracked face
const int kImageInput = 640;  // SCRFD input for still images (insightface default det_size)

float Iou(const DetectedBox &a, const DetectedBox &b) {
    const float w = std::min(a.x2, b.x2) - std::max(a.x1, b.x1), h = std::min(a.y2, b.y2) - std::max(a.y1, b.y1);
    if (w <= 0 || h <= 0) return 0.f;
    const float inter = w * h;
    return inter / ((a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter);
}
}  // namespace

PipelineInsightFace::PipelineInsightFace(std::unique_ptr<NetSession> detector, std::unique_ptr<NetSession> recognizer)
    : detector_(std::move(detector)), recognizer_(std::move(recognizer)) {}

ExtractResult PipelineInsightFace::FromFace(const cv::Mat &bgr, const ScrfdFace &face) {
    ExtractResult result;
    result.box = face.box;
    result.landmarks = face.kps;
    cv::Mat aligned = NormCrop112(bgr, face.kps);
    if (!aligned.empty()) result.feature = recognizer_.Extract(aligned);
    return result;
}

ExtractResult PipelineInsightFace::FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode) {
    // Square crop of 1.6x the box side keeps the whole head with context, so SCRFD sees the face at
    // about 60% of its 320 input, well inside its training range.
    const float cx = (box.x1 + box.x2) / 2, cy = (box.y1 + box.y2) / 2;
    const float half = std::max(box.x2 - box.x1, box.y2 - box.y1) * 0.8f;
    const cv::Rect region = cv::Rect(static_cast<int>(cx - half), static_cast<int>(cy - half),
                                     static_cast<int>(2 * half), static_cast<int>(2 * half)) &
                            cv::Rect(0, 0, bgr.cols, bgr.rows);
    if (region.width < 16 || region.height < 16) return {};
    std::vector<ScrfdFace> faces = detector_.Detect(bgr(region), kCropInput);
    const ScrfdFace *best = nullptr;
    float best_iou = 0.3f;  // must overlap the tracked face, not a neighbour
    for (auto &f : faces) {
        f.box.x1 += region.x; f.box.x2 += region.x; f.box.y1 += region.y; f.box.y2 += region.y;
        for (auto &p : f.kps) { p.x += region.x; p.y += region.y; }
        const float iou = Iou(f.box, box);
        if (iou > best_iou) { best_iou = iou; best = &f; }
    }
    return best ? FromFace(bgr, *best) : ExtractResult{};
}

ExtractResult PipelineInsightFace::FromImage(const cv::Mat &bgr, LandmarkMode) {
    std::vector<ScrfdFace> faces = detector_.Detect(bgr, kImageInput);
    if (faces.empty()) return {};
    // Enrolment photos: the largest face is the subject.
    auto area = [](const ScrfdFace &f) { return (f.box.x2 - f.box.x1) * (f.box.y2 - f.box.y1); };
    const ScrfdFace &largest = *std::max_element(faces.begin(), faces.end(),
                                                 [&](const ScrfdFace &a, const ScrfdFace &b) { return area(a) < area(b); });
    return FromFace(bgr, largest);
}

}  // namespace face
