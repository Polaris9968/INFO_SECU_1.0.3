// test_psum.cpp - PSI-Sum mode standalone binary
//
// Runs the pbssPMT-based PSI-Sum protocol. The receiver learns
//   sum = Σ_{x ∈ X ∩ Y} val_x  (mod q),
// where val_x is the per-element payload carried by the sender.
//
// This is one of four functionalities of the single sPSO protocol
// (PSI / PSU / PSI-Card / PSI-Sum). All four are dispatched from
// the unified run_pso(mode, ...) entry point in common.cpp; this
// binary just calls it with MODE_PSI_SUM.
//
// Paper: Figure 11 of "A Leakage-Free Framework for Private Set Operations".
//
// Usage:
//   ./test_psum                 # in-process sender+receiver (testing)
//   ./test_psum --print-sets    # also dump X / Y / payload / sum plaintext

#include "common.h"

int main(int argc, char** argv) {
    bool print_sets = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--print-sets") print_sets = true;
    }
    // payload/p/q default-emptied → common.cpp generates deterministic payload
    // via same PRNG chain as set_gen (so unit tests stay reproducible).
    run_pso(MODE_PSI_SUM, print_sets);
    return 0;
}
