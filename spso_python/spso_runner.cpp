// spso_runner.cpp — 可执行文件 wrapper for sPSO run_pso (SPIKE 1.5 / SPIKE 2)
//
// 编译: g++ -std=c++20 -fcoroutines -O3 -fopenmp -fPIC \
//        -maes -mavx2 -mpclmul -msse2 -msse3 -msse4.1 \
//        <includes> spso_python/spso_runner.cpp \
//        libpbsspmt.a libsecureJoin.a libmacoro.a liblibOTe.a \
//        libcoproto.a libcryptoTools.a libSimplestOT.a libKyberOT.a \
//        build/libblake3.a \
//        -lpthread -ldl -o spso_python/spso_runner
//
// 用法(SPIKE 1.5 — 随机数据,5 mode):
//   ./spso_runner --mode psi|psu|card|psi_sum|ss_psi [--print-sets] [--payload 1,2,3]
//
// 用法(SPIKE 2 — 真实输入数据,走 INFO_SECU_1.0.3 标准 PSI pipeline):
//   ./spso_runner --mode psi \
//                 --input-dir <path> \
//                 --output-file <path>
//
//   --input-dir : 读 <path>/receiver.txt 和 <path>/sender.txt
//                 (每行一个标准化的 uint64 字符串)
//   --output-file : 把"receiver 学到的交集"作为原始字符串列表写入该文件
//                   (每行一个元素)
//
// 实现要点:
//   1. 用 blake3 把每行 token 哈希成 uint64(双方用相同固定 seed,稳定)
//   2. 调用修改后的 run_pso(mode, payload, p, q, &sender_set, &recver_set)
//   3. parse stdout 中 `=== INTERSECTION_START ===` / `=== INTERSECTION_END ===` 块
//   4. 把 uint64 结果用 receiver 的 inverse-map 还原成原始字符串,写入 output-file

#include <cryptoTools/Crypto/Rijndael256.h>
#include "/root/projects/INFO_SECU_1.0.3/sPSO/pbsspmt/test/common.cpp"

#include <blake3.h>

#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <unordered_map>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <unistd.h>
#include <sys/stat.h>   // SPIKE 5: mkdir for --dump-dir
#include <sys/types.h>  // SPIKE 5: mode_t for mkdir

namespace spso_cli {

// ---- Token → uint64 hashing (deterministic across both parties) ----
//
// The Flask backend stores standardized tokens as decimal strings in
// receiver.txt / sender.txt. These can exceed uint64 (e.g. SHA-256-derived
// hashes use 16 bytes = 128 bits). To fit sPSO's uint64 protocol we map
// each line through blake3 with a fixed seed and take the first 8 bytes.
// Same string → same uint64 (preserves equality), different strings →
// distinct uint64 with overwhelming probability.
constexpr uint64_t TOKEN_HASH_SEED_LO = 0xa5a5a5a5a5a5a5a5ULL;
constexpr uint64_t TOKEN_HASH_SEED_HI = 0x5a5a5a5a5a5a5a5aULL;

uint64_t token_to_u64(const std::string& s) {
    blake3_hasher h;
    blake3_hasher_init(&h);
    // Mix in a fixed seed so an attacker can't trivially craft collisions
    // by precomputing blake3("0") etc. The seed is public (compiled in),
    // but that's fine — same seed gives same hash for same string, which
    // is what we need for PSI correctness.
    uint8_t seed_bytes[16];
    std::memcpy(seed_bytes + 0, &TOKEN_HASH_SEED_LO, 8);
    std::memcpy(seed_bytes + 8, &TOKEN_HASH_SEED_HI, 8);
    blake3_hasher_update(&h, seed_bytes, 16);
    blake3_hasher_update(&h, s.data(), s.size());
    uint8_t out[8];
    blake3_hasher_finalize(&h, out, 8);
    uint64_t v;
    std::memcpy(&v, out, 8);
    return v;
}

// ---- Mode parsing ----
PSOMode parse_mode(const char* s) {
    std::string m(s);
    if (m == "psi")     return MODE_PSI;
    if (m == "psu")     return MODE_PSU;
    if (m == "card")    return MODE_CARD;
    if (m == "psi_sum") return MODE_PSI_SUM;
    if (m == "ss_psi")  return MODE_SS_PSI;
    std::cerr << "Unknown mode: " << m << "\n";
    std::exit(2);
}

// ---- Payload parsing (unchanged from SPIKE 1.5) ----
std::vector<uint64_t> parse_payload(const char* s) {
    std::vector<uint64_t> v;
    std::string str(s);
    size_t pos = 0;
    while (pos < str.size()) {
        size_t comma = str.find(',', pos);
        if (comma == std::string::npos) comma = str.size();
        v.push_back(std::stoull(str.substr(pos, comma-pos)));
        pos = comma + 1;
    }
    return v;
}

// ---- Read tokens from file, one per line; ignore blank lines ----
std::vector<std::string> read_lines(const std::string& path) {
    std::vector<std::string> out;
    std::ifstream in(path);
    if (!in) {
        std::cerr << "Cannot open file: " << path << "\n";
        std::exit(2);
    }
    std::string line;
    while (std::getline(in, line)) {
        // Strip trailing \r (Windows line endings), whitespace
        while (!line.empty() && (line.back() == '\r' || line.back() == ' '
                                 || line.back() == '\t' || line.back() == '\n')) {
            line.pop_back();
        }
        if (!line.empty()) {
            out.push_back(line);
        }
    }
    return out;
}

// ---- Parse stdout-style intersection block ----
// Looks for "=== INTERSECTION_START ===" ... "=== INTERSECTION_END ==="
// and returns the lines in between as uint64 hex strings.
std::vector<uint64_t> parse_intersection_block(const std::string& stdout_text) {
    const std::string START = "=== INTERSECTION_START ===";
    const std::string END   = "=== INTERSECTION_END ===";
    auto s_pos = stdout_text.find(START);
    auto e_pos = stdout_text.find(END, s_pos == std::string::npos ? 0 : s_pos);
    if (s_pos == std::string::npos || e_pos == std::string::npos) {
        return {};
    }
    s_pos += START.size();
    std::vector<uint64_t> result;
    std::istringstream iss(stdout_text.substr(s_pos, e_pos - s_pos));
    std::string line;
    while (std::getline(iss, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' '
                                 || line.back() == '\t')) {
            line.pop_back();
        }
        if (line.empty()) continue;
        // Strip optional "0x" prefix
        if (line.size() > 2 && line[0] == '0' && (line[1] == 'x' || line[1] == 'X')) {
            line = line.substr(2);
        }
        try {
            result.push_back(std::stoull(line, nullptr, 16));
        } catch (...) {
            std::cerr << "[spso_runner] WARN: cannot parse intersection line: '"
                      << line << "'\n";
        }
    }
    return result;
}

// ---- Parse stdout-style union block (SPIKE 3: PSU) ----
// Looks for "=== UNION_START ===" ... "=== UNION_END ===" — same shape as INTERSECTION.
std::vector<uint64_t> parse_union_block(const std::string& stdout_text) {
    const std::string START = "=== UNION_START ===";
    const std::string END   = "=== UNION_END ===";
    auto s_pos = stdout_text.find(START);
    auto e_pos = stdout_text.find(END, s_pos == std::string::npos ? 0 : s_pos);
    if (s_pos == std::string::npos || e_pos == std::string::npos) {
        return {};
    }
    s_pos += START.size();
    std::vector<uint64_t> result;
    std::istringstream iss(stdout_text.substr(s_pos, e_pos - s_pos));
    std::string line;
    while (std::getline(iss, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' '
                                 || line.back() == '\t')) {
            line.pop_back();
        }
        if (line.empty()) continue;
        if (line.size() > 2 && line[0] == '0' && (line[1] == 'x' || line[1] == 'X')) {
            line = line.substr(2);
        }
        try {
            result.push_back(std::stoull(line, nullptr, 16));
        } catch (...) {
            std::cerr << "[spso_runner] WARN: cannot parse union line: '"
                      << line << "'\n";
        }
    }
    return result;
}

// ---- Parse stdout-style cardinality line (SPIKE 3: CARD) ----
// Looks for "=== CARDINALITY_VALUE: <N> ===" — single integer.
uint64_t parse_cardinality_line(const std::string& stdout_text) {
    const std::string MARKER = "=== CARDINALITY_VALUE:";
    auto pos = stdout_text.find(MARKER);
    if (pos == std::string::npos) {
        return 0;
    }
    pos += MARKER.size();
    // Skip spaces
    while (pos < stdout_text.size() && stdout_text[pos] == ' ') ++pos;
    // Read until next space or '==='
    std::string num_str;
    while (pos < stdout_text.size() && stdout_text[pos] != ' ' && stdout_text[pos] != '\n' && stdout_text[pos] != '\r') {
        num_str.push_back(stdout_text[pos++]);
    }
    try {
        return std::stoull(num_str);
    } catch (...) {
        std::cerr << "[spso_runner] WARN: cannot parse cardinality value: '"
                  << num_str << "'\n";
        return 0;
    }
}

// ---- Parse stdout-style sum value line (SPIKE 4: PSI-SUM) ----
// Looks for "=== PSI_SUM_VALUE: <N> ===" — single integer (mod q).
uint64_t parse_sum_value_line(const std::string& stdout_text) {
    const std::string MARKER = "=== PSI_SUM_VALUE:";
    auto pos = stdout_text.find(MARKER);
    if (pos == std::string::npos) {
        return 0;
    }
    pos += MARKER.size();
    while (pos < stdout_text.size() && stdout_text[pos] == ' ') ++pos;
    std::string num_str;
    while (pos < stdout_text.size() && stdout_text[pos] != ' ' && stdout_text[pos] != '\n' && stdout_text[pos] != '\r') {
        num_str.push_back(stdout_text[pos++]);
    }
    try {
        return std::stoull(num_str);
    } catch (...) {
        std::cerr << "[spso_runner] WARN: cannot parse sum value: '"
                  << num_str << "'\n";
        return 0;
    }
}

void print_usage() {
    std::cerr <<
        "Usage: spso_runner --mode <psi|psu|card|psi_sum|ss_psi> [options]\n"
        "SPIKE 1.5 (random sets):\n"
        "  --print-sets        Print sets before/after protocol\n"
        "  --payload 1,2,3     Comma-separated uint64 payload (for PSI-Sum)\n"
        "  --p <uint>          Prime p (default 2^32)\n"
        "  --q <uint>          Prime q (default 2^50)\n"
        "SPIKE 2 (real input from INFO_SECU_1.0.3 PSI pipeline):\n"
        "  --input-dir <path>  Read <path>/receiver.txt and <path>/sender.txt\n"
        "                      (one standardized token per line)\n"
        "  --output-file <path> Write recovered intersection (as original\n"
        "                       string tokens, one per line) to this file.\n"
        "                       Only meaningful for --mode psi|ss_psi.\n"
        "  --help              Show this help\n";
}

}  // namespace spso_cli

int main(int argc, char** argv) {
    using namespace spso_cli;
    PSOMode mode = MODE_PSI;
    bool print_sets = false;
    std::vector<uint64_t> payload;
    uint64_t p = 1ULL << 32;
    uint64_t q = 1ULL << 50;
    std::string input_dir;
    std::string output_file;
    // 2026-07-30 Friday fix: SS-PSI mode 用 out 参数拿到两份额,需要预先声明
    std::vector<std::string> ss_share_sender_out;
    std::vector<std::string> ss_share_receiver_out;

    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--mode") == 0 && i+1 < argc) {
            mode = parse_mode(argv[++i]);
        } else if (std::strcmp(argv[i], "--print-sets") == 0) {
            print_sets = true;
        } else if (std::strcmp(argv[i], "--payload") == 0 && i+1 < argc) {
            payload = parse_payload(argv[++i]);
        } else if (std::strcmp(argv[i], "--p") == 0 && i+1 < argc) {
            p = std::stoull(argv[++i]);
        } else if (std::strcmp(argv[i], "--q") == 0 && i+1 < argc) {
            q = std::stoull(argv[++i]);
        } else if (std::strcmp(argv[i], "--input-dir") == 0 && i+1 < argc) {
            input_dir = argv[++i];
        } else if (std::strcmp(argv[i], "--output-file") == 0 && i+1 < argc) {
            output_file = argv[++i];
        } else if (std::strcmp(argv[i], "--dump-dir") == 0 && i+1 < argc) {
            // SPIKE 5 (2026-07-30 Friday demo): env var → common.cpp dumps OPRF
            // prf_vals per-cuckoo-bin to <dump-dir>/oprf_prf_*.txt for UI demo.
            {
                std::string dump_dir = argv[++i];
                mkdir(dump_dir.c_str(), 0755);  // safe: no-op if exists
                setenv("SPSO_DEMO_DUMP_DIR", dump_dir.c_str(), 1);
                std::cerr << "[spso_runner] dump-dir = " << dump_dir << "\n";
            }
        } else if (std::strcmp(argv[i], "--help") == 0 || std::strcmp(argv[i], "-h") == 0) {
            print_usage(); return 0;
        } else {
            std::cerr << "Unknown arg: " << argv[i] << "\n";
            print_usage(); return 2;
        }
    }

    try {
        if (!input_dir.empty()) {
            // ---- SPIKE 2: real input sets from INFO_SECU_1.0.3 PSI pipeline ----
            const std::string receiver_path = input_dir + "/receiver.txt";
            const std::string sender_path   = input_dir + "/sender.txt";

            std::cerr << "[spso_runner] reading receiver from " << receiver_path << "\n";
            std::cerr << "[spso_runner] reading sender   from " << sender_path   << "\n";

            std::vector<std::string> receiver_lines = read_lines(receiver_path);
            std::vector<std::string> sender_lines   = read_lines(sender_path);

            if (receiver_lines.empty() || sender_lines.empty()) {
                std::cerr << "[spso_runner] ERROR: empty input file (receiver="
                          << receiver_lines.size() << ", sender=" << sender_lines.size() << ")\n";
                return 2;
            }
            // SPIKE 2: pad the smaller set with sentinel uint64 values so both
            // sets are the same size (run_pso requires equal input sizes).
            // The sentinel values are blake3("__spike2_sentinel__") XOR i (so
            // each padding element is unique and distinct from any real token).
            // The protocol will treat these as 'extra items that don't intersect
            // with the other party', which is harmless for PSI correctness.
            // SPIKE 2 padding strategy:
            //   1. Equalize receiver/sender sizes (smaller side padded).
            //   2. Ensure total padded size >= MIN_N (32), so OKVS w constraint
            //      (m_oprf = ceil(1.3 * cuco_sz) > w) has enough room.
            //      For MIN_N=32, cuco_sz=ceil(1.22*32)=40, m_oprf=ceil(1.3*40)=52,
            //      so w can be up to 48 (multiple of 8). Empirically w=32 works.
            //   3. Each side gets UNIQUE sentinel strings so blake3 hashes don't
            //      collide with each other or with real tokens.
            const size_t MIN_N = 32;
            size_t target_size = std::max({receiver_lines.size(), sender_lines.size(), MIN_N});
            if (receiver_lines.size() != target_size || sender_lines.size() != target_size) {
                std::cerr << "[spso_runner] INFO: padding (recver=" << receiver_lines.size()
                          << ", sender=" << sender_lines.size()
                          << ") -> " << target_size << "\n";
            }
            while (receiver_lines.size() < target_size) {
                size_t idx = receiver_lines.size();
                receiver_lines.push_back("__spike2_pad_recver_" + std::to_string(idx));
            }
            while (sender_lines.size() < target_size) {
                size_t idx = sender_lines.size();
                sender_lines.push_back("__spike2_pad_sender_" + std::to_string(idx));
            }

            // SPIKE 4: PSI-Sum 的 payload 也需要 padding 到 target_size。
            // common.cpp 要求 payload.size() == n（=sender_set.size()=target_size）
            // padding sender 多出的 sentinels 其 value = 0（不影响 sum）
            if (mode == MODE_PSI_SUM && !payload.empty() && payload.size() < target_size) {
                std::cerr << "[spso_runner] INFO: padding payload (size="
                          << payload.size() << " -> " << target_size << ")\n";
                payload.resize(target_size, 0);
            }

            // Hash strings → uint64 (both parties use the same fixed seed)
            std::vector<uint64_t> sender_set, recver_set;
            sender_set.reserve(sender_lines.size());
            recver_set.reserve(receiver_lines.size());
            for (auto& s : sender_lines)   sender_set.push_back(token_to_u64(s));
            for (auto& s : receiver_lines) recver_set.push_back(token_to_u64(s));

            // Build inverse map: uint64 → original string (receiver's tokens).
            // sPSO's PSI mode lets the receiver (R) learn X ∩ Y, so we use the
            // receiver's mapping to recover the original string tokens that the
            // Flask app will display.
            std::unordered_map<uint64_t, std::string> recv_u64_to_orig;
            recv_u64_to_orig.reserve(receiver_lines.size() * 2);
            for (size_t i = 0; i < receiver_lines.size(); ++i) {
                uint64_t h = token_to_u64(receiver_lines[i]);
                // First occurrence wins (preserve stable mapping)
                if (recv_u64_to_orig.find(h) == recv_u64_to_orig.end()) {
                    recv_u64_to_orig[h] = receiver_lines[i];
                }
            }

            std::cerr << "[spso_runner] n=" << sender_set.size()
                      << " (sender) / " << recver_set.size() << " (receiver)\n";

            // Run protocol (redirect stdout to capture for intersection parse)
            // We can't easily redirect stdout from inside the process without
            // freopen(), so we rely on the structured markers in run_pso's output.
            // The markers also go to stderr (for debugging) if needed.
            std::cout.flush();
            std::cerr.flush();

            // Capture stdout to a pipe so we can parse the intersection block
            // without it being mixed with other log output.
            int saved_stdout_fd = dup(STDOUT_FILENO);
            int pipefd[2];
            if (pipe(pipefd) != 0) {
                std::cerr << "[spso_runner] pipe() failed\n";
                return 1;
            }
            // Redirect stdout to write-end of pipe
            dup2(pipefd[1], STDOUT_FILENO);
            close(pipefd[1]);

            run_pso(mode, /*print_sets=*/false, payload, p, q,
                    &sender_set, &recver_set,
                    &ss_share_sender_out, &ss_share_receiver_out);

            // Restore stdout
            fflush(stdout);
            dup2(saved_stdout_fd, STDOUT_FILENO);
            close(saved_stdout_fd);

            // Read pipe contents
            std::string captured_stdout;
            char buf[4096];
            ssize_t n;
            while ((n = read(pipefd[0], buf, sizeof(buf))) > 0) {
                captured_stdout.append(buf, n);
            }
            close(pipefd[0]);

            // Parse result block based on mode (SPIKE 2 / SPIKE 3 / SPIKE 4)
            std::vector<uint64_t> inter_u64;
            std::vector<uint64_t> union_u64;
            uint64_t cardinality_u64 = 0;
            uint64_t sum_u64 = 0;   // SPIKE 4
            // 2026-07-30 Friday fix: SS-PSI mode 不再解析 INTERSECTION 块(论文§5.2 是 secret shares 输出,
            // 不是明文交集)。out 参数已通过 run_pso 拿到 ss_share_sender_out / ss_share_receiver_out
            if (mode == MODE_SS_PSI) {
                // 跳过 INTERSECTION 解析,下面专门处理 share 文件
            } else if (mode == MODE_PSI) {
                inter_u64 = parse_intersection_block(captured_stdout);
            } else if (mode == MODE_PSU) {
                union_u64 = parse_union_block(captured_stdout);
            } else if (mode == MODE_CARD) {
                cardinality_u64 = parse_cardinality_line(captured_stdout);
            } else if (mode == MODE_PSI_SUM) {
                sum_u64 = parse_sum_value_line(captured_stdout);
                // SPIKE 4: 重新输出 marker 到 stdout 供 spso_client 解析
                std::cout << "=== PSI_SUM_VALUE: " << sum_u64 << " ===\n";
            }

            std::cerr << "[spso_runner] mode=" << mode_name(mode)
                      << " recovered (uint64): "
                      << (mode == MODE_PSI ? std::to_string(inter_u64.size()) + " elements"
                          : mode == MODE_SS_PSI ? std::to_string(ss_share_sender_out.size()) + " shares per party"
                          : mode == MODE_PSU ? std::to_string(union_u64.size()) + " union elements"
                          : mode == MODE_CARD ? std::to_string(cardinality_u64) + " cardinality"
                          : mode == MODE_PSI_SUM ? std::to_string(sum_u64) + " sum (mod q)"
                          : std::string("n/a")) << "\n";

            // Map uint64 → original receiver-string (for display in Flask)
            // For PSU: receiver only knows its OWN tokens, but the union
            // includes sender's tokens too. We can only recover receiver's
            // tokens; sender's tokens come from the union source. For full
            // fidelity, we need both sender and receiver inverse maps.
            //
            // Strategy: union = X ∪ Y. We need a way to recover both sides.
            // Solution: build inverse map from BOTH files (receiver + sender),
            // since the protocol uint64 was hashed from the same string token
            // on both sides, so uint64 → original_token is the same in both
            // directions (collision-resistant blake3).
            //
            // For PSI: only intersected (X ∩ Y) elements are kept, and these
            // by definition exist in BOTH parties' input. Receiver's inverse
            // map suffices (sender's elements that intersect with R are also
            // in R's input → recovery works). But for robustness, we use the
            // union of both.
            //
            // Build combined inverse map.
            std::unordered_map<uint64_t, std::string> u64_to_orig;
            u64_to_orig.reserve((receiver_lines.size() + sender_lines.size()) * 2);
            for (size_t i = 0; i < receiver_lines.size(); ++i) {
                uint64_t h = token_to_u64(receiver_lines[i]);
                if (u64_to_orig.find(h) == u64_to_orig.end()) {
                    u64_to_orig[h] = receiver_lines[i];
                }
            }
            for (size_t i = 0; i < sender_lines.size(); ++i) {
                uint64_t h = token_to_u64(sender_lines[i]);
                if (u64_to_orig.find(h) == u64_to_orig.end()) {
                    u64_to_orig[h] = sender_lines[i];
                }
            }

            uint64_t not_found = 0;
            std::vector<std::string> recovered_orig;
            if (mode == MODE_PSI) {
                recovered_orig.reserve(inter_u64.size());
                for (uint64_t v : inter_u64) {
                    auto it = u64_to_orig.find(v);
                    if (it != u64_to_orig.end()) {
                        recovered_orig.push_back(it->second);
                    } else {
                        ++not_found;
                        std::ostringstream oss;
                        oss << "0x" << std::hex << std::setw(16) << std::setfill('0') << v;
                        recovered_orig.push_back(oss.str());
                    }
                }
            } else if (mode == MODE_PSU) {
                recovered_orig.reserve(union_u64.size());
                for (uint64_t v : union_u64) {
                    auto it = u64_to_orig.find(v);
                    if (it != u64_to_orig.end()) {
                        recovered_orig.push_back(it->second);
                    } else {
                        ++not_found;
                        std::ostringstream oss;
                        oss << "0x" << std::hex << std::setw(16) << std::setfill('0') << v;
                        recovered_orig.push_back(oss.str());
                    }
                }
            } else if (mode == MODE_CARD) {
                // Cardinality is just a single integer; no inverse map needed.
                recovered_orig.push_back(std::to_string(cardinality_u64));
            } else if (mode == MODE_PSI_SUM) {
                // SPIKE 4: PSI-Sum recovered value (mod q). Single integer line.
                // 同时 dump cardinality(从 intersection size 求出)，以便 routes.py / _read_finalized_result 读
                recovered_orig.push_back(std::to_string(sum_u64));
            }
            if (not_found > 0) {
                std::cerr << "[spso_runner] WARNING: " << not_found
                          << " intersected uint64 values had no original mapping\n";
            }

            // Write output file
            // 2026-07-30 Friday fix: SS-PSI mode 输出两份 share 文件 (share_sender.txt + share_receiver.txt)
            // 其他 mode 仍然写 recovered_orig 到 output_file
            if (mode == MODE_SS_PSI) {
                if (input_dir.empty()) {
                    std::cerr << "[spso_runner] ERROR: SS-PSI mode requires --input-dir "
                              << "(写 share_sender.txt / share_receiver.txt 到该目录)\n";
                    return 1;
                }
                const std::string share_sender_path = input_dir + "/share_sender.txt";
                const std::string share_receiver_path = input_dir + "/share_receiver.txt";
                std::ofstream ofs(share_sender_path);
                if (!ofs) {
                    std::cerr << "[spso_runner] cannot open share file: "
                              << share_sender_path << "\n";
                    return 1;
                }
                for (const auto& s : ss_share_sender_out) ofs << s << "\n";
                ofs.close();
                std::ofstream ofr(share_receiver_path);
                if (!ofr) {
                    std::cerr << "[spso_runner] cannot open share file: "
                              << share_receiver_path << "\n";
                    return 1;
                }
                for (const auto& s : ss_share_receiver_out) ofr << s << "\n";
                ofr.close();
                std::cerr << "[spso_runner] SS-PSI wrote "
                          << ss_share_sender_out.size() << " sender shares to "
                          << share_sender_path << "\n"
                          << "[spso_runner] SS-PSI wrote "
                          << ss_share_receiver_out.size() << " receiver shares to "
                          << share_receiver_path << "\n";
            } else if (!output_file.empty()) {
                std::ofstream of(output_file);
                if (!of) {
                    std::cerr << "[spso_runner] cannot open output file: "
                              << output_file << "\n";
                    return 1;
                }
                for (auto& s : recovered_orig) {
                    of << s << "\n";
                }
                std::cerr << "[spso_runner] wrote " << recovered_orig.size()
                          << " result tokens to " << output_file << "\n";
            } else {
                std::cerr << "[spso_runner] (no --output-file given, "
                          << "result not persisted)\n";
            }

            // Also dump captured stdout to stderr for debugging visibility
            std::cerr << "----- run_pso stdout (truncated) -----\n";
            // Print last 4KB only to avoid spamming
            if (captured_stdout.size() > 4096) {
                std::cerr << "..." << (captured_stdout.size() - 4096)
                          << " bytes truncated...\n";
                std::cerr << captured_stdout.substr(captured_stdout.size() - 4096);
            } else {
                std::cerr << captured_stdout;
            }
            std::cerr << "----- end stdout -----\n";

            return 0;
        } else {
            // ---- SPIKE 1.5: random sets via set_gen ----
            run_pso(mode, print_sets, payload, p, q);
        }
    } catch (const std::exception& e) {
        std::cerr << "run_pso threw: " << e.what() << "\n";
        return 1;
    }
    return 0;
}