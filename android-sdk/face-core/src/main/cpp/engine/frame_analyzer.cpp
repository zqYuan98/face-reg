#include "frame_analyzer.h"

#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include "image_ops.h"

namespace face {

FrameAnalyzer::FrameAnalyzer(std::unique_ptr<NetSession> scrfd, AAssetManager *assets, std::vector<ModelConfig> live_configs)
    : detector_(std::move(scrfd)) {
    if (live_.LoadModel(assets, live_configs) != 0) throw std::runtime_error("liveness models failed to load");
}

void FrameAnalyzer::InvalidateFrame() {
    // Keep the vector's size too: clearing it would make the next resize zero-fill a whole frame.
    width_ = height_ = rotation_ = 0;
    converted_ = false;
}

bool FrameAnalyzer::SetNv21(const uint8_t *nv21, int width, int height, int rotation_degrees) {
    InvalidateFrame();
    const int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (nv21 == nullptr || width <= 0 || height <= 0 || (width & 1) || (height & 1) || rotation % 90 != 0) return false;
    const int64_t pixels = static_cast<int64_t>(width) * height;
    const int64_t bytes = pixels + pixels / 2;
    if (bytes > std::numeric_limits<int>::max()) return false; // image helpers use int offsets
    const size_t size = static_cast<size_t>(bytes);
    nv21_.resize(size);
    std::memcpy(nv21_.data(), nv21, size);
    width_ = width;
    height_ = height;
    rotation_ = rotation;
    converted_ = false;
    return true;
}

std::vector<ScrfdFace> FrameAnalyzer::DetectSampled(float x, float y, float w, float h, int long_side, float threshold) {
    if (width_ <= 0 || height_ <= 0 || nv21_.empty()) throw std::logic_error("Detection requires a valid NV21 frame");
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(w) || !std::isfinite(h)
        || !std::isfinite(x + w) || !std::isfinite(y + h) || !(w > 0) || !(h > 0)
        || long_side < 32 || !std::isfinite(threshold) || threshold < 0 || threshold > 1)
        throw std::invalid_argument("Invalid SCRFD sampling arguments");
    const int64_t aligned_side = (static_cast<int64_t>(long_side) + 31) / 32 * 32;
    if (aligned_side * aligned_side > std::numeric_limits<int>::max() / 3)
        throw std::invalid_argument("SCRFD input exceeds integer indexing limits");
    const float scale = static_cast<float>(long_side) / std::max(w, h);
    const int dst_w = std::max(1, static_cast<int>(std::lround(w * scale))), dst_h = std::max(1, static_cast<int>(std::lround(h * scale)));
    if (!SampleBgr(nv21_.data(), width_, height_, rotation_, x, y, w, h, dst_w, dst_h, sample_))
        throw std::runtime_error("SCRFD frame sampling failed");
    // The sample already has the target long side, so SCRFD only pads it to a multiple of 32.
    std::vector<ScrfdFace> faces = detector_.DetectLongSide(sample_, long_side, threshold);
    const float sx = w / dst_w, sy = h / dst_h;
    for (auto &f : faces) {
        f.box.x1 = x + f.box.x1 * sx;
        f.box.x2 = x + f.box.x2 * sx;
        f.box.y1 = y + f.box.y1 * sy;
        f.box.y2 = y + f.box.y2 * sy;
        for (auto &p : f.kps) {
            p.x = x + p.x * sx;
            p.y = y + p.y * sy;
        }
    }
    return faces;
}

std::vector<ScrfdFace> FrameAnalyzer::Detect(int long_side, float threshold) {
    return DetectSampled(0.f, 0.f, static_cast<float>(width()), static_cast<float>(height()), long_side, threshold);
}

std::vector<ScrfdFace> FrameAnalyzer::DetectRegion(float x, float y, float w, float h, int long_side, float threshold) {
    return DetectSampled(x, y, w, h, long_side, threshold);
}

float FrameAnalyzer::Liveness(int left, int top, int right, int bottom) {
    if (width_ <= 0 || height_ <= 0 || nv21_.empty() || right <= left || bottom <= top) return NAN;
    if (!converted_) {
        cv::Mat yuv(height_ + height_ / 2, width_, CV_8UC1, nv21_.data());
        if (rotation_ == 0) {
            cv::cvtColor(yuv, bgr_, cv::COLOR_YUV2BGR_NV21);
        } else {
            cv::cvtColor(yuv, sensor_, cv::COLOR_YUV2BGR_NV21);
            cv::rotate(sensor_, bgr_, rotation_ == 90 ? cv::ROTATE_90_CLOCKWISE
                                      : rotation_ == 180 ? cv::ROTATE_180 : cv::ROTATE_90_COUNTERCLOCKWISE);
        }
        converted_ = true;
    }
    FaceBox box;
    box.x1 = static_cast<float>(left);
    box.y1 = static_cast<float>(top);
    box.x2 = static_cast<float>(right);
    box.y2 = static_cast<float>(bottom);
    return live_.Detect(bgr_, box);
}

StandaloneLiveness::StandaloneLiveness(AAssetManager *assets, std::vector<ModelConfig> configs) {
    if (live_.LoadModel(assets, configs) != 0) throw std::runtime_error("liveness models failed to load");
}

float StandaloneLiveness::Score(const uint8_t *nv21, int width, int height, int rotation_degrees,
                                int left, int top, int right, int bottom) {
    const int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (width <= 0 || height <= 0 || (width & 1) || (height & 1) || rotation % 90 != 0 || right <= left || bottom <= top)
        return NAN;
    // Same conversion as FrameAnalyzer::Liveness (OpenCV NV21, what the models were trained on).
    cv::Mat yuv(height + height / 2, width, CV_8UC1, const_cast<uint8_t *>(nv21));
    if (rotation == 0) {
        cv::cvtColor(yuv, bgr_, cv::COLOR_YUV2BGR_NV21);
    } else {
        cv::cvtColor(yuv, sensor_, cv::COLOR_YUV2BGR_NV21);
        cv::rotate(sensor_, bgr_, rotation == 90 ? cv::ROTATE_90_CLOCKWISE
                                  : rotation == 180 ? cv::ROTATE_180 : cv::ROTATE_90_COUNTERCLOCKWISE);
    }
    FaceBox box;
    box.x1 = static_cast<float>(left);
    box.y1 = static_cast<float>(top);
    box.x2 = static_cast<float>(right);
    box.y2 = static_cast<float>(bottom);
    return live_.Detect(bgr_, box);
}

}  // namespace face
