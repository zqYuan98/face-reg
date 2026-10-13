#ifndef FACE_UPRIGHT_FRAME_H
#define FACE_UPRIGHT_FRAME_H

#include <opencv2/core/mat.hpp>

/**
 * One camera frame converted to upright BGR once, then shared by the face detector and both
 * liveness models. Owned by com.mv.engine.UprightFrame; used from a single thread.
 */
struct UprightFrame {
    cv::Mat bgr;
    cv::Mat sensor; // conversion scratch in sensor orientation, reused across frames
};

#endif //FACE_UPRIGHT_FRAME_H
