// test_flow.cpp - thin PSI wrapper (kept for backward compatibility)
//
// Historically the only test executable in sPSO; equivalent to test_psi.cpp.
// New code should use test_psi / test_psu / test_card.

#include "common.h"

int main() {
    run_pso(MODE_PSI);
    return 0;
}