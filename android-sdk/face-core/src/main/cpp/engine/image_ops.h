#ifndef FACE_ENGINE_IMAGE_OPS_H
#define FACE_ENGINE_IMAGE_OPS_H

#include <opencv2/core/mat.hpp>
#include <cstdint>

namespace face {

/**
 * Converts one upright-frame rectangle of a sensor-orientation NV21 frame to BGR with full-range
 * (JFIF) BT.601, the same integer formula as Java Nv21.cropToArgb. Full range matches what the
 * enrolment service sees when it decodes a JPEG photo. Returns false if the rectangle is invalid.
 */
bool Nv21CropToBgr(const uint8_t *nv21, int width, int height, int rotation_degrees,
                   int left, int top, int crop_width, int crop_height, cv::Mat &out);

}  // namespace face

#endif  // FACE_ENGINE_IMAGE_OPS_H
