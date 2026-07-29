// spso_runpso.cpp — SPIKE 1 wrapper to expose sPSO run_pso() to Python
//
// Strategy: directly #include common.cpp so run_pso() and its helpers
// land in this TU. This is a SPIKE pattern; production should refactor
// common.cpp into the pbsspmt library proper.
//
// run_pso() prints everything to stdout and returns void. To make it
// Python-friendly, we wrap it: redirect std::cout to a stringstream for
// the duration of the call, then return that captured text. Python parses
// the text (key=value pairs, [intersection] list, etc.).

// Rijndael256.h must come BEFORE common.cpp: libOTe's EKEPopf.h uses
// Rijndael256Enc/Rijndael256Dec but doesn't include the header itself,
// assuming callers include it first.
#include <cryptoTools/Crypto/Rijndael256.h>

#include "/root/projects/INFO_SECU_1.0.3/sPSO/pbsspmt/test/common.cpp"

#include <sstream>
#include <string>

namespace spso {

std::string run_pso_capture(int mode_int,
                            bool print_sets,
                            const std::vector<uint64_t>& payload,
                            uint64_t p,
                            uint64_t q,
                            const std::string& dump_dir = "") {
    if (!dump_dir.empty()) {
        setenv("SPSO_DEMO_DUMP_DIR", dump_dir.c_str(), 1);
    } else {
        unsetenv("SPSO_DEMO_DUMP_DIR");
    }
    std::ostringstream oss;
    auto* old_cout = std::cout.rdbuf(oss.rdbuf());
    try {
        run_pso(static_cast<PSOMode>(mode_int), print_sets, payload, p, q);
    } catch (...) {
        std::cout.rdbuf(old_cout);
        throw;
    }
    std::cout.rdbuf(old_cout);
    return oss.str();
}

}  // namespace spso
