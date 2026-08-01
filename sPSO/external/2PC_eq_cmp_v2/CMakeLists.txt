cmake_minimum_required(VERSION 3.15)
project(2PC_eq_cmp C CXX)

set(COMMON_FLAGS "-Wall -Wfatal-errors -Wno-unused-variable -fcoroutines -std=c++20 -fopenmp")
SET(CMAKE_CXX_FLAGS_RELEASE "-O3  -DNDEBUG")
SET(CMAKE_CXX_FLAGS_RELWITHDEBINFO " -O2 -g -ggdb")
SET(CMAKE_CXX_FLAGS_DEBUG  "-O0 -g -ggdb")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ")

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${COMMON_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${COMMON_FLAGS}")

if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
    SET(CMAKE_BUILD_TYPE Release)
endif()

set(CMAKE_PREFIX_PATH "${CMAKE_SOURCE_DIR}/external/install/")
#set(NO_SYSTEM_PATH true)
#set(NO_CMAKE_SYSTEM_PATH true)
find_package(libOTe REQUIRED)

aux_source_directory(. DIR_SRCS)
add_executable(eq_cmp ${DIR_SRCS})

target_link_libraries(eq_cmp oc::libOTe)

