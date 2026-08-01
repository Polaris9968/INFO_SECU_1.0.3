// test_ss_psi.cpp - Secret-Shared PSI mode standalone binary
//
// Runs the pbssPMT-based Secret-Shared PSI protocol. Both parties end up
// holding share vectors:
//   * S holds ⃗r  (size cuco_sz, randomly chosen)
//   * R holds ⃗z  (size cuco_sz, OT outputs chosen by their eq bits)
//
// The actual X ∩ Y is recoverable only when both parties combine their shares
// (typically over a downstream computation that respects sharing semantics).
// In this in-process test we combine in main scope and verify the recovered
// multiset equals X ∩ Y (plaintext).
//
// Paper: §5.2 / Figure 11 of "A Leakage-Free Framework for Private Set Operations".
//
// Usage:
//   ./test_ss_psi                 # in-process sender+receiver (testing)
//   ./test_ss_psi --print-sets    # also dump X / Y / recovered intersection plaintext

#include "common.h"

int main(int argc, char** argv) {
    bool print_sets = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--print-sets") print_sets = true;
    }
    run_pso(MODE_SS_PSI, print_sets);
    return 0;
}
