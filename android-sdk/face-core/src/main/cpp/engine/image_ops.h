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

/**
 * Upright-frame rectangle [x, x+w) x [y, y+h) (any real values; outside the frame reads as black)
 * resampled to dst_w x dst_h BGR for the detector. The luma and chroma planes are first resized
 * in sensor orientation (OpenCV, area averaging when shrinking), then only the small image is
 * converted (OpenCV NV21, the range the liveness path also uses) and rotated. Bounds snap to the
 * 2x2 chroma grid, so positions are exact to about one source pixel. False for invalid arguments.
 */
bool SampleBgr(const uint8_t *nv21, int width, int height, int rotation_degrees,
               float x, float y, float w, float h, int dst_w, int dst_h, cv::Mat &out);

}  // namespace face

#endif  // FACE_ENGINE_IMAGE_OPS_H
