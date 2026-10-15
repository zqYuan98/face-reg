#ifndef FACE_ENGINE_PIPELINE_INSIGHTFACE_H
#define FACE_ENGINE_PIPELINE_INSIGHTFACE_H

#include <memory>
#include "insightface_models.h"
#include "pipeline.h"

namespace face {

/**
 * InsightFace feature version (e.g. w600k_mbf / w600k_r50, 512-d). With a tracker box, SCRFD runs
 * on a small square crop around the box only to place the five points; still images are detected
 * whole at 640, as insightface FaceAnalysis does. Landmark modes do not apply. Not thread safe.
 */
class PipelineInsightFace : public FacePipeline {
public:
    PipelineInsightFace(std::unique_ptr<NetSession> detector, std::unique_ptr<NetSession> recognizer);

    ExtractResult FromBox(const cv::Mat &bgr, const DetectedBox &box, LandmarkMode mode) override;
    /**
     * Five points already known (from the tracker's SCRFD pass): align and extract directly,
     * skipping the detection that FromBox runs on the crop.
     */
    ExtractResult FromKeypoints(const cv::Mat &bgr, const Landmarks5 &kps);

    ExtractResult FromImage(const cv::Mat &bgr, LandmarkMode mode) override;

private:
    ExtractResult FromFace(const cv::Mat &bgr, const ScrfdFace &face);

    Scrfd detector_;
    ArcFace recognizer_;
};

}  // namespace face

#endif  // FACE_ENGINE_PIPELINE_INSIGHTFACE_H
