// Common interface of the feature pipelines the JNI layer can host.
#ifndef FACE_ENGINE_PIPELINE_H
#define FACE_ENGINE_PIPELINE_H

#include <opencv2/core/mat.hpp>
#include <vector>
#include "v1_models.h"

namespace face {

enum class LandmarkMode {
    kTruncated = 0,  // v1 only: exactly the enrolment service
    kSubpixel = 1,   // v1 only: keep the fractional landmark positions
    kTta = 2,        // v1 only: sub-pixel, averaged over five jittered boxes
};

struct ExtractResult {
    std::vector<float> feature;  // empty on failure; L2-normalised
    Landmarks5 landmarks{};
    DetectedBox box{};
};

class FacePipeline {
public:
    virtual ~FacePipeline() = default;
    /** Face already located (tracker box in {@code bgr} coordinates). */
    virtual ExtractResult FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode) = 0;
    /** Still image such as an enrolment photo: the pipeline finds the face itself. */
    virtual ExtractResult FromImage(const cv::Mat &bgr, LandmarkMode mode) = 0;
};

}  // namespace face

#endif  // FACE_ENGINE_PIPELINE_H
