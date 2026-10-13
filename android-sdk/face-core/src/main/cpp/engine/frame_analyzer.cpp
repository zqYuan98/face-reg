#include "frame_analyzer.h"

#include <opencv2/imgproc.hpp>
#include <cmath>
#include <stdexcept>

namespace face {

FrameAnalyzer::FrameAnalyzer(std::unique_ptr<NetSession> scrfd, AAssetManager *assets, std::vector<ModelConfig> live_configs)
    : detector_(std::move(scrfd)) {
    if (live_.LoadModel(assets, live_configs) != 0) throw std::runtime_error("liveness models failed to load");
}

bool FrameAnalyzer::SetNv21(const uint8_t *nv21, int width, int height, int rotation_degrees) {
    const int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (width <= 0 || height <= 0 || (width & 1) || (height & 1) || rotation % 90 != 0) return false;
    cv::Mat yuv(height + height / 2, width, CV_8UC1, const_cast<uint8_t *>(nv21));
    if (rotation == 0) {
        cv::cvtColor(yuv, bgr_, cv::COLOR_YUV2BGR_NV21);
    } else {
        cv::cvtColor(yuv, sensor_, cv::COLOR_YUV2BGR_NV21);
        cv::rotate(sensor_, bgr_, rotation == 90 ? cv::ROTATE_90_CLOCKWISE
                                  : rotation == 180 ? cv::ROTATE_180 : cv::ROTATE_90_COUNTERCLOCKWISE);
    }
    return true;
}

std::vector<ScrfdFace> FrameAnalyzer::Detect(int long_side, float threshold) {
    if (bgr_.empty()) return {};
    return detector_.DetectLongSide(bgr_, long_side, threshold);
}

float FrameAnalyzer::Liveness(int left, int top, int right, int bottom) {
    if (bgr_.empty() || right <= left || bottom <= top) return NAN;
    FaceBox box;
    box.x1 = static_cast<float>(left);
    box.y1 = static_cast<float>(top);
    box.x2 = static_cast<float>(right);
    box.y2 = static_cast<float>(bottom);
    return live_.Detect(bgr_, box);
}

}  // namespace face
