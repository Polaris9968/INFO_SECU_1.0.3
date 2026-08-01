// common.h - shared utilities for sPSO protocol tests (PSI / PSU / PSI-Card / PSI-Sum)
//
// All four test executables (test_psi, test_psu, test_card, test_psum) include
// this header and call run_pso(MODE_*) from common.cpp. The protocol logic up
// to and including the pbssPMT step is mode-independent; only the OT msg
// construction (sender) and the post-OT validation (receiver) branch on mode.

#pragma once

#include <cassert>
#include <iomanip>
#include <iostream>
#include <random>
#include <set>
#include <chrono>
#include <vector>
#include <stdexcept>
#include <thread>
#include <unordered_set>
#include <optional>
#include <string>

// libOTe
#include <cryptoTools/Common/BitVector.h>
#include <cryptoTools/Common/block.h>
#include <cryptoTools/Crypto/PRNG.h>
#include <coproto/Common/macoro.h>
#include <coproto/Socket/AsioSocket.h>
#include <macoro/sync_wait.h>
#include <libOTe/Base/BaseOT.h>
#include <libOTe/Base/SimplestOT.h>
#include <libOTe/TwoChooseOne/Silent/SilentOtExtSender.h>
#include <libOTe/TwoChooseOne/Silent/SilentOtExtReceiver.h>

// our lib
#include "oprf.h"
#include "okvsbk.h"
#include "cuckoo3.h"
#include "galois128.h"
#include "utils.h"
#include "neweq.h"
#include <secure-join/Perm/AltModPerm.h>
#include <coproto/Socket/LocalAsyncSock.h>

#ifdef HAVE_OPENMP
#include <omp.h>
#endif

using namespace secJoin;
using namespace std;
using namespace osuCrypto;
using namespace coproto;
using namespace oprf;
using okvs::Galois128;

// ============================================================
// PSO mode selector (Figure 11 of paper)
//
//   PSI:            msg_i^b = (b ⊕ u_i) · cuco_tab[π(i)]              →  R learns X ∩ Y
//   PSU:            msg_i^b = (1 ⊕ b ⊕ u_i) · cuco_tab[π(i)]          →  R learns X \ Y, outputs X ∪ Y
//   PSI-Card:       msg_i^b = b ⊕ u_i (single bit)                    →  R learns |X ∩ Y|
//   PSI-Sum:        msg_i^b = r_i + (b ⊕ e_s^i) · V*[π(i)] (mod q)     →  R learns Σ_{X∩Y} val_x (mod q)
//                            (one-shot extra send: r' = Σ r_i mod q to R)
//   Secret-Shared PSI: msg_i^b = r_i ⊕ (b ⊕ e_s^i) · X*[π(i)]          →  S holds ⃗r, R holds ⃗z; XOR-shares
//                            of intersection values are recoverable when both parties combine shares.
// ============================================================
enum PSOMode { MODE_PSI = 0, MODE_PSU = 1, MODE_CARD = 2,
                MODE_PSI_SUM = 3, MODE_SS_PSI = 4 };

inline const char* mode_name(PSOMode m) {
    return (m == MODE_PSI)    ? "PSI"
         : (m == MODE_PSU)    ? "PSU"
         : (m == MODE_CARD)   ? "PSI-Card"
         : (m == MODE_PSI_SUM)? "PSI-Sum"
         :                     "SS-PSI";   // MODE_SS_PSI
}

// ============================================================
// Default test parameters
// ============================================================
//   NOTE: n MUST satisfy cuco_sz = ceil(cuckoo_exp * n) > DEFAULT_OKVS_W
//         (otherwise OPRF::init throws "m must be > w" — silently).
//         With cuckoo_exp=1.22 and w=96, smallest n is 79; default 256.
constexpr uint64_t DEFAULT_N     = (1ull << 8);   // 256 (per TESTING_NOTES debug default)
constexpr uint64_t DEFAULT_INTER = (1ull << 5);   // 32  (per TESTING_NOTES debug default)
constexpr double   DEFAULT_OKVS_EXP = 1.30;
constexpr uint64_t DEFAULT_OKVS_W   = 96;

// ----- PSI-Sum payload defaults (paper Figure 11 / Section 5) -----
//   val_i ∈ Z_p, single payload fits in p bits
//   q chosen so  DEFAULT_N · p < q   (no wrap-around under sum mod q)
constexpr uint64_t DEFAULT_PAYLOAD_P = (1ull << 32);   // 2^32 per element
constexpr uint64_t DEFAULT_PAYLOAD_Q = (1ull << 50);   // 2^50 > DEFAULT_N · DEFAULT_PAYLOAD_P

// ============================================================
// Helpers
// ============================================================

// time formatter (ms)
static inline double fmt_time(auto start, auto end) {
    using namespace std::chrono;
    return duration_cast<milliseconds>(end - start).count();
}

// comm formatter (MB)
static inline double fmt_comm(uint64_t bytes) {
    return bytes / (1024.0 * 1024.0);
}

// Generate a deterministic set of n 64-bit elements using BLAKE3.
inline void set_gen(uint64_t n, uint64_t start, vector<uint64_t>& keys, block seed) {
    if (keys.size() != (size_t)n) {
        throw std::invalid_argument("keys size mismatch with n");
    }
    for (uint64_t i = 0; i < n; ++i) {
        keys[i] = blake3_hash64(i + start, seed);
    }
}

// ============================================================
// Plaintext dump helper — prints a set/vector as 8 hex values per line.
// Used for human eyeball verification of protocol results.
// ============================================================
template<typename Container>
inline void print_set_hex(const Container& c, const std::string& label, int per_line = 8) {
    std::cout << label << " (size=" << c.size() << "):" << std::endl;
    int cnt = 0;
    for (auto x : c) {
        if (cnt > 0 && cnt % per_line == 0) std::cout << std::endl;
        // Always pad to 16 hex digits (full uint64_t) so columns line up for eyeball diff.
        std::cout << "  0x" << std::setw(16) << std::setfill('0') << std::hex << x << std::dec;
        cnt++;
    }
    std::cout << std::endl;
}

// ============================================================
// Main entry point — runs the full sPSO protocol in two threads
// (sender + receiver, in-process) with the selected PSO mode.
//
// All four functionalities from paper Figure 11 share this single entry:
//   * PSI  mode → non-zero msg[i] ⇒ element ∈ S ∩ R, count = inter
//   * PSU  mode → kept msg[i] elements form X \ R; Y ∪ kept == X ∪ Y
//   * CARD mode → HammingWeight of low bit of msg[i] == |X ∩ Y| == inter
//   * PSUM mode → receiver recovers sum = Σ_{x ∈ X∩Y} val_x (mod q)
//
// `print_sets`: if true, dump S / R / result sets as plaintext hex after
//               protocol completes. Default off; auto-on when n <= 128.
//
// PSI-Sum params (only consulted when mode == MODE_PSI_SUM):
//   `payload`   : val_i ∈ Z_p per element. If empty, common.cpp generates a
//                 deterministic random payload via PRNG (same seed chain as
//                 set_gen, so unit tests stay reproducible).
//                 If non-empty, must have size == n; values are reduced mod p.
//   `p`, `q`    : payload modulus and sum modulus. Pass 0 to take
//                 DEFAULT_PAYLOAD_P / DEFAULT_PAYLOAD_Q. Precondition: q > n · p.
//
// Throws on protocol error or precondition violation. Prints timing / comm
// stats to stdout.
// ============================================================
void run_pso(PSOMode mode,
             bool print_sets = false,
             std::vector<uint64_t> payload = {},
             uint64_t p = DEFAULT_PAYLOAD_P,
             uint64_t q = DEFAULT_PAYLOAD_Q);