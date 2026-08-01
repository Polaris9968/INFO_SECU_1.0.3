#pragma once
#include "cryptoTools/Common/Defines.h"
#include "cryptoTools/Crypto/PRNG.h"
#include <vector>
#include <array>
#include <stdexcept>
#include <cstring>
#include <iostream>

#define CUCKOO_MAX_VICTIM 256
#define CUCKOO_EXPANSION double(1.22)

// namespace osuCrypto {
using namespace osuCrypto;

std::array<uint64_t, 3> get3hash(uint64_t key, uint64_t range, block seed);

class CuckooHash3 {
public:
    enum class State {
        Uninitialized,
        Initialized,
        Inserted
    };

private:
    State state_ = State::Uninitialized;

public:
    uint64_t n = 0, m = 0;
    block seed;
    uint64_t dummy = 0xff;
    block dummyB;
    PRNG prng;

    // randomize pos
    std::array<uint64_t, 3> hashed_pos;

    std::vector<block> table;
    std::vector<std::array<uint64_t,3>> positions;

    CuckooHash3() = default;

    void init(uint64_t n_, uint64_t m_, block seed_, uint64_t dummy_ = 0xff);

    void precompute_hashes(const std::vector<uint64_t>& keys);

    void insert(const std::vector<uint64_t>& keys);

    const std::vector<block>& get_table() const;

private:
    void insert_idx(uint64_t idx);
    void replace_with_keys(const std::vector<uint64_t>& keys);
};

// } // namespace osuCrypto