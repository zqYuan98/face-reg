// Command-line harness for the native engine, run on the device over adb.
//
//   face_engine_cli golden <model_dir> <image_list> <out_file> [exact|subpixel|tta] [threads]
//       Detect, landmark, align and extract every image; write boxes, points and features as
//       text for tools/face-eval to compare against the Python enrolment service.
//   face_engine_cli bench <model_dir> <image> <iterations> <threads> [xnnpack]
//       Median time of each model, including the ones the old prebuilt library also ran
//       (rotation classifier, FSANet), so old and new per-extraction cost can be compared.
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <string>
#include <vector>
#include "../pipeline_insightface.h"
#include "../pipeline_v1.h"

using namespace face;

static std::unique_ptr<NetSession> Load(const std::string &dir, const std::string &name, const OrtConfig &config) {
    std::vector<char> bytes = ReadFile(dir + "/" + name);
    if (bytes.empty()) throw std::runtime_error("Cannot read model " + name);
    return std::unique_ptr<NetSession>(new NetSession(bytes.data(), bytes.size(), config));
}

static double NowMs() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static double Median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v.empty() ? 0 : v[v.size() / 2];
}

static int Golden(const std::string &dir, const std::string &list, const std::string &out_path, LandmarkMode mode, int threads) {
    OrtConfig config;
    config.threads = threads;
    PipelineV1 pipeline(Load(dir, "face_landmarker_pts5_net1.onnx", config), Load(dir, "face_landmarker_pts5_net2.onnx", config),
                        Load(dir, "face_recognizer.onnx", config), Load(dir, "faceboxesv2-640x640.onnx", config));
    std::ifstream in(list);
    FILE *out = std::fopen(out_path.c_str(), "w");
    if (!in || !out) {
        std::cerr << "cannot open list or output\n";
        return 2;
    }
    std::string path;
    int images = 0, faces = 0;
    while (std::getline(in, path)) {
        if (path.empty()) continue;
        cv::Mat img = cv::imread(path, cv::IMREAD_COLOR);
        std::fprintf(out, "IMG %s\n", path.c_str());
        ++images;
        if (img.empty()) {
            std::fprintf(out, "ERR unreadable\n");
            continue;
        }
        ExtractResult r = pipeline.FromImage(img, mode);
        if (r.feature.empty()) {
            std::fprintf(out, "NOFACE\n");
            continue;
        }
        const DetectedBox &b = r.box;
        std::fprintf(out, "BOX %.9g %.9g %.9g %.9g %.9g\n", b.x1, b.y1, b.x2, b.y2, b.score);
        std::fprintf(out, "LM");
        for (const auto &p : r.landmarks) std::fprintf(out, " %.17g %.17g", p.x, p.y);
        std::fprintf(out, "\n");
        cv::Mat aligned = AlignSeeta256(img, r.landmarks);
        double checksum = cv::sum(aligned)[0] + cv::sum(aligned)[1] + cv::sum(aligned)[2];
        std::fprintf(out, "ALIGNSUM %.0f\n", checksum);
        const std::vector<float> &feature = r.feature;
        std::fprintf(out, "FEAT");
        for (float v : feature) std::fprintf(out, " %.9g", v);
        std::fprintf(out, "\n");
        ++faces;
    }
    std::fclose(out);
    std::cout << images << " images, " << faces << " features\n";
    return 0;
}

static int Bench(const std::string &dir, const std::string &image, int iterations, int threads, bool xnnpack) {
    OrtConfig config;
    config.threads = threads;
    config.xnnpack = xnnpack;
    cv::Mat img = cv::imread(image, cv::IMREAD_COLOR);
    if (img.empty()) {
        std::cerr << "cannot read image\n";
        return 2;
    }
    FaceBoxes detector(Load(dir, "faceboxesv2-640x640.onnx", config));
    Landmarker5 landmarker(Load(dir, "face_landmarker_pts5_net1.onnx", config), Load(dir, "face_landmarker_pts5_net2.onnx", config));
    auto recognizer_model = Load(dir, "face_recognizer.onnx", config);
    const bool xnn_used = recognizer_model->UsingXnnpack();
    Recognizer1024 recognizer(std::move(recognizer_model));
    auto rotation = Load(dir, "model_gray_mobilenetv2_rotcls.onnx", config);
    auto fsa_var = Load(dir, "fsanet-var.onnx", config);
    auto fsa_conv = Load(dir, "fsanet-conv.onnx", config);
    std::vector<float> rot_in(3 * 224 * 224, 0.5f), fsa_in(3 * 64 * 64, 0.1f);

    auto boxes = detector.Detect(img);
    if (boxes.empty()) {
        std::cerr << "no face in bench image\n";
        return 3;
    }
    const DetectedBox b = boxes[0];
    Landmarks5 points = landmarker.Detect(img, b.x1, b.y1, b.x2, b.y2);
    cv::Mat aligned = AlignSeeta256(img, points);

    struct Stage { const char *name; std::function<void()> run; std::vector<double> ms; };
    std::vector<Stage> stages = {
        {"rotation_cls(old only)", [&] { rotation->Run(rot_in.data(), {1, 3, 224, 224}); }, {}},
        {"faceboxes_640", [&] { detector.Detect(img); }, {}},
        {"landmarks_2stage", [&] { landmarker.Detect(img, b.x1, b.y1, b.x2, b.y2); }, {}},
        {"landmarks_tta5(new)", [&] { for (int k = 0; k < 5; ++k) landmarker.Detect(img, b.x1 + k, b.y1, b.x2 + k, b.y2, true); }, {}},
        {"fsanet_x2(old only)", [&] { fsa_var->Run(fsa_in.data(), {1, 3, 64, 64}); fsa_conv->Run(fsa_in.data(), {1, 3, 64, 64}); }, {}},
        {"align_256", [&] { AlignSeeta256(img, points); }, {}},
        {"recognizer_248", [&] { recognizer.Extract(aligned); }, {}},
    };
    for (auto &s : stages) s.run();  // warm-up
    for (int it = 0; it < iterations; ++it)
        for (auto &s : stages) {
            double t = NowMs();
            s.run();
            s.ms.push_back(NowMs() - t);
        }
    std::printf("image %dx%d threads=%d xnnpack=%s\n", img.cols, img.rows, threads, xnn_used ? "on" : "off");
    double old_total = 0, new_total = 0;
    for (auto &s : stages) {
        double m = Median(s.ms);
        std::printf("  %-24s median %8.2f ms\n", s.name, m);
        old_total += m;
        if (std::strstr(s.name, "old only") == nullptr && std::strstr(s.name, "(new)") == nullptr &&
            std::strcmp(s.name, "faceboxes_640") != 0) new_total += m;
        if (std::strstr(s.name, "(new)") != nullptr) old_total -= m;  // not part of the old pipeline
    }
    std::printf("  old pipeline (all stages)          %8.2f ms\n", old_total);
    std::printf("  tracked-face path (no rot/det/pose) %8.2f ms\n", new_total);
    return 0;
}

// InsightFace still-image path on every listed image; same output format as Golden.
static int GoldenInsightFace(const std::string &det, const std::string &rec, const std::string &list,
                             const std::string &out_path, int threads) {
    OrtConfig config;
    config.threads = threads;
    auto load = [&](const std::string &path) {
        std::vector<char> bytes = ReadFile(path);
        if (bytes.empty()) throw std::runtime_error("cannot read " + path);
        return std::unique_ptr<NetSession>(new NetSession(bytes.data(), bytes.size(), config));
    };
    PipelineInsightFace pipeline(load(det), load(rec));
    std::ifstream in(list);
    FILE *out = std::fopen(out_path.c_str(), "w");
    if (!in || !out) return 2;
    std::string path;
    int faces = 0;
    while (std::getline(in, path)) {
        if (path.empty()) continue;
        std::fprintf(out, "IMG %s\n", path.c_str());
        cv::Mat img = cv::imread(path, cv::IMREAD_COLOR);
        ExtractResult r = img.empty() ? ExtractResult{} : pipeline.FromImage(img, LandmarkMode::kTruncated);
        if (r.feature.empty()) { std::fprintf(out, "NOFACE\n"); continue; }
        std::fprintf(out, "BOX %.9g %.9g %.9g %.9g %.9g\nLM", r.box.x1, r.box.y1, r.box.x2, r.box.y2, r.box.score);
        for (const auto &p : r.landmarks) std::fprintf(out, " %.9g %.9g", p.x, p.y);
        std::fprintf(out, "\nFEAT");
        for (float v : r.feature) std::fprintf(out, " %.9g", v);
        std::fprintf(out, "\n");
        ++faces;
    }
    std::fclose(out);
    std::cout << faces << " features\n";
    return 0;
}

// Median latency of one model on a random input of the given NCHW shape.
static int BenchModel(const std::string &path, int n, int c, int h, int w, int iterations, int threads) {
    OrtConfig config;
    config.threads = threads;
    std::vector<char> bytes = ReadFile(path);
    if (bytes.empty()) throw std::runtime_error("cannot read " + path);
    NetSession model(bytes.data(), bytes.size(), config);
    std::vector<float> input(static_cast<size_t>(n) * c * h * w);
    for (size_t i = 0; i < input.size(); ++i) input[i] = static_cast<float>((i * 2654435761u) % 255) / 128.f - 1.f;
    model.Run(input.data(), {n, c, h, w});
    std::vector<double> ms;
    for (int i = 0; i < iterations; ++i) {
        double t = NowMs();
        model.Run(input.data(), {n, c, h, w});
        ms.push_back(NowMs() - t);
    }
    std::sort(ms.begin(), ms.end());
    std::printf("%s %dx%dx%dx%d threads=%d median %.2f ms p90 %.2f ms\n", path.c_str(), n, c, h, w, threads,
                ms[ms.size() / 2], ms[ms.size() * 9 / 10]);
    return 0;
}

int main(int argc, char **argv) {
    try {
        if (argc >= 6 && std::string(argv[1]) == "golden_if")
            return GoldenInsightFace(argv[2], argv[3], argv[4], argv[5], argc > 6 ? std::atoi(argv[6]) : 4);
        if (argc >= 9 && std::string(argv[1]) == "benchmodel")
            return BenchModel(argv[2], std::atoi(argv[3]), std::atoi(argv[4]), std::atoi(argv[5]), std::atoi(argv[6]),
                              std::atoi(argv[7]), std::atoi(argv[8]));
        if (argc >= 5 && std::string(argv[1]) == "golden")
            return Golden(argv[2], argv[3], argv[4],
                          argc <= 5 ? LandmarkMode::kTruncated : std::string(argv[5]) == "subpixel" ? LandmarkMode::kSubpixel
                          : std::string(argv[5]) == "tta" ? LandmarkMode::kTta : LandmarkMode::kTruncated,
                          argc > 6 ? std::atoi(argv[6]) : 4);
        if (argc >= 6 && std::string(argv[1]) == "bench")
            return Bench(argv[2], argv[3], std::atoi(argv[4]), std::atoi(argv[5]), argc > 6 && std::string(argv[6]) == "xnnpack");
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
    std::cerr << "usage: face_engine_cli golden <model_dir> <list> <out> [exact|subpixel|tta] [threads]\n"
                 "       face_engine_cli bench <model_dir> <image> <iterations> <threads> [xnnpack]\n";
    return 64;
}
