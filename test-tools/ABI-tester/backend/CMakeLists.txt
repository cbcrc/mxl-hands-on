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

find_package(PkgConfig REQUIRED)
pkg_check_modules(HTTPLIB REQUIRED IMPORTED_TARGET cpp-httplib)
find_package(nlohmann_json REQUIRED)

add_executable(abi-tester src/main.cpp src/domains.cpp src/registry.cpp src/calls.cpp)

# -Werror=return-type only: falling off the end of a non-void function is undefined
# behaviour, not an error the standard requires the compiler to reject, so GCC warns
# and links anyway. A blanket -Werror would also stop the build on the harmless
# unused-function notices a helper produces before its caller is typed.
target_compile_options(abi-tester PRIVATE -Wall -Wextra -Werror=return-type)

target_include_directories(abi-tester PRIVATE "${MXL_ROOT}/lib/include")
target_link_directories(abi-tester PRIVATE "${MXL_LIB_DIR}")
target_link_libraries(abi-tester PRIVATE mxl PkgConfig::HTTPLIB nlohmann_json::nlohmann_json)

# Bake the library location into the binary so it runs without LD_LIBRARY_PATH.
set_target_properties(abi-tester PROPERTIES BUILD_RPATH "${MXL_LIB_DIR}")