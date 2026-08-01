cmake_minimum_required(VERSION 3.15)
project(sPSO C CXX)

enable_testing()

# ===== Compiler Settings =====
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Common flags
set(COMMON_FLAGS "-Wall -Wfatal-errors -Wno-unused-variable -fopenmp -mavx2 -mpclmul -msse2 -O3")

set(COMMON_LDFLAGS "-Wl,--allow-multiple-definition")

set(CMAKE_C_FLAGS   "${CMAKE_C_FLAGS} ${COMMON_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${COMMON_FLAGS} -fcoroutines")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} ${COMMON_LDFLAGS}")
set(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} ${COMMON_LDFLAGS}")

set(THIRDPARTY_INSTALL_DIR "${CMAKE_SOURCE_DIR}/external/install")
list(APPEND CMAKE_PREFIX_PATH "${THIRDPARTY_INSTALL_DIR}")

find_package(libOTe REQUIRED)
find_package(cryptoTools REQUIRED)
find_package(coproto REQUIRED)
find_package(secureJoin REQUIRED)

# Disable auto fetching libOTe for secure-join
set(FETCH_LIBOTE OFF CACHE BOOL "Disable auto fetching libOTe")
set(SECUREJOIN_THIRDPARTY_DIR "${THIRDPARTY_INSTALL_DIR}")
set(SECUREJOIN_THIRDPARTY_CLONE_DIR "${THIRDPARTY_INSTALL_DIR}")

# --- Add BLAKE3 ---
add_library(blake3 STATIC
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_dispatch.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_portable.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_sse2.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_sse41.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_avx2.c
    ${CMAKE_SOURCE_DIR}/external/blake3/c/blake3_avx512.c
)
# optional: enable SIMD (auto-detected)
if (CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(blake3 PRIVATE -msse2 -mavx2 -mavx512f -mavx512vl)
endif()

target_include_directories(blake3 PUBLIC
    ${CMAKE_SOURCE_DIR}/external/blake3/c
)

# our pbssPMT library
add_subdirectory(pbsspmt)