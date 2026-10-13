// ONNX Runtime session wrapper shared by every model in the engine.
#ifndef FACE_ENGINE_ORT_MODEL_H
#define FACE_ENGINE_ORT_MODEL_H

#include <onnxruntime/onnxruntime_cxx_api.h>
#include <memory>
#include <string>
#include <vector>

namespace face {

struct OrtConfig {
    int threads = 2;
    /** XNNPACK execution provider; falls back to the default CPU provider when unavailable. */
    bool xnnpack = false;
    /**
     * Let idle pool threads spin. Off by default: in the app the analysis thread (detection,
     * liveness) and the feature worker share four big cores, and spinning threads starve each other.
     */
    bool spin = false;
};

/** One model loaded from memory. Not thread safe: use one instance per thread. */
class NetSession {
public:
    NetSession(const void *data, size_t size, const OrtConfig &config);

    /** Runs the model's single input and returns every output as a float vector. */
    std::vector<std::vector<float>> Run(const float *input, const std::vector<int64_t> &shape);

    /** Shape of output {@code index} from the last Run. */
    const std::vector<int64_t> &OutputShape(size_t index) const { return output_shapes_[index]; }

    bool UsingXnnpack() const { return xnnpack_; }

private:
    std::unique_ptr<Ort::Session> session_;
    std::string input_name_;
    std::vector<std::string> output_names_;
    std::vector<std::vector<int64_t>> output_shapes_;
    bool xnnpack_ = false;
};

/** Reads a whole file; empty on failure. */
std::vector<char> ReadFile(const std::string &path);

}  // namespace face

#endif  // FACE_ENGINE_ORT_MODEL_H
