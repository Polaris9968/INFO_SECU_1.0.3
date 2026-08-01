## Introduction

This project provides an experimental framework for evaluating leakage-free private set operations (PSO). The goal of the repository is to benchmark the performance of our protocols and implementation techniques, rather than to serve as a production-ready library. Users are encouraged to review the implementation carefully before integrating any components outside benchmarking or academic use.

## Dependences -- Explanation

Our system is built on top of several cryptographic and parallel-computing libraries.
 - [secure-join](https://github.com/ladnir/secure-join.git) provides the implementation of permutation correlation generator (PermCG).
 - [libOTe](https://github.com/osu-crypto/libOTe.git) provide core primitives used in two-party computation and oblivious transfer.
 - [2PC_eq_cmp](https://zenodo.org/records/17217396) provides equality and comparison protocols used within our PSO workflow.
 - [Blake3](https://github.com/BLAKE3-team/BLAKE3.git) is included as a fast, modern hash function.
 - [OpenMP](https://www.openmp.org) enables configurable multi-threading to improve performance on multi-core CPUs.
These dependencies must be compiled and installed before building our own code, as they supply most of the foundational cryptographic operations used throughout the framework.

## Code Structure
The repository is organized to clearly separate our own implementation from external dependencies and build artifacts.
 - The `pbsspmt` directory contains all source files, headers, and unit tests for our protocol and benchmarking framework.
 - The `external` directory holds third-party libraries along with an `install` subfolder that acts as a custom installation prefix for all dependencies.
 - The `build` directory is where CMake generates configuration files and where compilation outputs are stored.
```bash
- README.md
- CMakeLists.txt
- pbsspmt           // our code
  |-- src           // source code
  |-- include       // headers
  |-- test          // test code
- external          // external lib
  |-- install       // external lib install path
  |-- blake3        // blake3 (submodule)
  |-- libOTe        // libOTe (submodule)
  |-- secure-join   // secure-join (submodule)
  |-- 2PC_eq_cmp_v2 // 2PC equality/comparison protocols
- build             // build directory
```

## Build

Before building our framework, all external dependencies must be configured and installed correctly. Most dependencies are managed as git submodules. The instructions in this section explain how to initialize them and compile each library with the required options. After the dependencies are installed into `external/install`, you can build our code using CMake as usual.

```bash
mkdir -p external/install
```

### Getting the source code (submodules)

libOTe, Blake3, and secure-join are managed as git submodules. After cloning this repository, initialize and update them:

```bash
git submodule update --init --recursive
```

> **Note for libOTe**: Our code requires a specific commit of libOTe (`657f6da90bff5774a2d01c824e997572d5e8ba00`) for compatibility with secure-join. After `git submodule update --init`, enter the libOTe directory and check out the required version:
> ```bash
> cd external/libOTe
> git fetch origin
> git checkout 657f6da90bff5774a2d01c824e997572d5e8ba00
> cd ../..
> ```

### OpenMP

[OpenMP](https://www.openmp.org) is used to parallelize computationally expensive tasks in our implementation. You may need to install or enable OpenMP support depending on your compiler and operating system. The official OpenMP documentation provides detailed installation steps. Once installed, the number of threads can be controlled dynamically via the `OMP_NUM_THREADS` environment variable during testing.

### libOTe

We rely heavily on `libOTe` for oblivious transfer operations and other low-level cryptographic primitives. Because `secure-join` requires compatibility with a specific commit of `libOTe`, we explicitly instruct users to check out the required version (see the note above). The build script `build.py` simplifies the compilation process by enabling all necessary modules, including **circuits**, **AES**, **SSE**, **silent VOLE**, and **Boost** support. Finally, `libOTe` is installed into the unified `external/install` directory to make integration straightforward.

1. Build libOTe
```bash
cd external/libOTe
python build.py -DENABLE_CIRCUITS=ON -DENABLE_BOOST=ON -DENABLE_SODIUM=ON -DENABLE_SSE=ON -DENABLE_AES=ON -DCOPROTO_ENABLE_BOOST=ON -DENABLE_ALL_OT=ON -DENABLE_SILENT_VOLE=ON -DCMAKE_CXX_FLAGS="-march=native"
```

2. Install libOTe
Install `libOTe` to `external/install`
```bash
cd out/build/linux
cmake --install . --prefix $(pwd)/../../../../install
```

### secure-join

secure-join provides efficient permutation correlation generator (PermCG) construction blocks that we integrate into our benchmarking environment. After initializing the submodule, you must compile it with **Boost** and the required **sodium** configuration flags.

1. Build
```bash
cd external/secure-join
python build.py -D SODIUM_MONTGOMERY=false -D COPROTO_ENABLE_BOOST=ON
```

2. Install
```bash
cd out/build/linux
cmake --install . --prefix $(pwd)/../../../../install
```

### 2PC_eq_cmp_v2

The `2PC_eq_cmp_v2` directory contains our modified version of the two-party secure equality test and comparison protocols. It provides the `eq2<T>` and `cmp1<T>` template classes used in the PSO workflow.

This library depends on libOTe (already built and installed in `external/install` as described above). The `CMAKE_PREFIX_PATH` in its `CMakeLists.txt` is pre-configured to look for libOTe under `${CMAKE_SOURCE_DIR}/external/install/`.

**Build standalone:**

```bash
cd external/2PC_eq_cmp_v2
mkdir build && cd build
cmake ..
cmake --build . --parallel
```

**Run standalone equality test (two terminals):**

```bash
# Terminal 1 (sender)
./build/eq_cmp -sender -n 100 -l 4 -c 0

# Terminal 2 (receiver)
./build/eq_cmp -receiver -n 100 -l 4 -c 0
```

**Run standalone comparison test:**

```bash
# Terminal 1 (sender)
./build/eq_cmp -sender -n 100 -l 4 -c 1

# Terminal 2 (receiver)
./build/eq_cmp -receiver -n 100 -l 4 -c 1
```

**Command-line parameters:**

| Flag | Description |
|------|-------------|
| `-sender` | Run as sender |
| `-receiver` | Run as receiver |
| `-n <num>` | Number of elements to compare |
| `-l <bits>` | Bit-length of each element |
| `-c <type>` | `0` = equality test, `1` = comparison |
| `-ip <addr>` | IP address (default: `localhost:1213`) |

> **Note**: In the main PSO workflow (`pbsspmt/test/test_flow.cpp`), the `eq2` protocol from `neweq.h` is included directly rather than linked as a separate library. The standalone build above is useful for testing the equality/comparison protocols independently.

### Our code

Once all dependencies are prepared, compiling our code follows the standard CMake workflow. The build process automatically detects the external libraries installed in `external/install`. After running `cmake` and `make`, the resulting binaries include both the protocol implementations and the benchmark tools used to evaluate them.

```bash
mkdir build && cd build
cmake ..
make -j
```

## Test

The project includes unit tests and benchmarking tests located in the test directory. You can run all tests using CTest or execute specific tests by name. OpenMP thread counts can be set at runtime to evaluate how parallelization affects performance. This allows users to perform reproducible measurements under different computational settings.

```bash
# all test
ctest -V

# OpenMP threads number = 4, run the specific test `workflow_test`
OMP_NUM_THREADS=4 ctest -R "workflow_test" -V
```

## LICENSE
This project is licensed under the **Apache License 2.0**.
You may use, distribute, and modify this software in accordance with the terms of the license.

For more details, please refer to the full license text in the LICENSE file included in this repository, or visit:
```arduino
http://www.apache.org/licenses/LICENSE-2.0
```
