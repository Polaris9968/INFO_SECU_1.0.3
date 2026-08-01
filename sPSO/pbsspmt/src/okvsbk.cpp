// OKVSBK.cpp
#include "okvsbk.h"
#include "blake3.h"

#include <boost/sort/spreadsort/spreadsort.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <sys/time.h>
#include <vector>

#ifdef HAVE_OPENMP
#include <omp.h>
#endif

using osuCrypto::PRNG;
using osuCrypto::block;

static inline double elapsedMs(const timeval& start, const timeval& end) {
    return (end.tv_sec - start.tv_sec) * 1000.0 +
           (end.tv_usec - start.tv_usec) / 1000.0;
}

static const uint8_t bitMasks[8] = {
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
};

static inline block makeMaskBlock(uint8_t val) {
    return block::allSame(val);
}

inline bool OKVSBK::getBit(uint8_t b, uint32_t n) {
    return (b & bitMasks[n]) != 0;
}

inline void OKVSBK::XorMemory(uint8_t* dest, const uint8_t* src, size_t len) {
    size_t block_count = len / sizeof(block);
    size_t remain = len % sizeof(block);

    block* d_ptr = reinterpret_cast<block*>(dest);
    const block* s_ptr = reinterpret_cast<const block*>(src);

    for (size_t i = 0; i < block_count; ++i) {
        d_ptr[i] = d_ptr[i] ^ s_ptr[i];
    }

    if (remain > 0) {
        size_t offset = block_count * sizeof(block);
        uint8_t* d_tail = dest + offset;
        const uint8_t* s_tail = src + offset;
        for (size_t j = 0; j < remain; ++j) {
            d_tail[j] ^= s_tail[j];
        }
    }
}

inline void OKVSBK::HashToFixedSize(blake3_hasher* hasher, uint8_t* dest,
                                    size_t bytesize, const block key) {
    blake3_hasher_reset(hasher);
    blake3_hasher_update(hasher, &key, sizeof(key));
    blake3_hasher_finalize(hasher, dest, bytesize);
}

inline uint32_t OKVSBK::DerivePos(const uint8_t* row_ptr, uint32_t r) {
    uint64_t raw_pos_int;
    std::memcpy(&raw_pos_int, row_ptr, sizeof(uint64_t));
    return static_cast<uint32_t>(((raw_pos_int % r) / 8) << 3);
}

inline Row OKVSBK::MakeRow(uint32_t idx, uint32_t pos) {
    return (static_cast<uint64_t>(pos) << 32) | static_cast<uint64_t>(idx);
}

inline uint32_t OKVSBK::RowIdx(Row row) {
    return static_cast<uint32_t>(row);
}

inline uint32_t OKVSBK::RowPos(Row row) {
    return static_cast<uint32_t>(row >> 32);
}

OKVSBK::OKVSBK(uint64_t n, uint64_t w, double e, const uint8_t* seed,
               uint32_t seedLen)
    : n_(n),
      m_(static_cast<uint64_t>(std::ceil(n * e))),
      w_(static_cast<uint32_t>(w)),
      r_(0),
      b_(0),
      stride_(0),
      e_(e) {
    if (seedLen != BLAKE3_KEY_LEN) {
        throw std::invalid_argument("Seed length must be exactly 32 bytes for BLAKE3");
    }
    if (w == 0 || (w % 8) != 0 || w > UINT32_MAX) {
        throw std::invalid_argument("w must be positive, multiple of 8, and fit in uint32_t");
    }
    if (m_ <= w_) {
        throw std::invalid_argument("m must be > w");
    }
    if ((m_ - w_) > UINT32_MAX) {
        throw std::invalid_argument("r must fit in uint32_t");
    }

    b_ = static_cast<uint32_t>(w_ / 8);
    stride_ = b_;
    r_ = static_cast<uint32_t>(m_ - w_);

    std::memcpy(seed_, seed, BLAKE3_KEY_LEN);
    blake3_hasher_init_keyed(&m_hasher, seed_);
}

bool OKVSBK::Encode(const std::vector<block>& keys, const std::vector<block>& values) {
    if (keys.size() != n_ || values.size() != n_) {
        throw std::invalid_argument("keys/values size mismatch with n");
    }

    timeval t_gen_start{}, t_gen_end{};
    timeval t_sort_start{}, t_sort_end{};
    timeval t_linear_start{}, t_linear_end{};
    timeval t_elim_start{}, t_elim_end{};
    timeval t_back_start{}, t_back_end{};

    std::vector<uint32_t> piv(n_, UINT32_MAX);
    std::vector<Row> rows(n_);
    std::vector<uint8_t> row_buf(n_ * stride_);

    gettimeofday(&t_gen_start, nullptr);
#ifdef HAVE_OPENMP
#pragma omp parallel
    {
        blake3_hasher hasher = m_hasher;
#pragma omp for schedule(static)
        for (uint64_t idx64 = 0; idx64 < n_; ++idx64) {
            const uint32_t idx = static_cast<uint32_t>(idx64);
            uint8_t* row_ptr = row_buf.data() + (static_cast<size_t>(idx) * stride_);
            HashToFixedSize(&hasher, row_ptr, b_, keys[idx64]);
            rows[idx64] = MakeRow(idx, DerivePos(row_ptr, r_));
        }
    }
#else
    {
        blake3_hasher hasher = m_hasher;
        for (uint64_t idx64 = 0; idx64 < n_; ++idx64) {
            const uint32_t idx = static_cast<uint32_t>(idx64);
            uint8_t* row_ptr = row_buf.data() + (static_cast<size_t>(idx) * stride_);
            HashToFixedSize(&hasher, row_ptr, b_, keys[idx64]);
            rows[idx64] = MakeRow(idx, DerivePos(row_ptr, r_));
        }
    }
#endif
    gettimeofday(&t_gen_end, nullptr);

    gettimeofday(&t_sort_start, nullptr);
    boost::sort::spreadsort::spreadsort(rows.begin(), rows.end());
    gettimeofday(&t_sort_end, nullptr);

    gettimeofday(&t_linear_start, nullptr);
    std::vector<uint8_t> sorted_row_buf(n_ * stride_);
    std::vector<block> sorted_values(n_);
#ifdef HAVE_OPENMP
#pragma omp parallel for schedule(static)
#endif
    for (uint64_t i64 = 0; i64 < n_; ++i64) {
        const uint32_t i = static_cast<uint32_t>(i64);
        const uint32_t src_idx = RowIdx(rows[i64]);
        std::memcpy(sorted_row_buf.data() + (static_cast<size_t>(i) * stride_),
                    row_buf.data() + (static_cast<size_t>(src_idx) * stride_), b_);
        sorted_values[i64] = values[src_idx];
    }
    gettimeofday(&t_linear_end, nullptr);

    gettimeofday(&t_elim_start, nullptr);
    for (uint32_t i = 0; i < n_; ++i) {
        bool found = false;
        uint8_t* row_i_ptr = sorted_row_buf.data() + (static_cast<size_t>(i) * stride_);
        const uint32_t row_i_pos = RowPos(rows[i]);
        const uint32_t row_i_bpos = row_i_pos >> 3;

        for (uint32_t j = 0; j < w_; ++j) {
            const uint32_t byte_idx = j >> 3;
            const uint32_t bit_idx = j & 0x7;
            if (!getBit(row_i_ptr[byte_idx], bit_idx)) {
                continue;
            }

            piv[i] = static_cast<uint32_t>(row_i_pos + j);
            found = true;

            for (uint32_t k = i + 1; k < n_; ++k) {
                if (RowPos(rows[k]) > piv[i]) {
                    break;
                }

                const uint32_t posk = static_cast<uint32_t>(piv[i] - RowPos(rows[k]));
                uint8_t* row_k_ptr = sorted_row_buf.data() + (static_cast<size_t>(k) * stride_);
                if (!getBit(row_k_ptr[posk >> 3], posk & 0x7)) {
                    continue;
                }

                const uint32_t row_k_bpos = RowPos(rows[k]) >> 3;
                const uint32_t shift = row_k_bpos - row_i_bpos;
                if (b_ > shift) {
                    XorMemory(row_k_ptr, row_i_ptr + shift, static_cast<size_t>(b_ - shift));
                }
                sorted_values[k] ^= sorted_values[i];
            }
            break;
        }

        if (!found) {
            throw std::runtime_error("encode failed");
        }
    }
    gettimeofday(&t_elim_end, nullptr);

    gettimeofday(&t_back_start, nullptr);
    PRNG prng(osuCrypto::sysRandomSeed());
    auto seed = prng.get<block>();
    p_.resize(m_);
#ifdef HAVE_OPENMP
#pragma omp parallel
    {
        PRNG local(seed ^ osuCrypto::toBlock(omp_get_thread_num()));
#pragma omp for schedule(static)
        for (uint64_t i = 0; i < p_.size(); ++i) {
            p_[i] = local.get<block>();
        }
    }
#else
    for (auto& x : p_) {
        x = prng.get<block>();
    }
#endif

    for (uint32_t idx = static_cast<uint32_t>(n_ - 1); idx < n_; --idx) {
        const uint8_t* row_ptr = sorted_row_buf.data() + (static_cast<size_t>(idx) * stride_);
        const uint32_t base_pos = RowPos(rows[idx]);
        const uint32_t pivot = piv[idx];

        __uint128_t xorRes = 0;
        const __uint128_t* curPtr = reinterpret_cast<const __uint128_t*>(p_.data() + base_pos + w_ - 8);
        const uint8_t* rowByte = row_ptr + b_ - 1;
        for (uint32_t k = b_; k-- > 0; --rowByte, curPtr -= 8) {
            const uint8_t byte = *rowByte;
            const __uint128_t m0 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 0) & 0x1U);
            const __uint128_t m1 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 1) & 0x1U);
            const __uint128_t m2 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 2) & 0x1U);
            const __uint128_t m3 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 3) & 0x1U);
            const __uint128_t m4 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 4) & 0x1U);
            const __uint128_t m5 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 5) & 0x1U);
            const __uint128_t m6 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 6) & 0x1U);
            const __uint128_t m7 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 7) & 0x1U);
            xorRes ^= (curPtr[0] & m0);
            xorRes ^= (curPtr[1] & m1);
            xorRes ^= (curPtr[2] & m2);
            xorRes ^= (curPtr[3] & m3);
            xorRes ^= (curPtr[4] & m4);
            xorRes ^= (curPtr[5] & m5);
            xorRes ^= (curPtr[6] & m6);
            xorRes ^= (curPtr[7] & m7);
        }

        __uint128_t pivotVal;
        std::memcpy(&pivotVal, &p_[pivot], sizeof(pivotVal));
        __uint128_t sortedVal;
        std::memcpy(&sortedVal, &sorted_values[idx], sizeof(sortedVal));
        pivotVal = xorRes ^ pivotVal ^ sortedVal;
        std::memcpy(&p_[pivot], &pivotVal, sizeof(pivotVal));
    }
    gettimeofday(&t_back_end, nullptr);

    std::cout << "[OKVSBK::Encode Timing] generate_rows_ms="
              << elapsedMs(t_gen_start, t_gen_end)
              << ", sort_ms=" << elapsedMs(t_sort_start, t_sort_end)
              << ", linearize_memory_ms=" << elapsedMs(t_linear_start, t_linear_end)
              << ", forward_elimination_ms=" << elapsedMs(t_elim_start, t_elim_end)
              << ", back_substitution_ms=" << elapsedMs(t_back_start, t_back_end)
              << std::endl;

    return true;
}

void OKVSBK::Decode(const std::vector<block>& keys, std::vector<block>& values) {
    if (keys.size() != values.size()) {
        throw std::invalid_argument("size mismatch");
    }

#ifdef HAVE_OPENMP
#pragma omp parallel
    {
        blake3_hasher hasher = m_hasher;
#pragma omp for schedule(static)
        for (uint64_t idx64 = 0; idx64 < n_; ++idx64) {
            std::vector<uint8_t> row(b_);
            HashToFixedSize(&hasher, row.data(), b_, keys[idx64]);
            const uint32_t pos = DerivePos(row.data(), r_);
            __uint128_t xorRes = 0;
            const __uint128_t* curPtr = reinterpret_cast<const __uint128_t*>(p_.data() + pos + w_ - 8);
            const uint8_t* rowByte = row.data() + b_ - 1;
            for (uint32_t k = b_; k-- > 0; --rowByte, curPtr -= 8) {
                const uint8_t byte = *rowByte;
                const __uint128_t m0 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 0) & 0x1U);
                const __uint128_t m1 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 1) & 0x1U);
                const __uint128_t m2 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 2) & 0x1U);
                const __uint128_t m3 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 3) & 0x1U);
                const __uint128_t m4 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 4) & 0x1U);
                const __uint128_t m5 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 5) & 0x1U);
                const __uint128_t m6 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 6) & 0x1U);
                const __uint128_t m7 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 7) & 0x1U);
                xorRes ^= (curPtr[0] & m0);
                xorRes ^= (curPtr[1] & m1);
                xorRes ^= (curPtr[2] & m2);
                xorRes ^= (curPtr[3] & m3);
                xorRes ^= (curPtr[4] & m4);
                xorRes ^= (curPtr[5] & m5);
                xorRes ^= (curPtr[6] & m6);
                xorRes ^= (curPtr[7] & m7);
            }
            std::memcpy(&values[idx64], &xorRes, sizeof(xorRes));
        }
    }
#else
    {
        blake3_hasher hasher = m_hasher;
        for (uint64_t idx64 = 0; idx64 < n_; ++idx64) {
            std::vector<uint8_t> row(b_);
            HashToFixedSize(&hasher, row.data(), b_, keys[idx64]);
            const uint32_t pos = DerivePos(row.data(), r_);
            __uint128_t xorRes = 0;
            const __uint128_t* curPtr = reinterpret_cast<const __uint128_t*>(p_.data() + pos + w_ - 8);
            const uint8_t* rowByte = row.data() + b_ - 1;
            for (uint32_t k = b_; k-- > 0; --rowByte, curPtr -= 8) {
                const uint8_t byte = *rowByte;
                const __uint128_t m0 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 0) & 0x1U);
                const __uint128_t m1 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 1) & 0x1U);
                const __uint128_t m2 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 2) & 0x1U);
                const __uint128_t m3 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 3) & 0x1U);
                const __uint128_t m4 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 4) & 0x1U);
                const __uint128_t m5 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 5) & 0x1U);
                const __uint128_t m6 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 6) & 0x1U);
                const __uint128_t m7 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 7) & 0x1U);
                xorRes ^= (curPtr[0] & m0);
                xorRes ^= (curPtr[1] & m1);
                xorRes ^= (curPtr[2] & m2);
                xorRes ^= (curPtr[3] & m3);
                xorRes ^= (curPtr[4] & m4);
                xorRes ^= (curPtr[5] & m5);
                xorRes ^= (curPtr[6] & m6);
                xorRes ^= (curPtr[7] & m7);
            }
            std::memcpy(&values[idx64], &xorRes, sizeof(xorRes));
        }
    }
#endif
}

void OKVSBK::DecodeOtherP(const std::vector<block>& keys, std::vector<block>& values,
                          const std::vector<block>& p) {
    if (keys.size() != values.size()) {
        throw std::invalid_argument("size mismatch");
    }

    const size_t num = keys.size();
#ifdef HAVE_OPENMP
#pragma omp parallel
    {
        blake3_hasher hasher = m_hasher;
#pragma omp for schedule(static)
        for (size_t idx = 0; idx < num; ++idx) {
            std::vector<uint8_t> row(b_);
            HashToFixedSize(&hasher, row.data(), b_, keys[idx]);
            const uint32_t pos = DerivePos(row.data(), r_);
            __uint128_t xorRes = 0;
            const __uint128_t* curPtr = reinterpret_cast<const __uint128_t*>(p.data() + pos + w_ - 8);
            const uint8_t* rowByte = row.data() + b_ - 1;
            for (uint32_t k = b_; k-- > 0; --rowByte, curPtr -= 8) {
                const uint8_t byte = *rowByte;
                const __uint128_t m0 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 0) & 0x1U);
                const __uint128_t m1 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 1) & 0x1U);
                const __uint128_t m2 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 2) & 0x1U);
                const __uint128_t m3 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 3) & 0x1U);
                const __uint128_t m4 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 4) & 0x1U);
                const __uint128_t m5 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 5) & 0x1U);
                const __uint128_t m6 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 6) & 0x1U);
                const __uint128_t m7 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 7) & 0x1U);
                xorRes ^= (curPtr[0] & m0);
                xorRes ^= (curPtr[1] & m1);
                xorRes ^= (curPtr[2] & m2);
                xorRes ^= (curPtr[3] & m3);
                xorRes ^= (curPtr[4] & m4);
                xorRes ^= (curPtr[5] & m5);
                xorRes ^= (curPtr[6] & m6);
                xorRes ^= (curPtr[7] & m7);
            }
            std::memcpy(&values[idx], &xorRes, sizeof(xorRes));
        }
    }
#else
    {
        blake3_hasher hasher = m_hasher;
        for (size_t idx = 0; idx < num; ++idx) {
            std::vector<uint8_t> row(b_);
            HashToFixedSize(&hasher, row.data(), b_, keys[idx]);
            const uint32_t pos = DerivePos(row.data(), r_);
            __uint128_t xorRes = 0;
            const __uint128_t* curPtr = reinterpret_cast<const __uint128_t*>(p.data() + pos + w_ - 8);
            const uint8_t* rowByte = row.data() + b_ - 1;
            for (uint32_t k = b_; k-- > 0; --rowByte, curPtr -= 8) {
                const uint8_t byte = *rowByte;
                const __uint128_t m0 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 0) & 0x1U);
                const __uint128_t m1 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 1) & 0x1U);
                const __uint128_t m2 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 2) & 0x1U);
                const __uint128_t m3 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 3) & 0x1U);
                const __uint128_t m4 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 4) & 0x1U);
                const __uint128_t m5 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 5) & 0x1U);
                const __uint128_t m6 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 6) & 0x1U);
                const __uint128_t m7 = static_cast<__uint128_t>(0) - static_cast<__uint128_t>((byte >> 7) & 0x1U);
                xorRes ^= (curPtr[0] & m0);
                xorRes ^= (curPtr[1] & m1);
                xorRes ^= (curPtr[2] & m2);
                xorRes ^= (curPtr[3] & m3);
                xorRes ^= (curPtr[4] & m4);
                xorRes ^= (curPtr[5] & m5);
                xorRes ^= (curPtr[6] & m6);
                xorRes ^= (curPtr[7] & m7);
            }
            std::memcpy(&values[idx], &xorRes, sizeof(xorRes));
        }
    }
#endif
}

void OKVSBK::Mul(const okvs::Galois128& delta_gf128) {
#ifdef HAVE_OPENMP
#pragma omp parallel for schedule(static)
#endif
    for (uint64_t idx = 0; idx < static_cast<uint64_t>(p_.size()); ++idx) {
        okvs::Galois128 g(p_[idx]);
        okvs::Galois128 res = delta_gf128 * g;
        p_[idx] = res.get<block>(0);
    }
}
