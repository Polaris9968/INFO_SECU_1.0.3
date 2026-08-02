#!/bin/bash
# build_spso_runner.sh — 重编 spso_runner 二进制 (INFO_SECU_1.0.3 后端 5 协议依赖)
#
# 2026-08-02 重建:二进制在 8-1 中午目录清理时丢失,此脚本固化编译命令防止再丢
#
# 用法: bash build_spso_runner.sh [--clean]
# 内存注意: 单文件 O3 编译峰值约 1.5G RSS, 3.4G 内存机器务必串行执行,
#           不要和 node/其他编译任务并行!
set -euo pipefail

SPSO_MAIN=/root/projects/sPSO           # 主开发副本(库都编在这)
SPSO_EXT=$SPSO_MAIN/external/install    # 外部依赖 install 前缀
SPSO_BLD=$SPSO_MAIN/build               # 主副本 build 目录
HERE=$(cd "$(dirname "$0")" && pwd)     # /root/projects/INFO_SECU_1.0.3/spso_python
OUT=$HERE/spso_runner

echo "==> [1/3] 校验库文件存在"
for lib in \
  "$SPSO_BLD/pbsspmt/libpbsspmt.a" \
  "$SPSO_BLD/libblake3.a" \
  "$SPSO_EXT/lib/libsecureJoin.a" \
  "$SPSO_EXT/lib/libmacoro.a" \
  "$SPSO_EXT/lib/liblibOTe.a" \
  "$SPSO_EXT/lib/libcoproto.a" \
  "$SPSO_EXT/lib/libcryptoTools.a" \
  "$SPSO_EXT/lib/libSimplestOT.a" \
  "$SPSO_EXT/lib/libKyberOT.a"; do
  [ -f "$lib" ] || { echo "缺少库: $lib"; exit 1; }
done
echo "    全部库就位 ✓"

echo "==> [2/3] 校验 common.cpp 含 SPIKE 2 参数 (sender_set_in)"
if ! grep -q "sender_set_in" /root/projects/INFO_SECU_1.0.3/sPSO/pbsspmt/test/common.cpp; then
  echo "    ❌ common.cpp 缺 sender_set_in(被回退成 5 参老版本)!"
  echo "       恢复: git show 33eb80c:sPSO/pbsspmt/test/common.{h,cpp} > 对应文件"
  exit 1
fi
echo "    参数就位 ✓"

echo "==> [3/3] 编译(串行,约 5-15 分钟)"
time g++ -std=c++20 -fcoroutines -O3 -fopenmp -fPIC \
    -maes -mavx2 -mpclmul -msse2 -msse3 -msse4.1 \
    -I"$SPSO_MAIN/pbsspmt/include" \
    -I"$SPSO_MAIN/pbsspmt/test" \
    -I"$SPSO_MAIN/external/2PC_eq_cmp_v2" \
    -I"$SPSO_EXT/include" \
    -I"$SPSO_EXT/include/secureJoin" \
    -I"$SPSO_MAIN/external/blake3/c" \
    "$HERE/spso_runner.cpp" \
    "$SPSO_BLD/pbsspmt/libpbsspmt.a" \
    "$SPSO_EXT/lib/libsecureJoin.a" \
    "$SPSO_EXT/lib/libmacoro.a" \
    "$SPSO_EXT/lib/liblibOTe.a" \
    "$SPSO_EXT/lib/libcoproto.a" \
    "$SPSO_EXT/lib/libcryptoTools.a" \
    "$SPSO_EXT/lib/libSimplestOT.a" \
    "$SPSO_EXT/lib/libKyberOT.a" \
    "$SPSO_BLD/libblake3.a" \
    -lpthread -ldl -lsodium -no-pie \
    -o "$OUT"

echo "==> 完成: $(ls -lh "$OUT" | awk '{print $5}') $OUT"
