#include "oprf.h"
#include <cryptoTools/Common/block.h>
#include <cryptoTools/Crypto/AES.h>
#include <macoro/sync_wait.h>
#include <iostream>
#include <cmath>
#include <memory>
#include <stdexcept>
#include <iomanip>
#include <sstream>

using namespace oprf;

// simple HashToBlock helper (same as your previous)
inline block oprf::HashToBlock(const block& val, const block& seed) {
    block combine = val ^ seed;
    PRNG prng(combine);
    return prng.get();
}

// ---------------- OprfSender ----------------

void OprfSender::init(uint64_t n_, uint64_t w_, double e_, std::shared_ptr<coproto::Socket> chl_, block prngSeed) {
    n = n_;
    w = w_;
    e = e_;
    m = static_cast<uint64_t>(std::ceil(static_cast<double>(n) * e));
    chl = chl_;

    // init PRNG and seeds
    commPrng.SetSeed(prngSeed);
    commPrng.get(okvsSeed_w, BLAKE3_KEY_LEN);
    // hash1 = commPrng.get();
    // hash2 = commPrng.get();

    // initialize OKVS
    okvs = std::make_unique<OKVSBK>(n, w, e, okvsSeed_w, BLAKE3_KEY_LEN);

    // allocate vectors
    W.resize(m);
    WW.resize(m);

    stat_ = OprfStatus::Init;
}

void OprfSender::init(OKVSParam param, std::shared_ptr<coproto::Socket> chl_, block prngSeed) {
    n = param.n;
    w = param.w;
    e = param.e;
    m = static_cast<uint64_t>(std::ceil(static_cast<double>(n) * e));
    chl = chl_;

    // init PRNG and seeds
    commPrng.SetSeed(prngSeed);
    commPrng.get(okvsSeed_w, BLAKE3_KEY_LEN);
    // hash1 = commPrng.get();
    // hash2 = commPrng.get();

    // initialize OKVS
    okvs = std::make_unique<OKVSBK>(n, w, e, okvsSeed_w, BLAKE3_KEY_LEN);

    // allocate vectors
    W.resize(m);
    WW.resize(m);

    stat_ = OprfStatus::Init;
}

void OprfSender::run() {
    if (stat_ == Uninit) throw std::runtime_error("OprfSender::run called before init");
    // 1. receive okvs encode status from receiver
    bool okvs_ok = false;
    macoro::sync_wait(chl->recv(okvs_ok));
    if (!okvs_ok) throw std::runtime_error("Receiver reported OKVS encode failed");

    // 2. VOLE send: W ^ V = delta * U
    PRNG mPrng(sysRandomSeed());
    delta = mPrng.get();

    SilentVoleSender<block, block, CoeffCtxGF128> voleSend;
    auto send_task = voleSend.silentSend(delta, W, commPrng, *chl);
    macoro::sync_wait(send_task);

    // 3. receive pp = p ^ U from receiver
    VecB pp_local(m);
    macoro::sync_wait(chl->recv(pp_local));
    macoro::sync_wait(chl->flush());

    // 4. compute WW = delta * pp + W
    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (uint64_t i = 0; i < m; ++i) {
        // WW[i] = ((Galois128(delta) * pp_local[i]) + W[i]).get<block>(0);
        WW[i] = delta.gf128Mul(pp_local[i]) ^ W[i];
    }

    // mark WW ready
    stat_ = OprfStatus::KeyReady;
}

// eval: decode using WW (p-vector) and produce outputs
void OprfSender::eval(const std::vector<block>& in, std::vector<block>& out) {
    if (stat_ != OprfStatus::KeyReady && stat_ != OprfStatus::Evaluated) {
        throw std::runtime_error("OprfSender::eval requires run() to have produced WW");
    }

    // resize output if needed
    out.resize(in.size());

    // seeds = DecodeOtherP(in, WW)
    std::vector<block> seeds(in.size());
    okvs->DecodeOtherP(in, seeds, WW);

    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (size_t i = 0; i < in.size(); ++i) {
        // seeds[i] = (Galois128(delta) * HashToBlock(in[i], hash1) + seeds[i]).get<block>(0);
        // out[i] = HashToBlock(in[i], seeds[i] ^ hash2);
        seeds[i] ^= delta.gf128Mul(mAesFixedKey.hashBlock(in[i]));
        out[i] = mAesFixedKey.hashBlock(in[i] ^ seeds[i]);
    }

    stat_ = OprfStatus::Evaluated;
}

// ---------------- OprfRecver ----------------

void OprfRecver::init(uint64_t n_, uint64_t w_, double e_, std::shared_ptr<coproto::Socket> chl_, block prngSeed) {
    n = n_;
    w = w_;
    e = e_;
    m = static_cast<uint64_t>(std::ceil(static_cast<double>(n) * e));
    chl = chl_;

    // std::cout << "In oprf.init: n="<<n<<",m="<<m<<",e="<<e<<std::endl;

    commPrng.SetSeed(prngSeed);
    commPrng.get(okvsSeed_w, BLAKE3_KEY_LEN);
    hash1 = commPrng.get();
    hash2 = commPrng.get();

    okvs = std::make_unique<OKVSBK>(n, w, e, okvsSeed_w, BLAKE3_KEY_LEN);

    U.resize(m);
    V.resize(m);
    pp.resize(m);

    stat_ = OprfStatus::Init;
}

void OprfRecver::init(OKVSParam param, std::shared_ptr<coproto::Socket> chl_, block prngSeed) {
    n = param.n;
    w = param.w;
    e = param.e;
    m = static_cast<uint64_t>(std::ceil(static_cast<double>(n) * e));
    chl = chl_;

    commPrng.SetSeed(prngSeed);
    commPrng.get(okvsSeed_w, BLAKE3_KEY_LEN);
    hash1 = commPrng.get();
    hash2 = commPrng.get();

    okvs = std::make_unique<OKVSBK>(n, w, e, okvsSeed_w, BLAKE3_KEY_LEN);

    U.resize(m);
    V.resize(m);
    pp.resize(m);

    stat_ = OprfStatus::Init;
}

void OprfRecver::run(const std::vector<block>& keys, std::vector<block>& prf_vals) {
    if (stat_ == Uninit) throw std::runtime_error("OprfRecver::run called before init");
    if (keys.size() != prf_vals.size()) {
        throw std::invalid_argument("keys size mismatch with prf_vals size");
    }

    // 1. Build OKVS (encode)
    size_t size = keys.size();
    std::vector<block> vals(size);
    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (uint64_t i = 0; i < (uint64_t)size; ++i) {
        // vals[i] = HashToBlock(keys[i], this->hash1);
        vals[i] = mAesFixedKey.hashBlock(keys[i]);
    }

    bool okvs_ok = okvs->Encode(keys, vals);
    macoro::sync_wait(chl->send(okvs_ok));
    if (!okvs_ok) throw std::runtime_error("OKVS encode failed");

    // 2. VOLE receive (U,V)
    SilentVoleReceiver<block, block, CoeffCtxGF128> voleRecv;
    auto recv_task = voleRecv.silentReceive(U, V, commPrng, *chl);
    macoro::sync_wait(recv_task);

    // 3. send pp = p ^ U
    auto p = okvs->getP();
    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (uint64_t i = 0; i < m; ++i) {
        pp[i] = p[i] ^ U[i];
    }
    macoro::sync_wait(chl->send(pp));
    macoro::sync_wait(chl->flush());

    // 4. evaluate on keys
    vector<block> seeds(size);
    vector<block> V_vec(V.begin(), V.end());
    okvs->DecodeOtherP(keys, seeds, V_vec);

    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (uint64_t i = 0; i < (uint64_t)size; ++i) {
        // prf_vals[i] = HashToBlock(keys[i], seeds[i] ^ hash2);
        prf_vals[i] = mAesFixedKey.hashBlock(keys[i] ^ seeds[i]);
    }

    // receiver role finished
    stat_ = OprfStatus::Evaluated;
}
