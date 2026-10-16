#include "ort_model.h"

#include "../include/onnxruntime/nnapi_provider_factory.h"

#include <fstream>
#include <stdexcept>

namespace face {

static Ort::Env &Environment() {
    static Ort::Env env(ORT_LOGGING_LEVEL_ERROR, "face-engine");
    return env;
}

NetSession::NetSession(const void *data, size_t size, const OrtConfig &config) {
    Ort::SessionOptions options;
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
    options.SetIntraOpNumThreads(config.threads);
    options.SetInterOpNumThreads(1);
    if (!config.spin) options.AddConfigEntry("session.intra_op.allow_spinning", "0");
    if (!config.profile_prefix.empty()) {
#ifdef _WIN32
        std::wstring prefix(config.profile_prefix.begin(), config.profile_prefix.end());
        options.EnableProfiling(prefix.c_str());
#else
        options.EnableProfiling(config.profile_prefix.c_str());
#endif
    }
    if (config.xnnpack) {
        try {
            // XNNPACK runs its own pool; ORT's pool then only handles the few unsupported nodes.
            options.AppendExecutionProvider("XNNPACK", {{"intra_op_num_threads", std::to_string(config.threads)}});
            options.SetIntraOpNumThreads(1);
            options.AddConfigEntry("session.intra_op.allow_spinning", "0");
            xnnpack_ = true;
        } catch (const Ort::Exception &) {
            xnnpack_ = false;  // provider not compiled into this ORT build
        }
    }
#ifdef __ANDROID__
    if (config.nnapi_flags >= 0)
        Ort::ThrowOnError(OrtSessionOptionsAppendExecutionProvider_Nnapi(options, static_cast<uint32_t>(config.nnapi_flags)));
#endif
    session_.reset(new Ort::Session(Environment(), data, size, options));
    Ort::AllocatorWithDefaultOptions allocator;
    input_name_ = session_->GetInputNameAllocated(0, allocator).get();
    for (size_t i = 0; i < session_->GetOutputCount(); ++i)
        output_names_.push_back(session_->GetOutputNameAllocated(i, allocator).get());
    output_shapes_.resize(output_names_.size());
}

std::vector<std::vector<float>> NetSession::Run(const float *input, const std::vector<int64_t> &shape) {
    size_t count = 1;
    for (int64_t d : shape) count *= static_cast<size_t>(d);
    Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    Ort::Value tensor = Ort::Value::CreateTensor<float>(memory, const_cast<float *>(input), count,
                                                        shape.data(), shape.size());
    const char *input_name = input_name_.c_str();
    std::vector<const char *> output_names;
    for (const auto &name : output_names_) output_names.push_back(name.c_str());
    std::vector<Ort::Value> outputs = session_->Run(Ort::RunOptions{nullptr}, &input_name, &tensor, 1,
                                                    output_names.data(), output_names.size());
    std::vector<std::vector<float>> result(outputs.size());
    for (size_t i = 0; i < outputs.size(); ++i) {
        auto info = outputs[i].GetTensorTypeAndShapeInfo();
        output_shapes_[i] = info.GetShape();
        const float *values = outputs[i].GetTensorData<float>();
        result[i].assign(values, values + info.GetElementCount());
    }
    return result;
}

std::string NetSession::EndProfiling() {
    Ort::AllocatorWithDefaultOptions allocator;
    auto path = session_->EndProfilingAllocated(allocator);
    return path ? std::string(path.get()) : std::string();
}

std::vector<char> ReadFile(const std::string &path) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) return {};
    std::streamsize size = in.tellg();
    std::vector<char> data(static_cast<size_t>(size));
    in.seekg(0);
    if (!in.read(data.data(), size)) return {};
    return data;
}

}  // namespace face
