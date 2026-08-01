// common.cpp - shared protocol orchestration for sPSO tests
//
// Implements run_pso(PSOMode), which runs the full pbssPMT-based PSO protocol
// (Figure 11 of "A Leakage-Free Framework for Private Set Operations"):
//
//   sender thread:   cuckoo → OPRF(recver) → OKVS decode → r' → pSEQT(Sender)
//                    → PermCG → OT-send (mode-dependent msg construction)
//   receiver thread: simple hash → OPRF(sender) → OKVS encode → pSEQT(Recver)
//                    → PermCG → OT-recv → mode-dependent validation
//
// The two parties exchange through:
//   * AsioSocket on "localhost:1213" (main data channel)
//   * LocalAsyncSocket pair (PermCG internal channel)
//   * AsioSocket on "localhost:1214" (pSEQT internal; out-of-process in real
//     deployment, in-process here for testing)

#include "common.h"
#include <map>
#include <cstdint>

// Threshold for auto-printing plaintext sets. Default-off for big sets to avoid spamming the terminal.
constexpr uint64_t PRINT_SETS_MAX_N = 128;

// =============================================================================
// Shared setup phase (extracted from run_pso/run_psum).
//
// Per paper Figure 11, all 4 modes (PSI / PSU / PSI-Card / PSI-Sum) share the
// SAME pre-OT pipeline: cuckoo → OPRF → OKVS → pSEQT → PermCG. Only the OT
// msg construction differs. These helpers lift that shared pipeline out of
// run_pso/run_psum so the actual mode logic stays small.
//
// Inputs (sender):   sender_set (size n) + chl + the same commPrng seeds as run_pso.
// Inputs (receiver): recver_set (size n) + chl + the same seeds.
// Output: state needed by OT block — cuco_tab, eq_share_*, pi_s, r_prime.
// =============================================================================

struct SenderSetupOut {
    std::vector<block> cuco_tab;
    std::vector<block> r_prime;
    BitVector          eq_share_sender;
    Perm               pi_s;
};

struct RecverSetupOut {
    BitVector eq_share_recver;
};

static void sender_setup_phase(
    uint64_t n, uint64_t cuco_sz, block cuckooSeed, block oprfSeed,
    const uint8_t* okvsSeed_w, double okvs_exp, uint64_t w,
    const std::vector<uint64_t>& sender_set, coproto::Socket& chl,
    SenderSetupOut& out)
{
    // ---- 1. Cuckoo hashing ----
    //   cuckoo3 uses block(0xff, *) as its default empty-bin marker.
    //   We MUST NOT pass a custom dummy — that would create bins the receiver
    //   can't filter (TESTING_NOTES §6).
    CuckooHash3 cuckoo;
    cuckoo.init(n, cuco_sz, cuckooSeed);
    cuckoo.insert(sender_set);
    out.cuco_tab = cuckoo.get_table();

    // ---- 2. OPRF (sender side: receiver runs OprfRecver, sender runs OprfSender) ----
    // Naming convention from paper: S=R, R=S, so the OPRF receiver is the PSO sender.
    OprfRecver oprfRecv;
    std::vector<block> prf_vals(cuco_sz);
    oprfRecv.init(cuco_sz, w, okvs_exp,
                  std::make_shared<coproto::Socket>(chl), oprfSeed);
    oprfRecv.run(out.cuco_tab, prf_vals);

    // ---- 3. OKVS decode (sender receives p-vector, decodes at cuckoo bins) ----
    uint64_t okvs_n = 3 * n;
    uint64_t okvs_m = std::ceil(okvs_exp * okvs_n);
    OKVSBK okvs(okvs_n, w, okvs_exp, okvsSeed_w, BLAKE3_KEY_LEN);
    std::vector<block> recver_p(okvs_m);
    sync_wait(chl.recv(recver_p));

    std::vector<block> rr(cuco_sz);
    okvs.DecodeOtherP(out.cuco_tab, rr, recver_p);
    out.r_prime.resize(cuco_sz);
    for (uint64_t i = 0; i < cuco_sz; ++i) out.r_prime[i] = rr[i] ^ prf_vals[i];

    // ---- 4. pSEQT (sender side) ----
    eq2<uint8_t> eq_sender(Role::Sender, "localhost:1214", nullptr, nullptr);
    out.eq_share_sender.resize(cuco_sz);
    eq_sender.run(out.r_prime, out.eq_share_sender, 128);

    // diagnostic: exchange share bytes to recover true equality (matches run_pso)
    std::vector<u8> sender_share_bytes(out.eq_share_sender.sizeBytes(), 0);
    memcpy(sender_share_bytes.data(), out.eq_share_sender.data(),
           out.eq_share_sender.sizeBytes());
    sync_wait(chl.send(sender_share_bytes));
    std::vector<u8> recver_share_bytes((cuco_sz + 7) / 8, 0);
    sync_wait(chl.recv(recver_share_bytes));

    BitVector eq_true(cuco_sz);
    for (uint64_t i = 0; i < cuco_sz; i++) {
        u8 sb = (sender_share_bytes[i / 8] >> (i % 8)) & 1;
        u8 rb = (recver_share_bytes[i / 8] >> (i % 8)) & 1;
        eq_true[i] = sb ^ rb;
    }
    uint64_t true_eq_ones = 0;
    for (uint64_t i = 0; i < cuco_sz; i++) true_eq_ones += eq_true[i];
    std::cout << "[sender]   true equality ones = " << true_eq_ones << std::endl;

    // ---- 5. PermCG apply (sender) ----
    coproto::Socket sOleChl = chl.fork();
    PRNG prng_perm_s(block(0, 0));
    out.pi_s = Perm(cuco_sz, prng_perm_s);

    AltModPermGenSender genPermSender;
    CorGenerator sOle;
    sOle.init(std::move(sOleChl), prng_perm_s, /*partyIdx=*/0,
              /*numConcurrent=*/1, /*batchSize=*/1 << 18, /*mock=*/false);
    genPermSender.init(cuco_sz, /*rowSize=*/1, sOle);
    PermCorSender sPerm;

    sync_wait(when_all_ready(
        genPermSender.generate(out.pi_s, prng_perm_s, chl, sPerm),
        sOle.start()
    ));
    std::cout << "[sender] PermCG generated, size=" << sPerm.size() << std::endl;

    oc::Matrix<u8> sender_in(cuco_sz, 1);
    oc::Matrix<u8> sender_out(cuco_sz, 1);
    for (uint64_t i = 0; i < cuco_sz; i++) {
        sender_in(i, 0) = (out.eq_share_sender.data()[i / 8] >> (i % 8)) & 1;
    }
    sync_wait(sPerm.apply<u8>(PermOp::Regular, sender_in, sender_out, chl));

    memset(out.eq_share_sender.data(), 0, out.eq_share_sender.sizeBytes());
    for (uint64_t i = 0; i < cuco_sz; i++) {
        out.eq_share_sender[i] = sender_out(i, 0) & 1;
    }
}

static void recver_setup_phase(
    uint64_t n, uint64_t cuco_sz, block cuckooSeed, block oprfSeed,
    const uint8_t* okvsSeed_w, double okvs_exp, uint64_t w,
    const std::vector<uint64_t>& recver_set, coproto::Socket& chl,
    RecverSetupOut& out)
{
    // ---- 1. simple hash + OKVS keys ----
    std::vector<block> okvs_key(3 * n);
    std::vector<block> okvs_value(3 * n);
    std::vector<std::array<uint64_t, 3>> pos(n);
    #ifdef HAVE_OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (uint64_t i = 0; i < n; ++i) {
        pos[i] = get3hash(recver_set[i], cuco_sz, cuckooSeed);
        okvs_key[i * 3 + 0] = block(recver_set[i], blake3_hash64((uint64_t)0x1, cuckooSeed));
        okvs_key[i * 3 + 1] = block(recver_set[i], blake3_hash64((uint64_t)0x2, cuckooSeed));
        okvs_key[i * 3 + 2] = block(recver_set[i], blake3_hash64((uint64_t)0x3, cuckooSeed));
    }

    // ---- 2. OPRF (sender side: receiver runs OprfSender) ----
    OprfSender oprfSend;
    oprfSend.init(cuco_sz, w, okvs_exp,
                  std::make_shared<coproto::Socket>(chl), oprfSeed);
    oprfSend.run();
    oprfSend.eval(okvs_key, okvs_value);

    // ---- 3. OKVS encode + send p-vector ----
    std::vector<block> r(cuco_sz);
    PRNG prng_once(block(233));
    for (auto &x : r) x = prng_once.get<block>();
    for (uint64_t i = 0; i < n; ++i) {
        okvs_value[i * 3 + 0] ^= r[pos[i][0]];
        okvs_value[i * 3 + 1] ^= r[pos[i][1]];
        okvs_value[i * 3 + 2] ^= r[pos[i][2]];
    }
    uint64_t okvs_n = 3 * n;
    OKVSBK okvs(okvs_n, w, okvs_exp, okvsSeed_w, BLAKE3_KEY_LEN);
    okvs.Encode(okvs_key, okvs_value);
    std::vector<block> p = okvs.getP();
    sync_wait(chl.send(p));

    // ---- 4. pSEQT (receiver side) ----
    eq2<uint8_t> eq_recver(Role::Receiver, "localhost:1214", nullptr, nullptr);
    out.eq_share_recver.resize(cuco_sz);
    eq_recver.run(r, out.eq_share_recver, 128);

    // diagnostic: exchange shares
    std::vector<u8> sender_share_bytes((cuco_sz + 7) / 8, 0);
    sync_wait(chl.recv(sender_share_bytes));
    std::vector<u8> recver_share_bytes(out.eq_share_recver.sizeBytes(), 0);
    memcpy(recver_share_bytes.data(), out.eq_share_recver.data(),
           out.eq_share_recver.sizeBytes());
    sync_wait(chl.send(recver_share_bytes));

    BitVector eq_true(cuco_sz);
    for (uint64_t i = 0; i < cuco_sz; i++) {
        u8 sb = (sender_share_bytes[i / 8] >> (i % 8)) & 1;
        u8 rb = (recver_share_bytes[i / 8] >> (i % 8)) & 1;
        eq_true[i] = sb ^ rb;
    }
    uint64_t true_eq_ones = 0;
    for (uint64_t i = 0; i < cuco_sz; i++) true_eq_ones += eq_true[i];
    std::cout << "[receiver]   true equality ones = " << true_eq_ones << std::endl;

    // ---- 5. PermCG apply (receiver) ----
    PRNG prng_perm_r(block(0, 1));
    AltModPermGenReceiver genPermRecver;
    CorGenerator rOle;
    coproto::Socket rOleChl = chl.fork();
    rOle.init(std::move(rOleChl), prng_perm_r, /*partyIdx=*/1,
              /*numConcurrent=*/1, /*batchSize=*/1 << 18, /*mock=*/false);
    genPermRecver.init(cuco_sz, /*rowSize=*/1, rOle);
    PermCorReceiver rPerm;

    sync_wait(when_all_ready(
        genPermRecver.generate(prng_perm_r, chl, rPerm),
        rOle.start()
    ));
    std::cout << "[receiver] PermCG generated, size=" << rPerm.size() << std::endl;

    oc::Matrix<u8> recver_in(cuco_sz, 1);
    oc::Matrix<u8> recver_out(cuco_sz, 1);
    for (uint64_t i = 0; i < cuco_sz; i++) {
        recver_in(i, 0) = (out.eq_share_recver.data()[i / 8] >> (i % 8)) & 1;
    }
    sync_wait(rPerm.apply<u8>(PermOp::Regular, recver_in, recver_out, chl));

    memset(out.eq_share_recver.data(), 0, out.eq_share_recver.sizeBytes());
    for (uint64_t i = 0; i < cuco_sz; i++) {
        out.eq_share_recver[i] = recver_out(i, 0) & 1;
    }
}

void run_pso(PSOMode mode, bool print_sets,
             std::vector<uint64_t> payload, uint64_t p_in, uint64_t q_in) {
    // ---------------------------- parameters ----------------------------
    const uint64_t n          = DEFAULT_N;
    const uint64_t inter      = DEFAULT_INTER;
    const double   okvs_exp   = DEFAULT_OKVS_EXP;
    const uint64_t w          = DEFAULT_OKVS_W;
    const double   cuckoo_exp = CUCKOO_EXPANSION;       // 1.22
    const uint64_t cuco_sz    = std::ceil(cuckoo_exp * n);

    // Captured protocol results (filled by the receiver thread for plaintext dump)
    std::set<uint64_t> captured_intersection;       // PSI  mode: receiver's X ∩ Y
    std::set<uint64_t> captured_kept;               // PSU  mode: receiver's X \ Y (kept_set)
    std::set<uint64_t> captured_union;              // PSU  mode: receiver's X ∪ Y (= R ∪ kept)

    // Secret-Shared PSI (§5.2): each party holds a share vector of size cuco_sz.
    //   R holds the OT output z_i (the msg chosen by their bit e_r^i).
    //   S holds the random mask vector r_i used to construct msg_i^b.
    //   σ[r][i] = z[i] ⊕ r[i] = 0                   if bin i is not intersection (e_s ⊕ e_r = 0)
    //   σ[r][i] = z[i] ⊕ r[i] = X*[π(i)]  (real)    if bin i is intersection (e_s ⊕ e_r = 1)
    // In this in-process test we just XOR z ⊕ r in main scope and verify the recovered
    // multiset equals X ∩ Y (plaintext). In a real deployment each party keeps its own
    // share and would combine with the partner's share only under protocol-specific use
    // (e.g., for a downstream homomorphic computation over the shares).
    std::vector<block>      ss_r_captured;
    std::vector<block>      ss_z_captured;
    bool                    ss_match = false;

    // ---------------------------- PRNGs ---------------------------------
    PRNG commPrng(block(123456));
    block setGenSeed  = commPrng.get();
    block cuckooSeed  = commPrng.get();
    // (uint64_t cuckooDummy removed — see TESTING_NOTES §6; cuckoo3 default dummy = 0xff)
    block oprfSeed    = commPrng.get();
    uint8_t okvsSeed_w[BLAKE3_KEY_LEN];
    commPrng.get(okvsSeed_w, BLAKE3_KEY_LEN);
    PRNG otPrng(sysRandomSeed());

    // ---------------------------- sets ----------------------------------
    //   S = [0, n)         \\\\  (TESTING_NOTES §1: was [1, n) → off-by-one, intersection = inter+1)
    //   R = [n-inter, 2n-inter)
    //   ⇒ |S ∩ R| = inter   (verified)
    vector<uint64_t> sender_set(n), recver_set(n);
    set_gen(n, (uint64_t)0,        sender_set, setGenSeed);
    set_gen(n, n - inter,          recver_set, setGenSeed);

    cout << "=================== Mode: " << mode_name(mode)
         << " | n=" << n << ", inter=" << inter
         << " ===================" << endl;

    cout << "set size=" << n
         << ", cuckoo expansion rate=" << cuckoo_exp
         << ", okvs expansion rate=" << okvs_exp
         << ", okvs w=" << w << "\n\n";

    // =====================================================================
    // Mode-specific pre-thread setup.
    //
    // Only MODE_PSI_SUM needs additional state: payload vec, payload/sum
    // moduli, and the expected sum (printed for sanity). Other modes skip
    // this block. By isolating it up front, both threads can capture
    // payload/p/q uniformly and the OT/validation lambdas only differ on
    // mode switch.
    // =====================================================================
    const uint64_t p = (p_in == 0) ? DEFAULT_PAYLOAD_P : p_in;
    const uint64_t q = (q_in == 0) ? DEFAULT_PAYLOAD_Q : q_in;
    uint64_t expected_sum_mod_q = 0;
    uint64_t captured_sum        = 0;
    bool     sum_match           = false;
    if (mode == MODE_PSI_SUM) {
        if (q <= p) {
            throw std::runtime_error("run_pso(MODE_PSI_SUM): require q > p (paper: q > n·p)");
        }
        if (payload.empty()) {
            // Generate deterministic payload via PRNG (same chain as set_gen).
            // start=0 to match the S/R convention from set_gen above (TESTING_NOTES §1).
            block payloadSeed = commPrng.get();
            payload.resize(n);
            set_gen(n, (uint64_t)0, payload, payloadSeed);
            for (auto& v : payload) v %= p;
        } else {
            if (payload.size() != n) {
                throw std::invalid_argument(
                    "run_pso(MODE_PSI_SUM): payload.size() must equal sender set size n");
            }
            for (auto& v : payload) v %= p;
        }
        // Expected sum from plaintext intersection (printed once, used by receiver for verdict)
        std::set<uint64_t> recver_set_for_expected(recver_set.begin(), recver_set.end());
        for (uint64_t i = 0; i < n; ++i) {
            if (recver_set_for_expected.count(sender_set[i])) {
                expected_sum_mod_q = (expected_sum_mod_q + payload[i]) % q;
            }
        }
        cout << "[psum] expected sum  = " << expected_sum_mod_q
             << "  (= Σ_{x ∈ X∩Y} val_x mod q, q = " << q << ")" << endl;
    }

    // PermCG uses LocalAsyncSocket pair (created in main, captured by move)
    auto [sChl, rChl] = coproto::LocalAsyncSocket::makePair();

    // ============================== sender ==============================
    std::thread th_sender([&, sChl = std::move(sChl)]() mutable {
        try {
            auto chl = asioConnect("localhost:1213", 0);

            double total_time = 0;
            double total_comm = 0;

            // ---- shared setup phase (cuckoo + OPRF + OKVS + pSEQT + PermCG) ----
            SenderSetupOut sout;
            sender_setup_phase(n, cuco_sz, cuckooSeed, oprfSeed, okvsSeed_w,
                               okvs_exp, w, sender_set, chl, sout);
            const std::vector<block>& cuco_tab        = sout.cuco_tab;
            const std::vector<block>& r_prime         = sout.r_prime;
            const BitVector&          eq_share_sender = sout.eq_share_sender;
            const Perm&               pi_s            = sout.pi_s;

            // ---- 6. OT send (mode-dependent msg construction) ----

            SilentOtExtSender sOTSender;
            sOTSender.configure(cuco_sz, 2, 1);

            auto bOtTask = sOTSender.genBaseOts(otPrng, chl);
            sync_wait(bOtTask);

            std::vector<std::array<block, 2>> msg2(cuco_sz);
            const block zero(0, 0);

            // Pre-compute PSI-Sum specific state (V_*, r_i, r' sent to R) once,
            // before the OT msg loop. The actual msg fill is in the per-bin loop below.
            vector<uint64_t> V_star;
            vector<uint64_t> r_i;
            if (mode == MODE_PSI_SUM) {
                // Build V_* aligned to cuckoo bins:
                //   V_*[σ(i)] = payload[i]  for real elements; V_*[dummy bin] = 0.
                V_star.assign(cuco_sz, 0);
                for (uint64_t bin = 0; bin < cuco_sz; ++bin) {
                    uint64_t elem_at_bin = cuco_tab[bin].get<uint64_t>(1);
                    // cuckoo3 uses block(0xff, ...) as its default empty-bin marker
                    // (see TESTING_NOTES §6). All real elements have hi byte < 0xff.
                    bool is_dummy = ((elem_at_bin >> 56) & 0xff) == 0xff;
                    if (is_dummy) continue;
                    for (uint64_t i = 0; i < n; ++i) {
                        if (sender_set[i] == elem_at_bin) {
                            V_star[bin] = payload[i];
                            break;
                        }
                    }
                }
                // Pick r_i ← Z_q for each cuckoo bin; aggregate r' = Σ r_i mod q; send once.
                PRNG rPrng(commPrng.get());
                r_i.resize(cuco_sz);
                uint64_t r_prime_send = 0;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    r_i[i] = rPrng.get<uint64_t>() % q;
                    r_prime_send = (r_prime_send + r_i[i]) % q;
                }
                sync_wait(chl.send(r_prime_send));
                cout << "[sender] PSI-Sum: r' = " << r_prime_send
                     << " (sum of " << cuco_sz << " samples mod q)" << endl;
            }

            // Pre-compute Secret-Shared PSI specific state: r_i ∈ {0,1}^ℓ1 per bin.
            // These are S's shares of the intersection (rounded XOR-side), XORed with
            // (b ⊕ e_s)·cuco before being sent as OT msgs.
            vector<block> ss_r;       // kept in scope until after msg fill
            if (mode == MODE_SS_PSI) {
                PRNG ssPrng(commPrng.get());
                ss_r.resize(cuco_sz);
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    ss_r[i] = ssPrng.get<block>();
                }
            }

            // Figure 11 message construction per mode:
            //   PSI     : msg_b = ⊥ ⊕ (b ⊕ u) · cuco[π(i)]
            //             u=0: msg_0=⊥, msg_1=cuco   u=1: msg_0=cuco, msg_1=⊥
            //   PSU     : msg_b = ⊥ ⊕ (1 ⊕ b ⊕ u) · cuco[π(i)]   (swapped vs PSI)
            //             u=0: msg_0=cuco, msg_1=⊥    u=1: msg_0=⊥, msg_1=cuco
            //   CARD    : msg_b = b ⊕ u   (single bit, encoded in block)
            //             u=0: msg_0=0, msg_1=1        u=1: msg_0=1, msg_1=0
            //   PSUM    : msg_b = r_i + (b ⊕ u) · V_[π(i)]   (mod q)   — arithmetic, see paper §5
            //             u=0: msg_0=r_i, msg_1=r_i+V   u=1: msg_0=r_i+V, msg_1=r_i
            //   SS-PSI  : msg_b = r_i ⊕ (b ⊕ u) · cuco[π(i)]  — secret-shared, see paper §5.2
            //             u=0: msg_0=r_i⊕cuco, msg_1=r_i  u=1: msg_0=r_i, msg_1=r_i⊕cuco
            //             → R's choice bit v = e_r; share R holds = msg_v;
            //                share S holds = r_i; for bin i with e_s ⊕ e_r = 1:
            //                z_i ⊕ r_i = cuco[π(i)] (= intersection element value)
            //                for bin i with e_s ⊕ e_r = 0:
            //                z_i ⊕ r_i = 0 (= "share of zero" sentinel).
            for (uint64_t i = 0; i < cuco_sz; i++) {
                const bool u = eq_share_sender[i];
                if (mode == MODE_PSI_SUM) {
                    // arithmetic msg (encoded in block hi bits; lo = 0)
                    const uint64_t V_val = V_star[pi_s[i]];
                    uint64_t m0 = (r_i[i] + (u ? V_val : 0)) % q;
                    uint64_t m1 = (r_i[i] + (u ? 0 : V_val)) % q;
                    msg2[i][0] = block(m0, 0);
                    msg2[i][1] = block(m1, 0);
                } else if (mode == MODE_SS_PSI) {
                    // XOR-share msg: each side of the OT holds half the share
                    const block cuco_val = cuco_tab[pi_s[i]];
                    msg2[i][0] = ss_r[i] ^ (u ? cuco_val : zero);
                    msg2[i][1] = ss_r[i] ^ (u ? zero : cuco_val);
                } else {
                    const block cuco_val = cuco_tab[pi_s[i]];
                    if (mode == MODE_PSI) {
                        msg2[i][0] = u ? cuco_val : zero;
                        msg2[i][1] = u ? zero : cuco_val;
                    } else if (mode == MODE_PSU) {
                        msg2[i][0] = u ? zero : cuco_val;
                        msg2[i][1] = u ? cuco_val : zero;
                    } else { // MODE_CARD
                        // CARD: msg_b = (b ⊕ u),跟 PSI 一样的 XOR 模式
                        // receiver 拿 v:msg_v = v ⊕ u = eq_true,sum = inter
                        // 把 bit 放在 lo 位(reader  get<uint64_t>(0) 读 lo)
                        msg2[i][0] = block(0, u ? 1 : 0);  // msg_0 = u
                        msg2[i][1] = block(0, u ? 0 : 1);  // msg_1 = NOT u
                    }
                }
            }
            // For SS-PSI: capture S's share vector r so main scope can do r ⊕ z for verification.
            // (Done after the OT loop and before flushing the channel so we don't lose scope on ss_r.)
            if (mode == MODE_SS_PSI) {
                ss_r_captured = std::move(ss_r);
            }
            auto silentOtTask = sOTSender.sendChosen(msg2, otPrng, chl);
            sync_wait(silentOtTask);

            sync_wait(chl.flush());
            sync_wait(chl.close());

            cout << "\n\n[sender] total running time: " << total_time
                 << " ms, total comm = " << total_comm << " MB" << endl;

        } catch (std::exception& ex) {
            cerr << "Sender exception: " << ex.what() << endl;
        }
    });

    // ============================ receiver ==============================
    std::thread th_recver([&, rChl = std::move(rChl)]() mutable {
        try {
            auto chl = asioConnect("localhost:1213", 1);

            double total_time = 0;
            double total_comm = 0;

            // ---- shared setup phase (simple hash + OPRF + OKVS + pSEQT + PermCG) ----
            RecverSetupOut rout;
            recver_setup_phase(n, cuco_sz, cuckooSeed, oprfSeed, okvsSeed_w,
                               okvs_exp, w, recver_set, chl, rout);
            const BitVector& eq_share_recver = rout.eq_share_recver;

            // ---- 6. OT receive (mode-dependent interpretation) ----

            SilentOtExtReceiver sOTRecver;
            sOTRecver.configure(cuco_sz, 2, 1);

            auto sOtTask = sOTRecver.genBaseOts(otPrng, chl);
            sync_wait(sOtTask);

            std::vector<block> msg(cuco_sz);
            BitVector choice = eq_share_recver;   // v_i as choice bits

            // PSI-Sum: receive r' = Σ r_i mod q before consuming OT msgs
            uint64_t r_prime_recv = 0;
            if (mode == MODE_PSI_SUM) {
                sync_wait(chl.recv(r_prime_recv));
                cout << "[receiver] PSI-Sum: r' = " << r_prime_recv << endl;
            }

            auto silentOtTask = sOTRecver.receiveChosen(choice, msg, otPrng, chl);
            sync_wait(silentOtTask);

            // ---- 7. mode-dependent validation ----
            std::set<uint64_t> sender_set_s(sender_set.begin(), sender_set.end());
            std::set<uint64_t> recver_set_s(recver_set.begin(), recver_set.end());

            if (mode == MODE_PSI) {
                // Existing PSI validation: non-zero msg ⇒ element ∈ S ∩ R
                uint64_t inter_seen = 0, false_positive = 0;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    if (msg[i] != block(0, 0)) {
                        inter_seen++;
                        uint64_t elem = msg[i].get<uint64_t>(1);
                        bool in_s = sender_set_s.count(elem) > 0;
                        bool in_r = recver_set_s.count(elem) > 0;
                        if (!in_s || !in_r) false_positive++;
                        if (in_s && in_r) captured_intersection.insert(elem);
                    }
                }
                cout << "[receiver] ★ PSI OT msg verification:" << endl;
                cout << "  non-zero count         = " << inter_seen
                     << "  (期望 = inter = " << inter << ")" << endl;
                cout << "  false positives        = " << false_positive
                     << "  (期望 = 0)" << endl;
                cout << "  inter missing (差值)   = "
                     << ((int64_t)inter - (int64_t)inter_seen)
                     << "  (期望 ≈ 0)" << endl;
                cout << "  sample 5 non-zero msgs:" << endl;
                uint64_t shown = 0;
                for (uint64_t i = 0; i < cuco_sz && shown < 5; ++i) {
                    if (msg[i] != block(0, 0)) {
                        cout << "    msg[" << i << "] element = 0x" << std::hex
                             << msg[i].get<uint64_t>(1) << std::dec << endl;
                        shown++;
                    }
                }
                cout.flush();

            } else if (mode == MODE_PSU) {
                // PSU validation: protocol requires Z := Z ∪ {z_i} if z_i ≠ d
                // (filter out cuckoo dummy items - they live in empty bins)
                // cuckoo3 init: dummyB = block(0xff, 0); replace_with_keys fills empty bins
                //   with block(0xff, blake3_hash64(pos, seed)) — hi byte always 0xff.
                // Figure 11: msg ∈ {⊥, X*[π(i)]}. Non-⊥ values are either real X elements
                //   (block(element, hash_idx)) or dummies (block(0xff, *)). Distinguish via hi.
                constexpr uint64_t DUMMY_HI = 0xff;  // cuckoo3 default dummy
                uint64_t x_minus_y_count = 0, false_pos = 0;
                std::set<uint64_t> kept_set;
                // DEBUG: 分桶收集,小 n 时打印明文
                std::vector<uint64_t> kept_in_s, kept_in_r_only, false_pos_missing_s, false_pos_in_r;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    uint64_t hi = msg[i].get<uint64_t>(1);
                    if (hi == DUMMY_HI) continue;     // empty bin (dummy) — protocol discards
                    if (msg[i] == block(0, 0)) continue;  // ⊥ when es ⊕ er = 1 (intersection)
                    x_minus_y_count++;
                    uint64_t elem = hi;
                    bool in_s = sender_set_s.count(elem) > 0;
                    bool in_r = recver_set_s.count(elem) > 0;
                    if (in_s && !in_r) kept_in_s.push_back(elem);          // 真 X\Y 元素
                    if (!in_s)        false_pos_missing_s.push_back(elem); // 不在 S,应该是 bug
                    if (in_r)         false_pos_in_r.push_back(elem);      // 在 R,应该是 bug
                    if (!in_s) false_pos++;        // shouldn't miss from S
                    if (in_r)  false_pos++;        // shouldn't be in R
                    kept_set.insert(elem);
                }

                // DEBUG: 小 n 时打印完整明文,方便定位
                if (n <= 1024) {
                    cout << "[debug] sender_set (S) size = " << sender_set_s.size() << endl;
                    uint64_t dump = 0;
                    for (auto x : sender_set) {
                        cout << "  S 0x" << std::hex << x << std::dec;
                        if (++dump >= 8) { cout << endl; break; }
                    }
                    if (dump >= 8) cout << "  ... (truncated)" << endl;
                    cout << "[debug] recver_set (R) size = " << recver_set_s.size() << endl;
                    dump = 0;
                    for (auto x : recver_set) {
                        cout << "  R 0x" << std::hex << x << std::dec;
                        if (++dump >= 8) { cout << endl; break; }
                    }
                    if (dump >= 8) cout << "  ... (truncated)" << endl;
                    cout << "[debug] expected X\\Y count = " << (n - inter) << endl;
                    cout << "[debug] KEPT in S\\\\R (true X\\\\Y): " << kept_in_s.size() << endl;
                    for (auto e : kept_in_s) cout << "    ok  0x" << std::hex << e << std::dec << endl;
                    cout << "[debug] FALSE_POS missing S (not in sender_set): " << false_pos_missing_s.size() << endl;
                    for (auto e : false_pos_missing_s) cout << "    !!  0x" << std::hex << e << std::dec << " (in R=" << recver_set_s.count(e) << ")" << endl;
                    cout << "[debug] FALSE_POS in R (shouldn't be in X\\\\Y): " << false_pos_in_r.size() << endl;
                    for (auto e : false_pos_in_r) cout << "    !!  0x" << std::hex << e << std::dec << " (in S=" << sender_set_s.count(e) << ")" << endl;
                }
                std::set<uint64_t> union_expected(sender_set.begin(), sender_set.end());
                union_expected.insert(recver_set.begin(), recver_set.end());
                std::set<uint64_t> union_actual(recver_set.begin(), recver_set.end());
                union_actual.insert(kept_set.begin(), kept_set.end());
                bool union_ok = (union_expected == union_actual);

                // Capture for plaintext dump
                captured_kept = kept_set;
                captured_union = union_actual;

                cout << "[receiver] ★ PSU OT msg verification:" << endl;
                cout << "  X\\Y elements seen      = " << x_minus_y_count
                     << "  (期望 = n - inter = " << (n - inter) << ")" << endl;
                cout << "  false positives        = " << false_pos
                     << "  (期望 = 0: each kept ∈ S ∧ kept ∉ R)" << endl;
                cout << "  X∪Y match (Y ∪ Z == S ∪ R) = "
                     << (union_ok ? "YES" : "NO") << endl;
                cout << "  sample 5 non-zero msgs:" << endl;
                uint64_t shown = 0;
                for (uint64_t i = 0; i < cuco_sz && shown < 5; ++i) {
                    if (msg[i] != block(0, 0)) {
                        cout << "    msg[" << i << "] element = 0x" << std::hex
                             << msg[i].get<uint64_t>(1) << std::dec << endl;
                        shown++;
                    }
                }
                cout.flush();

            } else if (mode == MODE_CARD) {
                // PSI-Card validation: HammingWeight of low bit == |X ∩ Y|
                uint64_t card = 0;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    card += (msg[i].get<uint64_t>(0) & 1);
                }
                cout << "[receiver] ★ PSI-Card verification:" << endl;
                cout << "  cardinality            = " << card
                     << "  (期望 = inter = " << inter << ")" << endl;
                cout << "  delta (card - inter)   = "
                     << ((int64_t)card - (int64_t)inter) << endl;
            } else if (mode == MODE_PSI_SUM) {
                // PSI-Sum validation: sum' = Σ z_i mod q; sum = (sum' - r') mod q.
                // Per paper Figure 11, this sum equals Σ_{x ∈ X∩Y} val_x (mod q).
                uint64_t sum_prime = 0;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    sum_prime = (sum_prime + msg[i].get<uint64_t>(1)) % q;
                }
                uint64_t sum_recovered = (sum_prime + q - r_prime_recv) % q;

                cout << "[receiver] ★ PSI-Sum verification:" << endl;
                cout << "  sum'  (Σ z_i mod q)   = " << sum_prime << endl;
                cout << "  r'                     = " << r_prime_recv << endl;
                cout << "  sum recovered          = " << sum_recovered << endl;
                cout << "  sum expected           = " << expected_sum_mod_q << endl;
                cout << "  match                  = "
                     << (sum_recovered == expected_sum_mod_q ? "YES" : "NO") << endl;

                captured_sum = sum_recovered;
                sum_match    = (sum_recovered == expected_sum_mod_q);
            } else if (mode == MODE_SS_PSI) {
                // Secret-Shared PSI: receiver only stores its share vector (the OT msgs
                // they received, indexed by bin i). Main scope combines z ⊕ r and
                // verifies recovered intersection equals expected X ∩ Y.
                ss_z_captured = msg;

                // Quick self-check: for each bin i, share_i ⊕ r_i (once S's r is combined)
                // should be 0 (mismatch) or a real intersection element.
                // Count expected & actual locally — main does the final reconciliation.
                uint64_t dummy_count = 0;
                uint64_t zero_count = 0;
                for (uint64_t i = 0; i < cuco_sz; ++i) {
                    uint64_t hi = msg[i].get<uint64_t>(1);
                    if (hi == 0) zero_count++;
                    if ((hi >> 56) == 0xff) dummy_count++;
                }
                cout << "[receiver] ★ SS-PSI recv:" << endl;
                cout << "  cuco_sz              = " << cuco_sz << endl;
                cout << "  msg blocks w/ hi=0   = " << zero_count << endl;
                cout << "  msg blocks w/ hi=0xff (dummy) = " << dummy_count << endl;
            }

            cout << "\n\n[receiver] total running time: " << total_time
                 << " ms, total comm = " << total_comm << " MB" << endl;

            sync_wait(chl.flush());
            sync_wait(chl.close());

        } catch (std::exception& ex) {
            cerr << "Receiver exception: " << ex.what() << endl;
        }
    });

    th_sender.join();
    th_recver.join();

    // =====================================================================
    // Secret-Shared PSI cross-party verification (paper §5.2)
    //
    // The actual protocol output is: each party holds a share vector
    //   S holds r (size cuco_sz),  R holds z (size cuco_sz).
    // Only the XOR z ⊕ r reveals which bins are intersection (and what the
    // intersection element value is). In this in-process test we combine
    // both shares in main scope and verify the recovered multiset equals
    // X ∩ Y (plaintext). In real deployment each party keeps its share
    // private; combining happens only under app-specific sharing semantics.
    // =====================================================================
    if (mode == MODE_SS_PSI) {
        std::set<uint64_t> recovered;
        uint64_t nonzero_bins  = 0;
        uint64_t dummy_bins    = 0;
        uint64_t zero_bins     = 0;
        for (uint64_t i = 0; i < cuco_sz; ++i) {
            block share = ss_z_captured[i] ^ ss_r_captured[i];
            if (share == block(0, 0)) {
                zero_bins++;
                continue;
            }
            uint64_t hi = share.get<uint64_t>(1);
            if ((hi >> 56) == 0xff) {
                dummy_bins++;        // shouldn't happen at intersection bins
                continue;
            }
            recovered.insert(hi);
            nonzero_bins++;
        }
        std::set<uint64_t> expected_inter;
        std::set<uint64_t> recver_set_s(recver_set.begin(), recver_set.end());
        for (auto x : sender_set) if (recver_set_s.count(x)) expected_inter.insert(x);

        ss_match = (recovered == expected_inter);

        cout << "online work flow finished. [" << mode_name(mode) << "]" << endl;
        cout << "  nonzero share bins (intersection candidates) = " << nonzero_bins << endl;
        cout << "  zero share bins (non-intersection)            = " << zero_bins << endl;
        cout << "  dummy-marked share bins (sanity)              = " << dummy_bins
             << "  (expected = 0)" << endl;
        cout << "  |X ∩ Y| recovered (distinct elements)         = " << recovered.size()
             << "  (expected = " << expected_inter.size() << ")" << endl;
        cout << "  match                                          = "
             << (ss_match ? "YES" : "NO") << endl;

        captured_intersection = recovered;   // reuse the existing captured set for plaintext dump
    } else {
        cout << "online work flow finished. [" << mode_name(mode) << "]" << endl;
    }

    // ---------------------------- plaintext dump (eyeball verification) ----------------------------
    // Auto-on when n ≤ 128; otherwise print a notice (use --print-sets to force).
    // CARD mode skips plaintext dump regardless of flags (cardinality is the only output).
    const bool small_n = (n <= PRINT_SETS_MAX_N);
    const bool want_print = (print_sets || small_n) && mode != MODE_CARD;
    if (print_sets && mode != MODE_CARD) {
        cout << "[info] --print-sets: forcing plaintext dump (n=" << n << ")" << endl;
    }
    if (want_print) {
        cout << "\n=== PLAINTEXT PROTOCOL RESULTS (eyeball verification) ===" << endl;
        print_set_hex(sender_set, "S: sender_set");
        print_set_hex(recver_set, "R: recver_set");
        if (mode == MODE_PSI) {
            print_set_hex(captured_intersection, "X\u2229Y: intersection (receiver learned)");
            // Expected X∩Y for cross-check
            std::set<uint64_t> recver_set_s(recver_set.begin(), recver_set.end());
            std::set<uint64_t> expected_inter;
            for (auto x : sender_set) if (recver_set_s.count(x)) expected_inter.insert(x);
            print_set_hex(expected_inter, "X\u2229Y: expected (S \u2229 R)");
            cout << "(match = " << (expected_inter == captured_intersection ? "YES" : "NO") << ")" << endl;
        } else if (mode == MODE_PSU) {
            print_set_hex(captured_kept,    "X\\Y: kept (receiver learned X minus R)");
            print_set_hex(captured_union,   "X\u222aY: union (receiver learned = R + kept)");
            // Expected X∪Y
            std::set<uint64_t> expected_union(sender_set.begin(), sender_set.end());
            expected_union.insert(recver_set.begin(), recver_set.end());
            print_set_hex(expected_union, "X\u222aY: expected (S \u222a R)");
            cout << "(match = " << (expected_union == captured_union ? "YES" : "NO") << ")" << endl;
        } else if (mode == MODE_SS_PSI) {
            // SS-PSI: dump recovered intersection (from z ⊕ r above) and compare to plaintext expected
            std::set<uint64_t> recver_set_s(recver_set.begin(), recver_set.end());
            std::set<uint64_t> expected_inter;
            for (auto x : sender_set) if (recver_set_s.count(x)) expected_inter.insert(x);
            print_set_hex(captured_intersection, "X\u2229Y: recovered (share-z \u2295 share-r)");
            print_set_hex(expected_inter,       "X\u2229Y: expected (S \u2229 R)");
            cout << "(match = " << (expected_inter == captured_intersection ? "YES" : "NO") << ")" << endl;
        } else if (mode == MODE_PSI_SUM) {
            cout << "--- PSI-Sum verdict ---" << endl;
            cout << "  sum (R learned)   = " << captured_sum << endl;
            cout << "  sum (expected)    = " << expected_sum_mod_q << endl;
            cout << "  match             = " << (sum_match ? "YES" : "NO") << endl;
            cout << "  verdict           = " << (sum_match ? "PASS" : "FAIL") << endl;
            // Also dump per-element payload for eyeball diff
            size_t show = std::min<size_t>(8, n);
            for (size_t i = 0; i < show; ++i) {
                cout << "    X[" << i << "] = 0x" << std::hex << std::setw(16)
                     << std::setfill('0') << sender_set[i] << std::dec
                     << "   val[" << i << "] = " << payload[i] << endl;
            }
            if (n > show) cout << "    ... (" << (n - show) << " more)" << endl;
        }
        // MODE_CARD: skip (Polaris's instruction — cardinality is the only output)
        cout << "==========================================================\n" << endl;
    } else if (mode == MODE_CARD && (print_sets || small_n)) {
        // CARD mode never prints plaintext regardless of --print-sets
    } else if (mode != MODE_CARD && !print_sets && n > PRINT_SETS_MAX_N) {
        cout << "[info] n=" << n << " > " << PRINT_SETS_MAX_N
             << ", skipping plaintext dump (use --print-sets to force)" << endl;
    }
}

// ============================================================
