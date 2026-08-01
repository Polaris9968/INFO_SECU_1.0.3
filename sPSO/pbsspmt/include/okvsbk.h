#pragma once
// OKVSBK.h

#include <cstdint>
#include <vector>

#include "blake3.h"
#include "cryptoTools/Common/block.h"
#include "cryptoTools/Crypto/PRNG.h"
#include "galois128.h"

using block = osuCrypto::block;
using Row = uint64_t;

struct OKVSParam {
    uint64_t n;
    uint64_t w;
    double e;
};

class OKVSBK {
public:
    OKVSBK(uint64_t n, uint64_t w, double e, const uint8_t* seed, uint32_t seedLen);
    OKVSBK() = delete;

    uint64_t getN() const { return n_; }
    uint64_t getM() const { return m_; }
    uint64_t getW() const { return w_; }
    uint64_t getR() const { return r_; }
    uint64_t getB() const { return b_; }
    double getE() const { return e_; }

    bool Encode(const std::vector<block>& keys, const std::vector<block>& values);
    void Decode(const std::vector<block>& keys, std::vector<block>& values);
    void DecodeOtherP(const std::vector<block>& keys, std::vector<block>& values,
                      const std::vector<block>& p);
    void Mul(const okvs::Galois128& delta_gf128);

    const std::vector<block>& getP() const { return p_; }

private:
    uint64_t n_;
    uint64_t m_;
    uint32_t w_;
    uint32_t r_;
    uint32_t b_;
    uint32_t stride_;
    double e_;
    std::vector<block> p_;

    uint8_t seed_[BLAKE3_KEY_LEN];
    blake3_hasher m_hasher;

    static inline void HashToFixedSize(blake3_hasher* hasher, uint8_t* dest,
                                       size_t bytesize, const block key);
    static inline uint32_t DerivePos(const uint8_t* row_ptr, uint32_t r);
    static inline Row MakeRow(uint32_t idx, uint32_t pos);
    static inline uint32_t RowIdx(Row row);
    static inline uint32_t RowPos(Row row);
    static inline bool getBit(uint8_t byte, uint32_t idx);
    static inline void XorMemory(uint8_t* dest, const uint8_t* src, size_t len);
};
