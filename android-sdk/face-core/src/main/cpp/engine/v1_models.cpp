// Build with -ffp-contract=off: the float32 expressions below must round exactly like NumPy's
// element-wise operations, and fused multiply-adds would not.
#include "v1_models.h"

#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace face {

// ---------------------------------------------------------------- FaceBoxes

static const int kBoxesSize = 640;

FaceBoxes::FaceBoxes(std::unique_ptr<NetSession> model) : model_(std::move(model)) {
    // FaceBoxesV2.generate_anchors: built in float64, used as float32 by the vectorized decode.
    const int steps[3] = {32, 64, 128};
    const std::vector<std::vector<int>> min_sizes = {{32, 64, 128}, {256}, {512}};
    const double offsets32[4] = {0.0, 0.25, 0.5, 0.75}, offsets64[2] = {0.0, 0.5};
    for (int k = 0; k < 3; ++k) {
        int fh = static_cast<int>(std::ceil(kBoxesSize / static_cast<double>(steps[k])));
        int fw = fh;
        for (int i = 0; i < fh; ++i) {
            for (int j = 0; j < fw; ++j) {
                for (int min_size : min_sizes[k]) {
                    double s = min_size / static_cast<double>(kBoxesSize);
                    auto push = [&](double ox, double oy) {
                        anchors_.push_back(static_cast<float>((j + ox) * steps[k] / kBoxesSize));
                        anchors_.push_back(static_cast<float>((i + oy) * steps[k] / kBoxesSize));
                        anchors_.push_back(static_cast<float>(s));
                        anchors_.push_back(static_cast<float>(s));
                    };
                    if (min_size == 32) {
                        for (double oy : offsets32) for (double ox : offsets32) push(ox, oy);
                    } else if (min_size == 64) {
                        for (double oy : offsets64) for (double ox : offsets64) push(ox, oy);
                    } else {
                        push(0.5, 0.5);
                    }
                }
            }
        }
    }
    input_.resize(3 * kBoxesSize * kBoxesSize);
}

// Box.iou_of: inclusive (+1) areas, float32.
static float IouInclusive(const DetectedBox &a, const DetectedBox &b) {
    float ix1 = std::max(a.x1, b.x1), iy1 = std::max(a.y1, b.y1);
    float ix2 = std::min(a.x2, b.x2), iy2 = std::min(a.y2, b.y2);
    if (!(ix1 < ix2 && iy1 < iy2)) return 0.f;
    float inter = (ix2 - ix1 + 1.0f) * (iy2 - iy1 + 1.0f);
    float area_a = (a.x2 - a.x1 + 1.0f) * (a.y2 - a.y1 + 1.0f);
    float area_b = (b.x2 - b.x1 + 1.0f) * (b.y2 - b.y1 + 1.0f);
    return inter / (area_a + area_b - inter);
}

std::vector<DetectedBox> FaceBoxes::Detect(const cv::Mat &bgr, float score_threshold, float iou_threshold, int topk) {
    cv::Mat canvas;
    cv::resize(bgr, canvas, cv::Size(kBoxesSize, kBoxesSize));
    const float mean[3] = {104.f, 117.f, 123.f};
    const int plane = kBoxesSize * kBoxesSize;
    for (int y = 0; y < kBoxesSize; ++y) {
        const uint8_t *row = canvas.ptr<uint8_t>(y);
        for (int x = 0; x < kBoxesSize; ++x)
            for (int c = 0; c < 3; ++c) input_[c * plane + y * kBoxesSize + x] = static_cast<float>(row[x * 3 + c]) - mean[c];
    }
    auto out = model_->Run(input_.data(), {1, 3, kBoxesSize, kBoxesSize});
    const std::vector<float> &deltas = out[0], &probs = out[1];
    const size_t count = anchors_.size() / 4;
    if (deltas.size() != count * 4 || probs.size() != count * 2) throw std::runtime_error("FaceBoxes anchor count mismatch");

    const float img_w = static_cast<float>(bgr.cols), img_h = static_cast<float>(bgr.rows);
    std::vector<DetectedBox> boxes;
    const size_t max_boxes = 30001;  // max_nms + 1, as in generate_bboxes
    for (size_t i = 0; i < count && boxes.size() < max_boxes; ++i) {
        float conf = probs[i * 2 + 1];
        if (!(conf >= score_threshold)) continue;
        const float *a = &anchors_[i * 4];
        const float *d = &deltas[i * 4];
        float t;
        t = d[0] * 0.1f; t = t * a[2]; float cx = a[0] + t;
        t = d[1] * 0.1f; t = t * a[3]; float cy = a[1] + t;
        float w = a[2] * std::exp(d[2] * 0.2f);
        float h = a[3] * std::exp(d[3] * 0.2f);
        float hw = w / 2.0f, hh = h / 2.0f;
        boxes.push_back({(cx - hw) * img_w, (cy - hh) * img_h, (cx + hw) * img_w, (cy + hh) * img_h, conf});
    }
    // hard_nms: stable sort by score, keep a box unless it overlaps a kept one.
    std::stable_sort(boxes.begin(), boxes.end(), [](const DetectedBox &l, const DetectedBox &r) { return l.score > r.score; });
    std::vector<char> merged(boxes.size(), 0);
    std::vector<DetectedBox> kept;
    for (size_t i = 0; i < boxes.size() && static_cast<int>(kept.size()) < topk; ++i) {
        if (merged[i]) continue;
        merged[i] = 1;
        for (size_t j = i + 1; j < boxes.size(); ++j)
            if (!merged[j] && IouInclusive(boxes[i], boxes[j]) > iou_threshold) merged[j] = 1;
        kept.push_back(boxes[i]);
    }
    return kept;
}

// ---------------------------------------------------------------- Landmarker5

Landmarker5::Landmarker5(std::unique_ptr<NetSession> net1, std::unique_ptr<NetSession> net2)
    : net1_(std::move(net1)), net2_(std::move(net2)), gray_(112 * 112) {}

Landmarks5 Landmarker5::Detect(const cv::Mat &bgr, float bx1, float by1, float bx2, float by2, bool subpixel) {
    // crop_face: round half to even (Python round), zero padding outside the image.
    const int x0 = static_cast<int>(std::nearbyint(bx1)), y0 = static_cast<int>(std::nearbyint(by1));
    const int x1 = static_cast<int>(std::nearbyint(bx2)), y1 = static_cast<int>(std::nearbyint(by2));
    if (x1 <= x0 || y1 <= y0) throw std::runtime_error("Empty landmark box");
    cv::Mat crop = cv::Mat::zeros(y1 - y0, x1 - x0, CV_8UC3);
    cv::Rect inside = cv::Rect(x0, y0, x1 - x0, y1 - y0) & cv::Rect(0, 0, bgr.cols, bgr.rows);
    if (inside.area() > 0) bgr(inside).copyTo(crop(cv::Rect(inside.x - x0, inside.y - y0, inside.width, inside.height)));
    cv::Mat resized, gray;
    cv::resize(crop, resized, cv::Size(112, 112));
    cv::cvtColor(resized, gray, cv::COLOR_BGR2GRAY);
    for (int y = 0; y < 112; ++y) {
        const uint8_t *row = gray.ptr<uint8_t>(y);
        for (int x = 0; x < 112; ++x) gray_[y * 112 + x] = row[x];
    }
    auto out1 = net1_->Run(gray_.data(), {1, 1, 112, 112});
    const std::vector<float> &feat = out1[0], &pos = out1[1];
    const auto &fshape = net1_->OutputShape(0);  // 1 x C x H x W
    const int channels = static_cast<int>(fshape[1]), fh = static_cast<int>(fshape[2]), fw = static_cast<int>(fshape[3]);

    // shape_index_process: a patch of net1 features around each landmark, zero outside the map.
    const int ph = static_cast<int>(15 * fh / 112.0 + 0.5), pw = static_cast<int>(15 * fw / 112.0 + 0.5);
    // float32 position times int minus a Python float stays float32 in NumPy.
    const float rh = static_cast<float>((ph - 1) / 2.0), rw = static_cast<float>((pw - 1) / 2.0);
    const int marks = static_cast<int>(pos.size() / 2);
    shape_index_.assign(static_cast<size_t>(channels) * ph * marks * pw, 0.f);
    for (int i = 0; i < marks; ++i) {
        float fy = pos[2 * i + 1] * static_cast<float>(fh - 1); fy = fy - rh; fy = fy + 0.5f;
        float fx = pos[2 * i] * static_cast<float>(fw - 1); fx = fx - rw; fx = fx + 0.5f;
        const int y = static_cast<int>(fy), x = static_cast<int>(fx);
        for (int c = 0; c < channels; ++c)
            for (int py = 0; py < ph; ++py) {
                const int sy = y + py;
                if (sy < 0 || sy >= fh) continue;
                for (int px = 0; px < pw; ++px) {
                    const int sx = x + px;
                    if (sx < 0 || sx >= fw) continue;
                    shape_index_[((static_cast<size_t>(c) * ph + py) * marks + i) * pw + px] = feat[(static_cast<size_t>(c) * fh + sy) * fw + sx];
                }
            }
    }
    auto out2 = net2_->Run(shape_index_.data(), {1, static_cast<int64_t>(shape_index_.size()), 1, 1});
    const std::vector<float> &refine = out2[0];

    const double scale_x = (x1 - x0) / 112.0, scale_y = (y1 - y0) / 112.0;
    const double max_x = bgr.cols - 0.01, max_y = bgr.rows - 0.01;
    Landmarks5 points;
    for (int k = 0; k < 5; ++k) {
        float lx = (refine[2 * k] + pos[2 * k]) * 112.0f;
        float ly = (refine[2 * k + 1] + pos[2 * k + 1]) * 112.0f;
        double ux = subpixel ? lx : static_cast<double>(static_cast<int32_t>(lx));
        double uy = subpixel ? ly : static_cast<double>(static_cast<int32_t>(ly));
        points[k].x = std::max(0.01, std::min(x0 + ux * scale_x, max_x));
        points[k].y = std::max(0.01, std::min(y0 + uy * scale_y, max_y));
    }
    return points;
}

// ---------------------------------------------------------------- Seeta alignment

cv::Mat AlignSeeta256(const cv::Mat &bgr, const Landmarks5 &points) {
    // transformation_maker. Under NumPy 2 the Python sums mix a Python float with float32 values,
    // which NumPy evaluates in float32; the arithmetic below keeps that precision and order.
    static const double mean_face[10] = {89.3095, 72.9025, 169.3095, 72.9025, 127.8949, 127.0441,
                                         96.8796, 184.8907, 159.1065, 184.7601};
    float sum_x = 0, sum_y = 0, sum_u = 0, sum_v = 0, sum_xx_yy = 0, sum_ux_vy = 0, sum_vx_uy = 0;
    for (int c = 0; c < 5; ++c) {
        const float sx = static_cast<float>(mean_face[2 * c] * 256 / 256);
        const float sy = static_cast<float>(mean_face[2 * c + 1] * 256 / 256);
        const float fu = static_cast<float>(points[c].x), fv = static_cast<float>(points[c].y);
        sum_x += sx;
        sum_y += sy;
        sum_u += fu;
        sum_v += fv;
        float t1 = sx * sx, t2 = sy * sy;
        sum_xx_yy += t1 + t2;
        t1 = sx * fu; t2 = sy * fv;
        sum_ux_vy += t1 + t2;
        t1 = fv * sx; t2 = fu * sy;
        sum_vx_uy += t1 - t2;
    }
    const float eps = std::numeric_limits<float>::epsilon();
    if (sum_xx_yy <= eps) return cv::Mat();
    float q = sum_u - sum_x * sum_ux_vy / sum_xx_yy + sum_y * sum_vx_uy / sum_xx_yy;
    float p = sum_v - sum_y * sum_ux_vy / sum_xx_yy - sum_x * sum_vx_uy / sum_xx_yy;
    float r = 5 - (sum_x * sum_x + sum_y * sum_y) / sum_xx_yy;
    if (std::fabs(r) <= eps) return cv::Mat();
    const double a = (sum_ux_vy - sum_x * q / r - sum_y * p / r) / sum_xx_yy;
    const double b = (sum_vx_uy + sum_y * q / r - sum_x * p / r) / sum_xx_yy;
    const double c = q / r, d = p / r;

    // spatial_transform + sampling: float64 bilinear, truncated to uint8, zero outside.
    const double t0 = a, t1 = -b, t2 = c, t3 = b, t4 = a, t5 = d;
    const int H = bgr.rows, W = bgr.cols;
    cv::Mat out = cv::Mat::zeros(256, 256, CV_8UC3);
    for (int i = 0; i < 256; ++i) {
        uint8_t *dst = out.ptr<uint8_t>(i);
        for (int j = 0; j < 256; ++j) {
            const double src_col = t0 * j + t1 * i + t2;
            const double src_row = t3 * j + t4 * i + t5;
            const double fr = std::floor(src_row), fc = std::floor(src_col);
            const long long ur = static_cast<long long>(fr), uc = static_cast<long long>(fc);
            if (!(ur >= 0 && ur < H - 1 && uc >= 0 && uc < W - 1)) continue;
            const double x = src_row - static_cast<double>(ur), y = src_col - static_cast<double>(uc);
            const uint8_t *top = bgr.ptr<uint8_t>(static_cast<int>(ur)) + uc * 3;
            const uint8_t *bottom = bgr.ptr<uint8_t>(static_cast<int>(ur) + 1) + uc * 3;
            for (int ch = 0; ch < 3; ++ch) {
                const double it = (1 - y) * top[ch] + y * top[ch + 3];
                const double ib = (1 - y) * bottom[ch] + y * bottom[ch + 3];
                const double v = (1 - x) * it + x * ib;
                dst[j * 3 + ch] = static_cast<uint8_t>(std::min(255.0, std::max(0.0, v)));
            }
        }
    }
    return out;
}

// ---------------------------------------------------------------- Recognizer

Recognizer1024::Recognizer1024(std::unique_ptr<NetSession> model) : model_(std::move(model)), input_(3 * 248 * 248) {}

std::vector<float> Recognizer1024::Extract(const cv::Mat &aligned256) {
    cv::Mat resized;
    if (aligned256.rows != 248 || aligned256.cols != 248) cv::resize(aligned256, resized, cv::Size(248, 248));
    else resized = aligned256;
    const int plane = 248 * 248;
    for (int y = 0; y < 248; ++y) {
        const uint8_t *row = resized.ptr<uint8_t>(y);
        for (int x = 0; x < 248; ++x)
            for (int c = 0; c < 3; ++c) input_[c * plane + y * 248 + x] = row[x * 3 + (2 - c)];  // BGR -> RGB
    }
    auto out = model_->Run(input_.data(), {1, 3, 248, 248});
    std::vector<float> feature = out[0];
    float sum = 0.f;
    for (float &v : feature) {
        v = std::sqrt(v);
        sum += v * v;
    }
    const float norm = std::sqrt(sum);
    if (!(norm > 0.f) || !std::isfinite(norm)) return {};
    for (float &v : feature) v /= norm;
    return feature;
}

}  // namespace face
