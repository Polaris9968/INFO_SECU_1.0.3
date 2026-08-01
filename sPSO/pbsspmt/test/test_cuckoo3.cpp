#include "cuckoo3.h"
#include "blake3.h"
#include <iostream>
#include <random>
#include <unordered_set>
#include <cassert>
#include <chrono>

using namespace osuCrypto;
using namespace std::chrono;

// Test CuckooHash3
void test_cuckoo_hash_3(uint64_t n, uint64_t m, bool verify) {
    std::cout << "=== Testing CuckooHash3 (BLAKE3-based) ===" << std::endl;

    const block seed = toBlock(125679);
    const uint64_t dummy = 0;

    CuckooHash3 cuckoo;

    try {
        cuckoo.init(n, m, seed, dummy);
        std::cout << "✓ CuckooHash3 initialized successfully" << std::endl;

        std::vector<uint64_t> keys;
        keys.reserve(n);
        std::unordered_set<uint64_t> key_set;
        std::mt19937_64 gen(std::random_device{}());
        std::uniform_int_distribution<uint64_t> dis(1, UINT64_MAX);

        while (keys.size() < n) {
            uint64_t key = dis(gen);
            if (key_set.insert(key).second)
                keys.push_back(key);
        }
        std::cout << "✓ Generated " << keys.size() << " unique keys" << std::endl;

        cuckoo.insert(keys);
        std::cout << "✓ All keys inserted successfully" << std::endl;

        if (verify) {
            const std::vector<block>& table = cuckoo.get_table();
            std::cout << "✓ Table retrieved, size: " << table.size() << std::endl;

            int missing_keys = 0;
            int found_in_wrong_position = 0;

            for (uint64_t key : keys) {
                auto positions = get3hash(key, m, seed);
                uint64_t h1 = positions[0];
                uint64_t h2 = positions[1];
                uint64_t h3 = positions[2];

                bool found = false;
                uint64_t found_pos = 0;

                for (uint64_t pos : {h1, h2, h3}) {
                    if (table[pos].get<uint64_t>(1) == key) {
                        found = true;
                        found_pos = pos;
                        break;
                    }
                }

                if (!found) {
                    missing_keys++;
                    std::cout << "✗ Key " << key
                              << " not found in (" << h1 << ", " << h2 << ", " << h3 << ")\n";
                } else if (found_pos != h1 && found_pos != h2 && found_pos != h3) {
                    found_in_wrong_position++;
                    std::cout << "✗ Key " << key
                              << " found in wrong position: " << found_pos
                              << " (expected one of: " << h1 << ", " << h2 << ", " << h3 << ")\n";
                }
            }

            int used_slots = 0;
            for (const auto& slot : table)
                if (slot.get<uint64_t>(1) != dummy)
                    used_slots++;

            std::cout << "\n=== Test Results ===" << std::endl;
            std::cout << "Total keys: " << n << std::endl;
            std::cout << "Table size: " << m << std::endl;
            std::cout << "Used slots: " << used_slots
                      << " (" << (used_slots * 100.0 / m) << "%)\n";
            std::cout << "Missing keys: " << missing_keys << std::endl;
            std::cout << "Wrong positions: " << found_in_wrong_position << std::endl;

            if (missing_keys == 0 && found_in_wrong_position == 0) {
                std::cout << "✓ TEST PASSED ✅\n";
            } else {
                std::cout << "✗ TEST FAILED ❌\n";
                assert(false && "Some keys not found or misplaced");
            }
        }

    } catch (const std::exception& e) {
        std::cout << "✗ Test failed with exception: " << e.what() << std::endl;
        assert(false && "Test failed with exception");
    }
}

void test_edge_cases() {
    std::cout << "\n=== Testing Edge Cases ===" << std::endl;

    {
        CuckooHash3 cuckoo;
        uint64_t dummy = 0;
        cuckoo.init(10, 20, toBlock(1), dummy);

        std::vector<uint64_t> keys = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
        cuckoo.insert(keys);

        const auto& table = cuckoo.get_table();
        for (uint64_t key : keys) {
            auto positions = get3hash(key, 20, toBlock(1));
            bool found = false;
            for (uint64_t pos : positions) {
                if (table[pos].get<uint64_t>(1) == key) {
                    found = true;
                    break;
                }
            }
            assert(found && "Key not found in small-scale edge test");
        }
        std::cout << "✓ Small scale test passed" << std::endl;
    }

    {
        CuckooHash3 cuckoo;
        cuckoo.init(5, 10, toBlock(2));
        try {
            cuckoo.init(5, 10, toBlock(2));
            assert(false && "Should have thrown exception for double init");
        } catch (const std::runtime_error&) {
            std::cout << "✓ Double init correctly rejected" << std::endl;
        }
    }

    {
        CuckooHash3 cuckoo;
        cuckoo.init(5, 10, toBlock(3));
        try {
            cuckoo.get_table();
            assert(false && "Should have thrown exception for get_table before insert");
        } catch (const std::runtime_error&) {
            std::cout << "✓ Pre-insert get_table correctly rejected" << std::endl;
        }
    }
}

int main() {
    try {
        const uint64_t n = (1 << 10);
        const uint64_t m = std::ceil((double)1.22 * n) + 2;

        auto start = high_resolution_clock::now();
        test_cuckoo_hash_3(n, m, true);
        auto end = high_resolution_clock::now();

        test_edge_cases();

        std::cout << "\n🎉 All tests completed successfully!" << std::endl;
        std::cout << "Cuckoo hash inserted " << n << " items in "
                  << duration<double>(end - start).count() << " s" << std::endl;

        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Test failed: " << e.what() << std::endl;
        return 1;
    }
}
