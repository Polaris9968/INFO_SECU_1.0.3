#pragma once

// galois128.h
//
// Reworked to remove yacl dependency and use libOTe / cryptoTools' block type.
// - Uses `oc::block` from cryptoTools (include "cryptoTools/Common/block.h").
// - Uses GCC builtin unsigned __int128 as uint128_t.
// - Keeps original public interface (constructors, Mul/Inv/Pow declarations, get<T>(), etc.).
// - Internals store a std::variant<oc::block, uint128_t> and provide a mutable cache
//   so const methods can return a stable pointer to 16-byte representation.
//
// Note: multiplication / reduction implementations are provided in the corresponding
//       .cpp file (not here). This header only contains declarations and inline helpers.

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <type_traits>
#include <variant>

#include "cryptoTools/Common/block.h" // osuCrypto::block

namespace okvs {

// Use GCC/Clang builtin 128-bit integer types
typedef unsigned __int128 uint128_t;
typedef __int128 int128_t;

// Allow printing uint128_t for debugging if desired (not implemented here).
// Keep alias consistent with earlier code expectations.

using osuCrypto::block;

// Variant stores either a cryptoTools block or a native uint128_t.
using Galois128Type = std::variant<block, uint128_t>;

// Forward declaration of multiplication helper implemented in .cpp
uint128_t cc_gf128Mul(const uint128_t a, const uint128_t b);

class Galois128 {
    public:
    // Constructors
    Galois128(uint64_t a, uint64_t b);
    explicit Galois128(uint64_t v) : Galois128(0, v) {}

    Galois128(const Galois128& v) { value_ = v.value_; invalidate_cache(); }

    // Construct from cryptoTools block
    explicit Galois128(const block& b) { value_ = b; invalidate_cache(); }

    // Construct from native 128-bit integer
    explicit Galois128(const uint128_t b) { value_ = b; invalidate_cache(); }

    Galois128& operator=(const Galois128& other) {
        value_ = other.value_;
        invalidate_cache();
        return *this;
    }

    // Arithmetic API (implemented in .cpp)
    Galois128 Add(const Galois128& rhs) const { 
        return Galois128(to_block(value_) ^ to_block(rhs.value_));
    }
    Galois128 Mul(const Galois128& rhs) const;
    Galois128 Pow(std::uint64_t i) const;
    Galois128 Inv() const;

    inline Galois128 operator+(const Galois128& rhs) const { return Galois128(Add(rhs)); }
    inline Galois128 operator*(const Galois128& rhs) const { return Galois128(Mul(rhs)); }
    inline Galois128 operator+(const block& rhs) const {
        return Galois128(Add(Galois128(rhs)));
    }
    inline Galois128 operator*(const block& rhs) const {
        return Galois128(Mul(Galois128(rhs)));
    }
    inline Galois128 operator+(const uint128_t& rhs) const {
        return Galois128(Add(Galois128(rhs)));
    }
    inline Galois128 operator*(const uint128_t& rhs) const {
        return Galois128(Mul(Galois128(rhs)));
    }
    inline Galois128 operator+(const uint64_t& rhs) const {
        return Galois128(Add(Galois128(rhs)));
    }
    inline Galois128 operator*(const uint64_t& rhs) const { return Galois128(Mul(Galois128(rhs))); }

    // Return pointer to 16-byte representation (stable pointer backed by mutable cache)
    const uint8_t* data() const {
        ensure_cache();
        return reinterpret_cast<const uint8_t*>(&cache_);
    }

    // Copy-out helpers (like original interface)
    template <typename T>
    typename std::enable_if<std::is_standard_layout<T>::value &&
                                std::is_trivial<T>::value && (sizeof(T) <= 16) &&
                                (16 % sizeof(T) == 0),
                            std::array<T, 16 / sizeof(T)> >::type
    get() const {
        std::array<T, 16 / sizeof(T)> output;
        std::memcpy(output.data(), data(), 16);
        return output;
    }

    template <typename T>
    typename std::enable_if<std::is_standard_layout<T>::value &&
                                std::is_trivial<T>::value && (sizeof(T) <= 16) &&
                                (16 % sizeof(T) == 0),
                            T>::type
    get(size_t index) const {
        if (index >= (16 / sizeof(T))) {
            throw std::out_of_range("Galois128::get index out of range");
        }
        T output;
        std::memcpy(&output, data() + sizeof(T) * index, sizeof(T));
        return output;
    }

    private:
    // Helper: convert variant value to oc::block
    static block to_block(const Galois128Type& v) {
        if (std::holds_alternative<block>(v)) {
            return std::get<block>(v);
        } else {
            uint128_t val = std::get<uint128_t>(v);
            block b{};
            // Copy 16 bytes from uint128_t into block (assumes sizeof(block) == 16).
            // Use memcpy to avoid strict-aliasing issues.
            std::memcpy(&b, &val, sizeof(b));
            return b;
        }
    }

    // Ensure cache contains current value as oc::block
    void ensure_cache() const {
        if (!cache_valid_) {
            cache_ = to_block(value_);
            cache_valid_ = true;
        }
    }

    // Invalidate cache when internal value changes
    void invalidate_cache() const { cache_valid_ = false; }

    private:
    Galois128Type value_;

    // mutable cache so const methods (like data()) can populate it lazily
    mutable block cache_{};
    mutable bool cache_valid_ = false;
};

// ostream operator for convenience (implementation may be provided in .cpp)
std::ostream& operator<<(std::ostream& os, const Galois128& x);

}  // namespace okvs
