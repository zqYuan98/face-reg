#ifndef FACE_ENGINE_PIPELINE_V1_H
#define FACE_ENGINE_PIPELINE_V1_H

#include <memory>
#include <vector>
#include "pipeline.h"
#include "v1_models.h"

namespace face {

/**
 * Feature version recognizer_v1_1024 without the steps the prebuilt library wasted per call:
 * no rotation classifier (the camera knows its rotation), no 640x640 detection when a tracker
 * already has the box, no FSANet (pose comes from the five points). Not thread safe.
 */
class PipelineV1 : public FacePipeline {
public:
    PipelineV1(std::unique_ptr<NetSession> landmark1, std::unique_ptr<NetSession> landmark2,
               std::unique_ptr<NetSession> recognizer, std::unique_ptr<NetSession> detector);

    /** Face already located (tracker box in {@code bgr} coordinates). */
    ExtractResult FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode) override;

    /** Still image: FaceBoxes picks the highest-scoring face, as the enrolment service does. */
    ExtractResult FromImage(const cv::Mat &bgr, LandmarkMode mode) override;

private:
    Landmarks5 Points(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode);

    Landmarker5 landmarker_;
    Recognizer1024 recognizer_;
    std::unique_ptr<FaceBoxes> detector_;
};

}  // namespace face

#endif  // FACE_ENGINE_PIPELINE_V1_H
