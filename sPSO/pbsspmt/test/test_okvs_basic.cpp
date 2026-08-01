// test_okvs_basic.cpp
#include <cassert>
#include <iostream>
#include <random>
#include <set>
#include <chrono>
#include "okvsbk.h"

#include "cryptoTools/Common/block.h"
#include "cryptoTools/Crypto/PRNG.h"

#ifdef HAVE_OPENMP
#include <omp.h>
#endif

// using uint128_t = unsigned __int128;

// static uint128_t rand128(std::mt19937_64 &eng) {
//     uint128_t a = (uint128_t)eng() << 64;
//     uint128_t b = (uint128_t)eng();
//     return a ^ b;
// }

// static std::string toHex(uint128_t x) {
//     char buf[33];
//     for (int i = 0; i < 16; i++) {
//         uint8_t b = (x >> ((15 - i) * 8)) & 0xff;
//         sprintf(buf + 2 * i, "%02x", b);
//     }
//     buf[32] = 0;
//     return buf;
// }

int main() {
#ifdef HAVE_OPENMP
    std::cout << "[INFO] OpenMP threads = " << omp_get_max_threads() << "\n";
#else
    std::cout << "[INFO] OpenMP not enabled\n";
#endif

    const int64_t n = (1 << 20);
    const int64_t w = 128;
    const double  e = 1.30;

    std::mt19937_64 eng{233};
    std::vector<block> keys(n), vals(n);
    std::set<block> used;
    osuCrypto::PRNG prng0(osuCrypto::ZeroBlock);

    for (int i = 0; i < n;) {
        block k = prng0.get();
        if (used.insert(k).second) {
            keys[i] = k;
            vals[i] = prng0.get();
            i++;
        }
    }

    osuCrypto::PRNG prng(osuCrypto::block(0, 1));
    uint8_t okvsSeed_w[BLAKE3_KEY_LEN];
    prng.get(okvsSeed_w, BLAKE3_KEY_LEN);

    // OKVSBK okvs(n, w, e, seed_w, seed_pos);
    OKVSBK okvs(n, w, e, okvsSeed_w, BLAKE3_KEY_LEN);

    auto t0 = std::chrono::high_resolution_clock::now();
    bool ok = okvs.Encode(keys, vals);
    auto t1 = std::chrono::high_resolution_clock::now();

    if (!ok) {
        std::cerr << "Encode returned false\n";
        return 1;
    }

    std::vector<block> out(n);
    okvs.Decode(keys, out);
    auto t2 = std::chrono::high_resolution_clock::now();

    double enc_t = std::chrono::duration<double>(t1 - t0).count();
    double dec_t = std::chrono::duration<double>(t2 - t1).count();

    int errors = 0;
    for (int i = 0; i < n; i++) {
        if (out[i] != vals[i]) {
            errors++;
            if (errors <= 5) {
                std::cerr << "Mismatch at " << i << "\n";
            }
        }
    }

    if (errors == 0) {
        std::cout << "[OK] encode/decode correct for n=" << n << "\n";
        std::cout << "Encode time = " << enc_t << " s\n";
        std::cout << "Decode time = " << dec_t << " s\n";
    } else {
        std::cerr << "[FAIL] errors=" << errors << "\n";
        return 2;
    }

    // -------------------------------------------------------------
    // Additional test: DecodeOtherP()
    // -------------------------------------------------------------
    std::cout << "[INFO] Testing DecodeOtherP() consistency...\n";

    const auto &pvec = okvs.getP();  // expose internal p
    std::vector<block> out2(n);
    okvs.DecodeOtherP(keys, out2, pvec);

    int errors2 = 0;
    for (int i = 0; i < n; i++) {
        if (out2[i] != vals[i]) {
            errors2++;
            if (errors2 <= 5) {
                std::cerr << "Mismatch (DecodeOtherP) at " << i << "\n";
            }
        }
    }

    if (errors2 == 0) {
        std::cout << "[OK] DecodeOtherP() correct for n=" << n << "\n";
        std::cout << "All tests passed.\n";
        return 0;
    } else {
        std::cerr << "[FAIL] DecodeOtherP() errors=" << errors2 << "\n";
        return 3;
    }
}
