// galois128.cpp
// Non-yacl implementation of GF(2^128) operations.
// - Uses oc::block (cryptoTools) as the 128-bit SIMD block type.
// - Provides PCLMUL (intrinsics) implementation on x86_64 when available.
// - Provides pure C++ fallback using unsigned __int128.

#include "galois128.h"

#include <array>
#include <cassert>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <type_traits>

#include "cryptoTools/Common/block.h" // osuCrypto::block

#ifdef __x86_64__
#include <emmintrin.h>
#include <wmmintrin.h>
#include <immintrin.h>
#endif

namespace okvs {

// Helper typedefs (already in header, repeated for cpp clarity)
using osuCrypto::block;
using uint128_t = unsigned __int128;
using int128_t = __int128;

// ----------------------- utility conversions -----------------------------

// Convert oc::block to uint128_t by memcpy (preserve raw bytes)
static uint128_t block_to_uint128(const block &b) {
    uint128_t out = 0;
    static_assert(sizeof(out) == 16, "uint128_t must be 16 bytes");
    std::memcpy(&out, &b, 16);
    return out;
}

// Convert uint128_t to oc::block by memcpy
static block uint128_to_block(uint128_t v) {
    block b{};
    std::memcpy(&b, &v, 16);
    return b;
}

// Convert variant value to oc::block (already in header but redefined for cpp)
static block variant_to_block(const Galois128Type &v) {
    if (std::holds_alternative<block>(v)) {
        return std::get<block>(v);
    } else {
        return uint128_to_block(std::get<uint128_t>(v));
    }
}

// Convert variant value to uint128_t
static uint128_t variant_to_uint128(const Galois128Type &v) {
    if (std::holds_alternative<uint128_t>(v)) {
        return std::get<uint128_t>(v);
    } else {
        return block_to_uint128(std::get<block>(v));
    }
}

// ----------------------- pure C++ GF(2^128) multiplication -----------------
//
// Implements the "shift-and-xor" multiplication in GF(2^128) with
// the reduction polynomial x^127 + x^7 + x^2 + x + 1 (GCM standard).
//
// Reference: GCM specification Algorithm 1 (Multiplication in GF(2^128))
//
uint128_t cc_gf128Mul(const uint128_t a, const uint128_t b) {
    uint128_t z = (uint128_t)0;
    uint128_t v = a;
    uint128_t bb = b;

    const uint128_t mask1 = (uint128_t)1;
    const uint128_t mask127 = ((uint128_t)1 << 127);
    const uint128_t r = (uint128_t)0x87; // reduction poly low byte

    for (int i = 0; i < 128; ++i) {
        if ((bb & mask1) != 0) {
            z ^= v;
        }
        // shift v left by 1
        bool carry = (v & mask127) != 0;
        v <<= 1;
        if (carry) v ^= r;
        bb >>= 1;
    }

    return z;
}

// ----------------------- x86 PCLMUL implementation ------------------------
#ifdef __x86_64__

// Runtime detection for pclmul support.
// Use builtin if available; otherwise conservative false.
static bool has_pclmul_runtime() {
#if defined(__GNUC__) || defined(__clang__)
    // __builtin_cpu_supports exists on GCC/Clang for x86 targets
    // It checks CPUID at runtime.
    return __builtin_cpu_supports("pclmul");
#else
    return false;
#endif
}

bool hasPCLML() { return has_pclmul_runtime(); }

// Compute 128x128 -> 256-bit product split into two 128-bit lanes.
// lo = lower 128 bits, hi = upper 128 bits.
static inline void mm_gf128Mul_impl(const __m128i &x, const __m128i &y, __m128i &lo,
                                    __m128i &hi) {
    __m128i t1 = _mm_clmulepi64_si128(x, y, 0x00);
    __m128i t2 = _mm_clmulepi64_si128(x, y, 0x10);
    __m128i t3 = _mm_clmulepi64_si128(x, y, 0x01);
    __m128i t4 = _mm_clmulepi64_si128(x, y, 0x11);

    __m128i t_middle = _mm_xor_si128(t2, t3);
    lo = _mm_xor_si128(t1, _mm_slli_si128(t_middle, 8));
    hi = _mm_xor_si128(t4, _mm_srli_si128(t_middle, 8));
}

// Reduction from 256-bit (hi:lo) to 128-bit with polynomial 0x87
static inline __m128i mm_gf128Reduce_impl(__m128i lo, __m128i hi) {
    // modulus of size 64 bits (low byte is 0x87)
    const uint64_t mod64 = 0x87;
    __m128i modulus = _mm_cvtsi64_si128((long long)mod64);

    // reduce with respect to high half
    __m128i tmp = _mm_clmulepi64_si128(hi, modulus, 0x01);
    lo = _mm_xor_si128(lo, _mm_slli_si128(tmp, 8));
    hi = _mm_xor_si128(hi, _mm_srli_si128(tmp, 8));

    // reduce with respect to low half
    tmp = _mm_clmulepi64_si128(hi, modulus, 0x00);
    lo = _mm_xor_si128(lo, tmp);

    return lo;
}

void mm_gf128Mul(const block &xb, const block &yb, block &xy1, block &xy2) {
    // oc::block is typically an alias to __m128i; reinterpret as __m128i
    __m128i x, y;
    std::memcpy(&x, &xb, 16);
    std::memcpy(&y, &yb, 16);

    __m128i lo, hi;
    mm_gf128Mul_impl(x, y, lo, hi);

    // write back raw 128-bit halves (lo/hi) into xy1/xy2 according to original naming
    std::memcpy(&xy1, &lo, 16);
    std::memcpy(&xy2, &hi, 16);
}

block mm_gf128Reduce(const block &x, const block &x1) {
    __m128i lo, hi;
    std::memcpy(&lo, &x, 16);
    std::memcpy(&hi, &x1, 16);
    __m128i out = mm_gf128Reduce_impl(lo, hi);
    block b;
    std::memcpy(&b, &out, 16);
    return b;
}

#else
// Non-x86 fallback stubs
bool hasPCLML() { return false; }
#endif // __x86_64__

// ----------------------- Galois128 methods --------------------------------

Galois128::Galois128(uint64_t a, uint64_t b) {
#ifdef __x86_64__
    // If compiling on x86 and oc::block is 16 bytes, prefer storing as block.
    // We'll always construct as uint128_t and also store block variant if desired.
    uint128_t v = (((uint128_t)a) << 64) | (uint128_t)b;
    // store as uint128_t by default to keep behavior consistent
    value_ = v;
    invalidate_cache();
#else
    value_ = (((uint128_t)a) << 64) | (uint128_t)b;
    invalidate_cache();
#endif
}

// Galois128::Galois128(const uint128_t b) {
//     value_ = b;
//     invalidate_cache();
// }

Galois128 Galois128::Mul(const Galois128 &rhs) const {
    // Prefer PCLMUL path when available and when oc::block can be used.
    #ifdef __x86_64__
    if (hasPCLML()) {
        // Convert both operands to block (__m128i), perform pclmul + reduce
        block A = variant_to_block(this->value_);
        block B = variant_to_block(rhs.value_);

        block lo, hi;
        mm_gf128Mul(A, B, lo, hi);
        block reduced = mm_gf128Reduce(lo, hi);
        return Galois128(variant_to_uint128(Galois128Type(reduced)));
    }
#endif

    // Fallback: pure C++ multiplication on uint128_t
    uint128_t a = variant_to_uint128(this->value_);
    uint128_t b = variant_to_uint128(rhs.value_);
    uint128_t z = cc_gf128Mul(a, b);
    return Galois128(z);
}

Galois128 Galois128::Pow(std::uint64_t i) const {
    Galois128 pow2(*this);
    Galois128 zeroblock((uint128_t)0);
    if (std::memcmp(pow2.data(), zeroblock.data(), 16) == 0) return Galois128((uint128_t)0);

    Galois128 s((uint128_t)1);
    std::uint64_t e = i;
    while (e) {
        if (e & 1) s = s.Mul(pow2);
        pow2 = pow2.Mul(pow2);
        e >>= 1;
    }
    return s;
}

Galois128 Galois128::Inv() const {
    // compute inverse as x^{2^128 - 2} using addition chain similar to reference
    Galois128 a = *this;
    Galois128 result((uint128_t)0);

    for (int64_t i = 0; i <= 6; ++i) {
        Galois128 b(a);
        for (int64_t j = 0; j < (1LL << i); ++j) {
            b = b * b;
        }
        a = a * b;
        if (i == 0) {
            result = b;
        } else {
            result = result * b;
        }
    }

    // verify: Mul(result) == 1
    Galois128 one((uint128_t)1);
    Galois128 check = this->Mul(result);
    uint128_t chk0 = check.get<uint128_t>(0);
    if (chk0 != (uint128_t)1) {
        throw std::runtime_error("Galois128::Inv verification failed");
    }
    return result;
}

// ostream operator
std::ostream &operator<<(std::ostream &os, const Galois128 &x) {
    // print as hex string of 16 bytes (big-endian visual order)
    const uint8_t *d = x.data();
    std::ostringstream ss;
    ss << std::hex << std::setfill('0');
    for (size_t i = 0; i < 16; ++i) {
        ss << std::setw(2) << static_cast<int>(d[i]);
    }
    os << ss.str();
    return os;
}

} // namespace okvs

