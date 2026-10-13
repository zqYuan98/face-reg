# Device-side test harness for the native engine; not part of the app build.
# Configure with the NDK toolchain, see tools/engine-cli/build-and-run.sh.
cmake_minimum_required(VERSION 3.18)
project(face_engine_cli CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_BUILD_TYPE Release)

set(CPP_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(LIB_DIR ${CPP_ROOT}/../jniLibs/${ANDROID_ABI})

add_executable(face_engine_cli main.cpp ../ort_model.cpp ../v1_models.cpp ../pipeline_v1.cpp ../image_ops.cpp)
target_include_directories(face_engine_cli PRIVATE ${CPP_ROOT}/include)
# Bit-for-bit parity with NumPy needs unfused float arithmetic.
target_compile_options(face_engine_cli PRIVATE -ffp-contract=off -O2)
target_link_libraries(face_engine_cli
        ${LIB_DIR}/libonnxruntime.so
        ${LIB_DIR}/libopencv_imgcodecs.so
        ${LIB_DIR}/libopencv_imgproc.so
        ${LIB_DIR}/libopencv_core.so
        log)
