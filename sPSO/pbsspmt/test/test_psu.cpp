// test_psu.cpp - PSU mode standalone binary
//
// Runs the pbssPMT-based PSU protocol. The receiver learns X ∪ Y.
//
//   msg construction (sender, Figure 11):
//     msg_i^b = ⊥ ⊕ (1 ⊕ b ⊕ u_i) · cuco_tab[π(i)]   (swapped vs PSI)
//
//   interpretation (receiver):
//     kept msg[i] (≠ 0) elements form X \ R;  X ∪ Y = Y ∪ kept
//
//   validation:
//     * X \ R count = n - inter
//     * false positives = 0  (each kept ∈ S ∧ kept ∉ R)
//     * Y ∪ kept == S ∪ R
//
// Usage:
//   ./test_psu                # in-process sender+receiver (testing)
//   ./test_psu --print-sets   # also dump S/R/X∪Y plaintext for eyeball verification

#include "common.h"

int main(int argc, char** argv) {
    bool print_sets = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--print-sets") print_sets = true;
    }
    run_pso(MODE_PSU, print_sets);
    return 0;
}