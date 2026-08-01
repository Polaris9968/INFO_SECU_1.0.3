// test_card.cpp - PSI-Card mode standalone binary
//
// Runs the pbssPMT-based PSI-Cardinality protocol. The receiver learns |X ∩ Y|.
//
//   msg construction (sender, Figure 11):
//     msg_i^b = b ⊕ es_i   (single bit; encoded in a 128-bit block by libOTe)
//
//   interpretation (receiver):
//     z_i = v_i ⊕ u_i = msg_i^{v_i}  (low bit of received block)
//     HammingWeight(z_1, ..., z_{n_c}) == |X ∩ Y|
//
//   validation:
//     * cardinality == inter
//
// NOTE: paper claims OT messages are 1 bit, but libOTe's Silent OT extension
// only exposes a 128-bit block interface, so communication in CARD mode is
// the same as PSI/PSU in practice. (Future optimization: bit-OT primitive.)
//
// Usage:
//   ./test_card                # in-process sender+receiver (testing)
//   ./test_card --print-sets   # accepted but ignored (CARD outputs only cardinality)
//
// Why CARD skips plaintext dump: CARD only reveals a count, not element identities.
// There's nothing to eyeball-compare — the digits are already the answer.

#include "common.h"

int main(int argc, char** argv) {
    (void)argc; (void)argv;  // CARD ignores --print-sets (see comment above)
    run_pso(MODE_CARD, /*print_sets=*/false);
    return 0;
}