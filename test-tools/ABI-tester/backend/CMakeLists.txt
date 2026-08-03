# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.16)
project(abi-tester CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Root of the MXL SDK checkout. Override with -DMXL_ROOT=/some/path
set(MXL_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../dmf-mxl"
    CACHE PATH "Root of the MXL SDK source tree")

set(MXL_LIB_DIR "${MXL_ROOT}/build/Linux-Clang-Release/lib")

add_executable(abi-tester src/main.cpp)

target_compile_options(abi-tester PRIVATE -Wall -Wextra)

target_include_directories(abi-tester PRIVATE "${MXL_ROOT}/lib/include")
target_link_directories(abi-tester PRIVATE "${MXL_LIB_DIR}")
target_link_libraries(abi-tester PRIVATE mxl)

# Bake the library location into the binary so it runs without LD_LIBRARY_PATH.
set_target_properties(abi-tester PROPERTIES BUILD_RPATH "${MXL_LIB_DIR}")