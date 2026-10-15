#include "image_ops.h"

#include <opencv2/imgproc.hpp>
#include <vector>

#include <algorithm>
#include <cmath>

namespace face {

static inline uint8_t Clamp(int v) { return static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v)); }

bool Nv21CropToBgr(const uint8_t *nv21, int width, int height, int rotation_degrees,
                   int left, int top, int crop_width, int crop_height, cv::Mat &out) {
    const int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (rotation % 90 != 0 || width <= 0 || height <= 0) return false;
    const int upright_w = rotation % 180 == 0 ? width : height, upright_h = rotation % 180 == 0 ? height : width;
    if (left < 0 || top < 0 || crop_width <= 0 || crop_height <= 0 || left + crop_width > upright_w ||
        top + crop_height > upright_h)
        return false;
    out.create(crop_height, crop_width, CV_8UC3);
    const uint8_t *uv = nv21 + width * height;
    for (int row = 0; row < crop_height; ++row) {
        const int uy = top + row;
        uint8_t *dst = out.ptr<uint8_t>(row);
        for (int col = 0; col < crop_width; ++col) {
            const int ux = left + col;
            int sx, sy;
            switch (rotation) {
                case 90: sx = uy; sy = height - 1 - ux; break;
                case 180: sx = width - 1 - ux; sy = height - 1 - uy; break;
                case 270: sx = width - 1 - uy; sy = ux; break;
                default: sx = ux; sy = uy; break;
            }
            const int y = nv21[sy * width + sx];
            const uint8_t *c = uv + (sy >> 1) * width + (sx & ~1);
            const int v = c[0] - 128, u = c[1] - 128;
            dst[col * 3 + 2] = Clamp(y + ((91881 * v) >> 16));
            dst[col * 3 + 1] = Clamp(y - ((22554 * u + 46802 * v) >> 16));
            dst[col * 3 + 0] = Clamp(y + ((116130 * u) >> 16));
        }
    }
    return true;
}

int g_sample_linear_above = 3;

bool SampleBgr(const uint8_t *nv21, int width, int height, int rotation_degrees,
               float x, float y, float w, float h, int dst_w, int dst_h, cv::Mat &out) {
    const int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (rotation % 90 != 0 || width < 2 || height < 2 || dst_w <= 0 || dst_h <= 0 || !(w > 0) || !(h > 0)) return false;
    const int uw = rotation % 180 == 0 ? width : height, uh = rotation % 180 == 0 ? height : width;
    out.create(dst_h, dst_w, CV_8UC3);
    // Part of the region inside the frame, in upright pixels, and where it lands in the output.
    const float cx0 = std::max(0.f, x), cy0 = std::max(0.f, y);
    const float cx1 = std::min(static_cast<float>(uw), x + w), cy1 = std::min(static_cast<float>(uh), y + h);
    const float kx = dst_w / w, ky = dst_h / h;
    const int dx0 = std::max(0, static_cast<int>(std::lround((cx0 - x) * kx)));
    const int dy0 = std::max(0, static_cast<int>(std::lround((cy0 - y) * ky)));
    const int dx1 = std::min(dst_w, static_cast<int>(std::lround((cx1 - x) * kx)));
    const int dy1 = std::min(dst_h, static_cast<int>(std::lround((cy1 - y) * ky)));
    if (dx1 - dx0 < 2 || dy1 - dy0 < 2) {
        out.setTo(cv::Scalar::all(0));
        return true;
    }
    if (dx0 > 0 || dy0 > 0 || dx1 < dst_w || dy1 < dst_h) out.setTo(cv::Scalar::all(0));
    // The same rectangle in sensor coordinates, widened to even bounds for the 2x2 chroma blocks.
    float sx0, sy0, sx1, sy1;
    switch (rotation) {
        case 90: sx0 = cy0; sx1 = cy1; sy0 = height - cx1; sy1 = height - cx0; break;
        case 180: sx0 = width - cx1; sx1 = width - cx0; sy0 = height - cy1; sy1 = height - cy0; break;
        case 270: sx0 = width - cy1; sx1 = width - cy0; sy0 = cx0; sy1 = cx1; break;
        default: sx0 = cx0; sx1 = cx1; sy0 = cy0; sy1 = cy1; break;
    }
    const int ix0 = std::max(0, static_cast<int>(std::floor(sx0)) & ~1), iy0 = std::max(0, static_cast<int>(std::floor(sy0)) & ~1);
    const int ix1 = std::min(width, (static_cast<int>(std::ceil(sx1)) + 1) & ~1);
    const int iy1 = std::min(height, (static_cast<int>(std::ceil(sy1)) + 1) & ~1);
    if (ix1 - ix0 < 2 || iy1 - iy0 < 2) return true;
    // Output part size in sensor orientation, even for NV21.
    const int pw = dx1 - dx0, ph = dy1 - dy0;
    int tw = rotation % 180 == 0 ? pw : ph, th = rotation % 180 == 0 ? ph : pw;
    tw = std::max(2, (tw + 1) & ~1);
    th = std::max(2, (th + 1) & ~1);
    // NEON-backed resizes of the luma and interleaved chroma planes, then one small conversion.
    const cv::Mat yPlane(height, width, CV_8UC1, const_cast<uint8_t *>(nv21));
    const cv::Mat uvPlane(height / 2, width / 2, CV_8UC2, const_cast<uint8_t *>(nv21) + width * height);
    thread_local std::vector<uint8_t> small;
    small.resize(static_cast<size_t>(tw) * th * 3 / 2);
    cv::Mat ySmall(th, tw, CV_8UC1, small.data()), uvSmall(th / 2, tw / 2, CV_8UC2, small.data() + tw * th);
    const cv::Rect yRect(ix0, iy0, ix1 - ix0, iy1 - iy0), uvRect(ix0 / 2, iy0 / 2, (ix1 - ix0) / 2, (iy1 - iy0) / 2);
    // Area averaging is exact but slow for non-integer ratios; beyond 2x shrink linear is enough
    // for the detector (see the trackbench comparison in the CLI).
    const int ratio = (ix1 - ix0) / std::max(1, tw);
    const int interpolation = (ix1 - ix0) < tw ? cv::INTER_LINEAR
                              : (g_sample_linear_above > 0 && ratio >= g_sample_linear_above) ? cv::INTER_LINEAR : cv::INTER_AREA;
    cv::resize(yPlane(yRect), ySmall, ySmall.size(), 0, 0, interpolation);
    cv::resize(uvPlane(uvRect), uvSmall, uvSmall.size(), 0, 0, interpolation);
    cv::Mat bgr, upright;
    cv::cvtColor(cv::Mat(th + th / 2, tw, CV_8UC1, small.data()), bgr, cv::COLOR_YUV2BGR_NV21);
    if (rotation == 0) upright = bgr;
    else cv::rotate(bgr, upright, rotation == 90 ? cv::ROTATE_90_CLOCKWISE
                                  : rotation == 180 ? cv::ROTATE_180 : cv::ROTATE_90_COUNTERCLOCKWISE);
    cv::Mat target = out(cv::Rect(dx0, dy0, pw, ph));
    if (upright.cols == pw && upright.rows == ph) upright.copyTo(target);
    else cv::resize(upright, target, target.size(), 0, 0, cv::INTER_LINEAR);
    return true;
}

}  // namespace face
