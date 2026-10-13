#include "image_ops.h"

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

}  // namespace face
