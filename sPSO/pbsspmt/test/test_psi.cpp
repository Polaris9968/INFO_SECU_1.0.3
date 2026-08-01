// test_psi.cpp - PSI mode standalone binary
//
// Runs the pbssPMT-based PSI protocol. The receiver learns X ∩ Y.
//
//   Behaviour matches the legacy test_workflow:
//     non-zero msg[i] ⇒ element ∈ S ∩ R, count = inter, false positives = 0
//
// Usage:
//   ./test_psi                # in-process sender+receiver (testing)
//   ./test_psi --print-sets   # also dump S/R/X∩Y plaintext for eyeball verification
//
// For backend integration, invoke as:
//   ./test_psi sender   # sender-only (connects to localhost:1213)   [NOT YET IMPL]

#include "common.h"

int main(int argc, char** argv) {
    bool print_sets = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--print-sets") print_sets = true;
    }
    run_pso(MODE_PSI, print_sets);
    return 0;
}