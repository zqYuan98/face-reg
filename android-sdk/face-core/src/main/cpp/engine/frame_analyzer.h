// Per-frame work on the analysis thread: one NV21 -> upright BGR conversion shared by SCRFD
// detection (faces + 5 points) and the minivision silent-liveness models.
#ifndef FACE_ENGINE_FRAME_ANALYZER_H
#define FACE_ENGINE_FRAME_ANALYZER_H

#include <android/asset_manager.h>
#include <memory>
#include <vector>
#include "../definition.h"
#include "../live/live.h"
#include "insightface_models.h"

namespace face {

class FrameAnalyzer {
public:
    FrameAnalyzer(std::unique_ptr<NetSession> scrfd, AAssetManager *assets, std::vector<ModelConfig> live_configs);

    /** Sensor-orientation NV21 -> upright BGR (OpenCV conversion, what the liveness models were trained on). */
    bool SetNv21(const uint8_t *nv21, int width, int height, int rotation_degrees);

    /** Faces in upright frame coordinates; the frame's long side is scaled to {@code long_side}. */
    std::vector<ScrfdFace> Detect(int long_side, float threshold);

    /** Mean real-face probability of the liveness models for a minivision-style square box (inclusive). */
    float Liveness(int left, int top, int right, int bottom);

    int width() const { return bgr_.cols; }
    int height() const { return bgr_.rows; }

private:
    Scrfd detector_;
    Live live_;
    cv::Mat sensor_, bgr_;
};

}  // namespace face

#endif  // FACE_ENGINE_FRAME_ANALYZER_H
