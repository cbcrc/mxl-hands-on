# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.16)
project(abi-tester CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Root of the MXL SDK checkout. Override with -DMXL_ROOT=/some/path
set(MXL_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../dmf-mxl"
    CACHE PATH "Root of the MXL SDK source tree")

# The dmf-mxl build directory is named after the CMake preset that produced it,
# so it differs between the Linux dev box and macOS. Override with -DMXL_PRESET=...
if(APPLE)
    set(MXL_PRESET "Darwin-Clang-Release" CACHE STRING "dmf-mxl build preset directory")
else()
    set(MXL_PRESET "Linux-Clang-Release" CACHE STRING "dmf-mxl build preset directory")
endif()

set(MXL_LIB_DIR "${MXL_ROOT}/build/${MXL_PRESET}/lib")

# Debian's libcpp-httplib-dev ships a pkg-config file and no CMake package;
# Homebrew's cpp-httplib ships a CMake package and no pkg-config file.
find_package(httplib CONFIG QUIET)
if(httplib_FOUND)
    set(HTTPLIB_TARGET httplib::httplib)
else()
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(HTTPLIB REQUIRED IMPORTED_TARGET cpp-httplib)
    set(HTTPLIB_TARGET PkgConfig::HTTPLIB)
endif()

find_package(nlohmann_json REQUIRED)

add_executable(abi-tester src/main.cpp src/domains.cpp src/registry.cpp src/calls.cpp src/engine.cpp src/scenario.cpp)

# -Werror=return-type only: falling off the end of a non-void function is undefined
# behaviour, not an error the standard requires the compiler to reject, so GCC warns
# and links anyway. A blanket -Werror would also stop the build on the harmless
# unused-function notices a helper produces before its caller is typed.
target_compile_options(abi-tester PRIVATE -Wall -Wextra -Werror=return-type)

target_include_directories(abi-tester PRIVATE "${MXL_ROOT}/lib/include")
target_link_directories(abi-tester PRIVATE "${MXL_LIB_DIR}")
target_link_libraries(abi-tester PRIVATE mxl ${HTTPLIB_TARGET} nlohmann_json::nlohmann_json)

# Bake the library location into the binary so it runs without LD_LIBRARY_PATH.
set_target_properties(abi-tester PROPERTIES BUILD_RPATH "${MXL_LIB_DIR}")