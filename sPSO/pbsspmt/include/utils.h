#pragma once
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>

#include "galois128.h"
#include "blake3.h"
#include "cryptoTools/Common/Defines.h"  // for osuCrypto::block

inline std::string print_bytes(const void* p, size_t size) {
    const uint8_t* q = static_cast<const uint8_t*>(p);
    std::ostringstream ss;
    ss << std::hex << std::setfill('0');
    for (size_t i = 0; i < size; ++i)
        ss << std::setw(2) << static_cast<int>(q[i]);
    return ss.str();
}

inline std::string print_hex16(const void* p) {
    return print_bytes(p, 16);
}

inline std::string print_hex16(const okvs::Galois128& g) {
    return print_hex16(g.data());
}

inline std::string print_hex16(const osuCrypto::block& b) {
    return print_hex16(&b);
}

inline uint64_t blake3_hash64(uint64_t key, const osuCrypto::block& seed) {
    blake3_hasher hasher;
    blake3_hasher_init(&hasher);
    blake3_hasher_update(&hasher, &key, sizeof(key));
    blake3_hasher_update(&hasher, &seed, sizeof(seed));

    uint8_t out[8];
    blake3_hasher_finalize(&hasher, out, sizeof(out));

    uint64_t result;
    std::memcpy(&result, out, sizeof(result));

    return result;
}

inline uint64_t blake3_hash64(const osuCrypto::block& key, const osuCrypto::block& seed) {
    blake3_hasher hasher;
    blake3_hasher_init(&hasher);
    blake3_hasher_update(&hasher, &key, sizeof(key));
    blake3_hasher_update(&hasher, &seed, sizeof(seed));

    uint8_t out[8];
    blake3_hasher_finalize(&hasher, out, sizeof(out));

    uint64_t result;
    std::memcpy(&result, out, sizeof(result));

    return result;
}