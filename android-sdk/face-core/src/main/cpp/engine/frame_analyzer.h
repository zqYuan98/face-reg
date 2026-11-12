// Per-frame work on the analysis thread. The raw NV21 frame is kept as delivered and each step
// converts only what it needs: detection samples a small upright image (whole frame or a region
// around a tracked face), liveness converts the full frame lazily, only on frames it runs on.
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

    /** Clears eligibility for detection/liveness after invalid input, retaining reusable capacity. */
    void InvalidateFrame();

    /** Keeps a copy of a sensor-orientation NV21 frame; nothing is converted yet. */
    bool SetNv21(const uint8_t *nv21, int width, int height, int rotation_degrees);

    /** Whole-frame detection, upright coordinates; the frame's long side is sampled to {@code long_side}. */
    std::vector<ScrfdFace> Detect(int long_side, float threshold);

    /**
     * Detection inside an upright-frame region (any position; outside the frame is black), sampled
     * so its long side is {@code long_side}. Used to follow a tracked face without a full scan.
     */
    std::vector<ScrfdFace> DetectRegion(float x, float y, float w, float h, int long_side, float threshold);

    /**
     * Mean real-face probability of the liveness models for a minivision-style square box
     * (inclusive). Converts the full frame with OpenCV on first use per frame, as the models were
     * trained on; frames without a liveness call never pay for it.
     */
    float Liveness(int left, int top, int right, int bottom);

    int width() const { return rotation_ % 180 == 0 ? width_ : height_; }
    int height() const { return rotation_ % 180 == 0 ? height_ : width_; }

private:
    std::vector<ScrfdFace> DetectSampled(float x, float y, float w, float h, int long_side, float threshold);

    Scrfd detector_;
    Live live_;
    std::vector<uint8_t> nv21_;
    int width_ = 0, height_ = 0, rotation_ = 0;
    bool converted_ = false;
    cv::Mat sensor_, bgr_, sample_;
};

/**
 * Silent liveness on its own thread: owns its models and converts the frame it is given, so it
 * never touches the analyzer's per-frame buffers. Not thread safe; one instance per worker.
 */
class StandaloneLiveness {
public:
    StandaloneLiveness(AAssetManager *assets, std::vector<ModelConfig> configs);
    /** Sensor-orientation NV21, upright-frame minivision box (inclusive); NaN on failure. */
    float Score(const uint8_t *nv21, int width, int height, int rotation_degrees, int left, int top, int right, int bottom);

private:
    Live live_;
    cv::Mat sensor_, bgr_;
};

}  // namespace face

#endif  // FACE_ENGINE_FRAME_ANALYZER_H
