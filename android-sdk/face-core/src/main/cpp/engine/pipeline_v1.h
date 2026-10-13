#ifndef FACE_ENGINE_PIPELINE_V1_H
#define FACE_ENGINE_PIPELINE_V1_H

#include <memory>
#include <vector>
#include "v1_models.h"

namespace face {

enum class LandmarkMode {
    kTruncated = 0,  // exactly the enrolment service
    kSubpixel = 1,   // keep the fractional landmark positions
    kTta = 2,        // sub-pixel, averaged over five jittered boxes (five runs of a ~1 ms net)
};

struct ExtractResult {
    std::vector<float> feature;  // empty on failure
    Landmarks5 landmarks{};
    DetectedBox box{};
};

/**
 * Feature version recognizer_v1_1024 without the steps the prebuilt library wasted per call:
 * no rotation classifier (the camera knows its rotation), no 640x640 detection when a tracker
 * already has the box, no FSANet (pose comes from the five points). Not thread safe.
 */
class PipelineV1 {
public:
    PipelineV1(std::unique_ptr<NetSession> landmark1, std::unique_ptr<NetSession> landmark2,
               std::unique_ptr<NetSession> recognizer, std::unique_ptr<NetSession> detector);

    /** Face already located (tracker box in {@code bgr} coordinates). */
    ExtractResult FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode);

    /** Still image: FaceBoxes picks the highest-scoring face, as the enrolment service does. */
    ExtractResult FromImage(const cv::Mat &bgr, LandmarkMode mode);

private:
    Landmarks5 Points(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode);

    Landmarker5 landmarker_;
    Recognizer1024 recognizer_;
    std::unique_ptr<FaceBoxes> detector_;
};

}  // namespace face

#endif  // FACE_ENGINE_PIPELINE_V1_H
