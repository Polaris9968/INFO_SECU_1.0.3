#pragma once
#include <vector>
#include <string>
#include <set>
#include <memory>
#include <stdexcept>

#include <coproto/Common/macoro.h>
#include <coproto/Socket/AsioSocket.h>
#include <cryptoTools/Common/block.h>
#include <cryptoTools/Crypto/PRNG.h>
#include <libOTe/Tools/CoeffCtx.h>
#include <libOTe/Vole/Silent/SilentVoleSender.h>
#include <libOTe/Vole/Silent/SilentVoleReceiver.h>

#include "okvsbk.h"
#include "galois128.h"

namespace oprf {

using namespace std;
using namespace osuCrypto;
using namespace coproto;
using namespace okvs;

// Vec type used by CoeffCtxGF128
using VecB = typename CoeffCtxGF128::template Vec<block>;

// Hash helper (defined in cpp)
inline block HashToBlock(const block& val, const block& seed);

// status enum
enum OprfStatus {
    Uninit,
    Init,
    KeyReady,  // run() completed and oprf is available
    Evaluated  // eval() has been used at least once (optional)
};

// Sender: run() computes WW (no inputs), eval() evaluates arbitrary inputs using WW.
class OprfSender {
public:
    PRNG commPrng;
    uint8_t okvsSeed_w[BLAKE3_KEY_LEN];
    block hash1;
    block hash2;
    block delta;

    VecB W;                      // VOLE sender output W
    std::vector<block> WW;       // WW = delta * pp + W (p-vector for Decode)
    std::unique_ptr<OKVSBK> okvs;
    std::shared_ptr<coproto::Socket> chl;

    OprfStatus stat_ = OprfStatus::Uninit;

    uint64_t n = 0;
    uint64_t m = 0;
    uint64_t w = 0;
    double  e = 1.0;

    OprfSender() = default;

    // Initialize: sets sizes, seeds, OKVS instance, reserves vectors.
    void init(uint64_t n_, uint64_t w_, double e_, std::shared_ptr<coproto::Socket> chl_, block prngSeed);
    void init(OKVSParam param, std::shared_ptr<coproto::Socket> chl_, block prngSeed);

    // run: perform VOLE sender, receive pp from receiver, compute WW.
    // No key input, no sender_out is produced/sent here.
    void run();

    // eval: evaluate OPRF on arbitrary inputs `in`, writing results into `out`.
    // Requires run() to have been called (WW must be ready).
    // Caller may provide out sized or not; this function will resize out.
    void eval(const std::vector<block>& in, std::vector<block>& out);
};

// Receiver: builds OKVS (encode), receives VOLE (U,V) and sends pp = p ^ U
class OprfRecver {
public:
    PRNG commPrng;
    uint8_t okvsSeed_w[BLAKE3_KEY_LEN];
    block hash1;
    block hash2;

    // OKVSBK okvs;
    VecB U;
    VecB V;
    VecB pp;

    std::unique_ptr<OKVSBK> okvs;
    std::shared_ptr<coproto::Socket> chl;

    OprfStatus stat_ = OprfStatus::Uninit;

    uint64_t n = 0;
    uint64_t m = 0;
    uint64_t w = 0;
    double  e = 1.0;

    OprfRecver() = default;

    // initialize
    void init(uint64_t n_, uint64_t w_, double e_, std::shared_ptr<coproto::Socket> chl_, block prngSeed);
    void init(OKVSParam param, std::shared_ptr<coproto::Socket> chl_, block prngSeed);

    // run the oprf protocol with input keys and obtains prf_vals
    void run(const std::vector<block>& keys, std::vector<block>& prf_vals);
};

} // namespace oprf
