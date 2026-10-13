#include "insightface_models.h"

#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>

namespace face {

// ---------------------------------------------------------------- SCRFD

Scrfd::Scrfd(std::unique_ptr<NetSession> model) : model_(std::move(model)) {}

static float IouPlusOne(const DetectedBox &a, const DetectedBox &b) {
    const float w = std::max(0.f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1) + 1);
    const float h = std::max(0.f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1) + 1);
    const float inter = w * h;
    return inter / ((a.x2 - a.x1 + 1) * (a.y2 - a.y1 + 1) + (b.x2 - b.x1 + 1) * (b.y2 - b.y1 + 1) - inter);
}

std::vector<ScrfdFace> Scrfd::Detect(const cv::Mat &bgr, int size, float threshold, float nms) {
    // insightface scrfd.py: fit into size x size keeping aspect (same integer rounding).
    const int h = bgr.rows, w = bgr.cols;
    int nh, nw;
    if (static_cast<double>(h) / w > 1) {
        nh = size;
        nw = static_cast<int>(size / (static_cast<double>(h) / w));
    } else {
        nw = size;
        nh = static_cast<int>(size * (static_cast<double>(h) / w));
    }
    return Run(bgr, nw, nh, static_cast<float>(nh) / h, size, size, threshold, nms);
}

std::vector<ScrfdFace> Scrfd::DetectLongSide(const cv::Mat &bgr, int long_side, float threshold, float nms) {
    const int h = bgr.rows, w = bgr.cols;
    const double scale = static_cast<double>(long_side) / std::max(h, w);
    const int nw = std::max(1, static_cast<int>(std::lround(w * scale))), nh = std::max(1, static_cast<int>(std::lround(h * scale)));
    const int in_w = (nw + 31) / 32 * 32, in_h = (nh + 31) / 32 * 32;
    return Run(bgr, nw, nh, static_cast<float>(nh) / h, in_w, in_h, threshold, nms);
}

std::vector<ScrfdFace> Scrfd::Run(const cv::Mat &bgr, int nw, int nh, float scale, int in_w, int in_h,
                                  float threshold, float nms) {
    cv::Mat resized;
    cv::resize(bgr, resized, cv::Size(nw, nh));
    const int plane = in_w * in_h;
    input_.assign(static_cast<size_t>(3) * plane, (0.f - 127.5f) / 128.f);
    for (int y = 0; y < nh; ++y) {
        const uint8_t *row = resized.ptr<uint8_t>(y);
        for (int x = 0; x < nw; ++x)
            for (int c = 0; c < 3; ++c) input_[c * plane + y * in_w + x] = (row[x * 3 + (2 - c)] - 127.5f) / 128.f;
    }
    auto out = model_->Run(input_.data(), {1, 3, in_h, in_w});
    if (out.size() != 9) throw std::runtime_error("SCRFD model must have 9 outputs (scores, boxes, kps x 3 strides)");

    std::vector<ScrfdFace> faces;
    const int strides[3] = {8, 16, 32};
    for (int i = 0; i < 3; ++i) {
        const int stride = strides[i], fw = in_w / stride, fh = in_h / stride;
        const std::vector<float> &scores = out[i], &boxes = out[i + 3], &kps = out[i + 6];
        for (int a = 0; a < fh * fw * 2; ++a) {
            if (scores[a] < threshold) continue;
            const int cell = a / 2;
            const float cx = static_cast<float>((cell % fw) * stride), cy = static_cast<float>((cell / fw) * stride);
            ScrfdFace f;
            f.box = {(cx - boxes[a * 4] * stride) / scale, (cy - boxes[a * 4 + 1] * stride) / scale,
                     (cx + boxes[a * 4 + 2] * stride) / scale, (cy + boxes[a * 4 + 3] * stride) / scale, scores[a]};
            for (int k = 0; k < 5; ++k)
                f.kps[k] = cv::Point2d((cx + kps[a * 10 + 2 * k] * stride) / scale, (cy + kps[a * 10 + 2 * k + 1] * stride) / scale);
            faces.push_back(f);
        }
    }
    std::stable_sort(faces.begin(), faces.end(), [](const ScrfdFace &l, const ScrfdFace &r) { return l.box.score > r.box.score; });
    std::vector<ScrfdFace> kept;
    std::vector<char> removed(faces.size(), 0);
    for (size_t i = 0; i < faces.size(); ++i) {
        if (removed[i]) continue;
        kept.push_back(faces[i]);
        for (size_t j = i + 1; j < faces.size(); ++j)
            if (!removed[j] && IouPlusOne(faces[i].box, faces[j].box) > nms) removed[j] = 1;
    }
    return kept;
}

// ---------------------------------------------------------------- alignment

cv::Mat NormCrop112(const cv::Mat &bgr, const Landmarks5 &kps) {
    static const double dst[5][2] = {{38.2946, 51.6963}, {73.5318, 51.5014}, {56.0252, 71.7366},
                                     {41.5493, 92.3655}, {70.7299, 92.2041}};
    // Umeyama similarity in closed form for 2-D points (rotation + uniform scale + translation).
    double msx = 0, msy = 0, mdx = 0, mdy = 0;
    for (int i = 0; i < 5; ++i) {
        msx += kps[i].x; msy += kps[i].y; mdx += dst[i][0]; mdy += dst[i][1];
    }
    msx /= 5; msy /= 5; mdx /= 5; mdy /= 5;
    double a = 0, b = 0, var = 0;  // a = sum(s.d), b = sum(s x d)
    for (int i = 0; i < 5; ++i) {
        const double sx = kps[i].x - msx, sy = kps[i].y - msy, dx = dst[i][0] - mdx, dy = dst[i][1] - mdy;
        a += sx * dx + sy * dy;
        b += sx * dy - sy * dx;
        var += sx * sx + sy * sy;
    }
    if (!(var > 0)) return cv::Mat();
    const double norm = std::sqrt(a * a + b * b);
    const double scale = norm / var, cos_t = a / norm, sin_t = b / norm;
    cv::Mat m = (cv::Mat_<double>(2, 3) << scale * cos_t, -scale * sin_t, 0, scale * sin_t, scale * cos_t, 0);
    m.at<double>(0, 2) = mdx - (m.at<double>(0, 0) * msx + m.at<double>(0, 1) * msy);
    m.at<double>(1, 2) = mdy - (m.at<double>(1, 0) * msx + m.at<double>(1, 1) * msy);
    cv::Mat out;
    cv::warpAffine(bgr, out, m, cv::Size(112, 112), cv::INTER_LINEAR, cv::BORDER_CONSTANT, cv::Scalar(0, 0, 0));
    return out;
}

// ---------------------------------------------------------------- ArcFace

ArcFace::ArcFace(std::unique_ptr<NetSession> model) : model_(std::move(model)), input_(3 * 112 * 112) {}

std::vector<float> ArcFace::Extract(const cv::Mat &aligned112) {
    const int plane = 112 * 112;
    for (int y = 0; y < 112; ++y) {
        const uint8_t *row = aligned112.ptr<uint8_t>(y);
        for (int x = 0; x < 112; ++x)
            for (int c = 0; c < 3; ++c) input_[c * plane + y * 112 + x] = (row[x * 3 + (2 - c)] - 127.5f) / 127.5f;
    }
    auto out = model_->Run(input_.data(), {1, 3, 112, 112});
    std::vector<float> feature = out[0];
    double sum = 0;
    for (float v : feature) sum += static_cast<double>(v) * v;
    const double norm = std::sqrt(sum);
    if (!(norm > 0) || !std::isfinite(norm)) return {};
    for (float &v : feature) v = static_cast<float>(v / norm);
    return feature;
}

}  // namespace face
