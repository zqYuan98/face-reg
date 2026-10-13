// Feature version recognizer_v1_1024: the models and pre/post-processing of the enrolment service
// (FaceFeatureExtractorAPI). Every numeric step mirrors the Python code so device features land in
// the same space as enrolled ones; comments name the Python function each piece reproduces.
#ifndef FACE_ENGINE_V1_MODELS_H
#define FACE_ENGINE_V1_MODELS_H

#include <opencv2/core/mat.hpp>
#include <array>
#include <memory>
#include <vector>
#include "ort_model.h"

namespace face {

struct DetectedBox {
    float x1, y1, x2, y2, score;
};

/** Five points: left eye, right eye, nose tip, left mouth corner, right mouth corner. */
using Landmarks5 = std::array<cv::Point2d, 5>;

/** models/facedetector.py FaceBoxesV2 (fixed 640x640 input). */
class FaceBoxes {
public:
    explicit FaceBoxes(std::unique_ptr<NetSession> model);
    std::vector<DetectedBox> Detect(const cv::Mat &bgr, float score_threshold = 0.35f,
                                    float iou_threshold = 0.45f, int topk = 1);

private:
    std::unique_ptr<NetSession> model_;
    std::vector<float> anchors_;  // cx, cy, s_kx, s_ky in float32, as the Python decode uses them
    std::vector<float> input_;
};

/** models/facelandmarks5er.py Landmark5er: two-stage SeetaFace 5-point net. */
class Landmarker5 {
public:
    Landmarker5(std::unique_ptr<NetSession> net1, std::unique_ptr<NetSession> net2);
    /**
     * {@code subpixel=false} reproduces the enrolment service, which truncates the points to whole
     * units of the 112x112 crop (about 2.7 px on a 300 px face). True keeps the fraction.
     */
    Landmarks5 Detect(const cv::Mat &bgr, float x1, float y1, float x2, float y2, bool subpixel = false);

private:
    std::unique_ptr<NetSession> net1_, net2_;
    std::vector<float> gray_, shape_index_;
};

/** models/facealign.py FaceAlign: 256x256 similarity warp with the service's own bilinear sampler. */
cv::Mat AlignSeeta256(const cv::Mat &bgr, const Landmarks5 &points);

/** models/facerecoger.py FaceRecoger: 256 crop resized to 248, RGB, raw 0..255, sqrt + L2. */
class Recognizer1024 {
public:
    explicit Recognizer1024(std::unique_ptr<NetSession> model);
    std::vector<float> Extract(const cv::Mat &aligned256);

private:
    std::unique_ptr<NetSession> model_;
    std::vector<float> input_;
};

}  // namespace face

#endif  // FACE_ENGINE_V1_MODELS_H
