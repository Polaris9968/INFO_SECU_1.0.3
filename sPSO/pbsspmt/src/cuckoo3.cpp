#include "cuckoo3.h"
#include "utils.h"

// namespace osuCrypto {

std::array<uint64_t,3> get3hash(uint64_t key, uint64_t range, block seed) {
    const int MAX_ATTEMPT = 5;
    for (int attempt = 0; attempt < MAX_ATTEMPT; ++attempt) {
        block new_seed = seed ^ block(key, uint64_t(attempt + 99));
        PRNG hprng(new_seed);
        uint64_t h1 = hprng.get<uint64_t>() % range;
        uint64_t h2 = hprng.get<uint64_t>() % range;
        uint64_t h3 = hprng.get<uint64_t>() % range;
        if (h1 != h2 && h1 != h3 && h2 != h3) return {h1,h2,h3};
    }
    throw std::runtime_error("CuckooHash3: Failed to get distinct hash positions");
}

void CuckooHash3::init(uint64_t n_, uint64_t m_, block seed_, uint64_t dummy_) {
    if (state_ != State::Uninitialized)
        throw std::runtime_error("CuckooHash3: init() called more than once or after insertion");

    if (n_ == 0 || m_ == 0 || n_ >= m_)
        throw std::invalid_argument("CuckooHash3: invalid n/m parameters (require n < m)");

    n = n_;
    m = m_;
    seed = seed_;
    dummy = dummy_;
    hashed_pos[0] = blake3_hash64((uint64_t)0x1, seed_);
    hashed_pos[1] = blake3_hash64((uint64_t)0x2, seed_);
    hashed_pos[2] = blake3_hash64((uint64_t)0x3, seed_);

    dummyB = block(dummy, 0);
    prng.SetSeed(seed);
    table.assign(m, dummyB);
    positions.clear();
    positions.resize(n);

    state_ = State::Initialized;
}

void CuckooHash3::precompute_hashes(const std::vector<uint64_t>& keys) {
    if (state_ != State::Initialized)
        throw std::runtime_error("CuckooHash3: precompute_hashes() before init()");
    if (keys.size() != n)
        throw std::invalid_argument("CuckooHash3: keys size mismatch with n");

    for (uint64_t idx = 0; idx < n; ++idx) {
        positions[idx] = get3hash(keys[idx], m, seed);
    }
}

void CuckooHash3::insert(const std::vector<uint64_t>& keys) {
    if (state_ != State::Initialized)
        throw std::runtime_error("CuckooHash3: insert() before init()");
    if (keys.size() != n)
        throw std::invalid_argument("CuckooHash3: insert() keys size mismatch with n");

    precompute_hashes(keys);

    for (uint64_t idx = 0; idx < n; ++idx)
        insert_idx(idx);

    replace_with_keys(keys);
    state_ = State::Inserted;
}

const std::vector<block>& CuckooHash3::get_table() const {
    if (state_ != State::Inserted)
        throw std::runtime_error("CuckooHash3: get_table() before insert()");
    return table;
}

void CuckooHash3::insert_idx(uint64_t idx) {
    // for (int i = 0; i < 3; ++i) {
    //     uint64_t pos = positions[idx][i];
    //     if (table[pos] == dummy) {
    //         table[pos] = block(idx, uint64_t(i+1));
    //         return;
    //     }
    // }

    // random walk
    uint64_t cur_idx = idx;
    for (int attempt = 0; attempt < CUCKOO_MAX_VICTIM; ++attempt) {
        // find empty entry first
        for (uint64_t i = 0; i < 3; ++i) {
            uint64_t pos = positions[cur_idx][i];
            if (table[pos] == dummyB) {
                // std::cout << "idx " << idx << " insert in pos = " << pos << std::endl;
                table[pos] = block(cur_idx, hashed_pos[i]);
                return;
            }
        }

        int k = prng.get<u8>() % 3;
        uint64_t pos = positions[cur_idx][k];

        block victim = table[pos];
        table[pos] = block(cur_idx, hashed_pos[k]);

        uint64_t vic_idx = victim.get<uint64_t>(1);
        uint64_t vic_hash = victim.get<uint64_t>(0);

        for (uint64_t alt = 0; alt < 3; ++alt) {
            if (hashed_pos[alt] == vic_hash) continue;
            uint64_t pos2 = positions[vic_idx][alt];
            if (table[pos2] == dummyB) {
                table[pos2] = block(vic_idx, hashed_pos[alt]);
                return;
            }
        }

        cur_idx = vic_idx;
    }

    std::cerr << "Failed to insert idx = " << idx << " into position (" << positions[idx][0] << ", " << positions[idx][1] << ", " << positions[idx][2] << ")" << std::endl;

    throw std::runtime_error("CuckooHash3: insertion failed (too many evictions), idx = " + std::to_string(cur_idx));
}

void CuckooHash3::replace_with_keys(const std::vector<uint64_t>& keys) {
    for (uint64_t pos = 0; pos < m; ++pos) {
        if (table[pos] == dummyB) {
            table[pos] = block(dummy, blake3_hash64(pos, seed));
        } else {
            uint64_t idx = table[pos].get<uint64_t>(1);
            uint64_t which = table[pos].get<uint64_t>(0);
            
            table[pos] = block(keys[idx], which);
        }
    }
}

// } // namespace osuCrypto