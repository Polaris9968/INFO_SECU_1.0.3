// test_galois128.cpp
#include "galois128.h"
#include "utils.h"

#include <cassert>
#include <chrono>
#include <iostream>
#include <random>
#include <vector>
#include <iomanip>
#include <sstream>
#include <cstring>

using namespace okvs;
using uint128_t = unsigned __int128;

// helper: build uint128 from two u64 (hi, lo)
static uint128_t make_u128(uint64_t hi, uint64_t lo) {
  uint128_t v = (uint128_t)hi;
  v <<= 64;
  v |= (uint128_t)lo;
  return v;
}

// // helper: hex print uint128_t (big-endian visual)
// static std::string to_hex(uint128_t v) {
//   uint64_t parts[2];
//   std::memcpy(parts, &v, 16);
//   uint64_t lo = parts[0];
//   uint64_t hi = parts[1];
//   std::ostringstream ss;
//   ss << std::hex << std::setfill('0') << std::setw(16) << hi
//      << std::setw(16) << lo;
//   return ss.str();
// }

// // helper: hex print Galois128 via its data()
// static std::string to_hex(const Galois128 &g) {
//   const uint8_t *d = g.data();
//   std::ostringstream ss;
//   ss << std::hex << std::setfill('0');
//   for (size_t i = 0; i < 16; ++i) ss << std::setw(2) << static_cast<int>(d[i]);
//   return ss.str();
// }

// assert equal else print nice debug
static void assert_eq_g128(const Galois128 &a, const Galois128 &b,
                           const char *msg = nullptr) {
  uint128_t va = a.get<uint128_t>(0);
  uint128_t vb = b.get<uint128_t>(0);
  if (va != vb) {
    std::cerr << "Assertion failed";
    if (msg) std::cerr << ": " << msg;
    std::cerr << "\n lhs = " << print_hex16(a) << "\n rhs = " << print_hex16(b)
              << std::endl;
    std::abort();
  }
}

// ----------------------------------------------------------------------
// ADDITION TESTS (NEW)
// ----------------------------------------------------------------------
static void test_addition_basic() {
  std::cout << "[INFO] Testing GF(2^128) addition properties...\n";

  Galois128 zero(make_u128(0, 0));
  Galois128 one(make_u128(0, 1));
  Galois128 a(make_u128(0x0123456789abcdefULL, 0xfedcba9876543210ULL));
  Galois128 b(make_u128(0x1111111111111111ULL, 0x2222222222222222ULL));
  Galois128 c(make_u128(0xAAAAAAAAAAAAAAAAULL, 0x5555555555555555ULL));

  // identity: a + 0 = a
  assert_eq_g128(a.Add(zero), a, "a + 0 == a");
  assert_eq_g128(zero.Add(a), a, "0 + a == a");

  // self-inverse: a + a = 0
  assert_eq_g128(a.Add(a), zero, "a + a == 0");

  // commutativity
  assert_eq_g128(a.Add(b), b.Add(a), "a + b == b + a");

  // associativity
  assert_eq_g128(a.Add(b.Add(c)), a.Add(b).Add(c), "associativity");

  // distributivity with multiplication
  auto lhs = a.Mul(b.Add(c));
  auto rhs = a.Mul(b).Add(a.Mul(c));
  assert_eq_g128(lhs, rhs, "distributivity a*(b+c)=a*b+a*c");

  std::cout << "[OK] Addition tests passed\n";
}

// ----------------------------------------------------------------------
// Existing tests from your provided code
// ----------------------------------------------------------------------
static void test_basic_properties() {
  Galois128 a(make_u128(0x0123456789abcdefULL, 0xfedcba9876543210ULL));
  Galois128 zero(make_u128(0, 0));
  Galois128 one(make_u128(0, 1));

  auto r0 = a.Mul(zero);
  assert_eq_g128(r0, zero, "a * 0 == 0");

  auto r1 = a.Mul(one);
  assert_eq_g128(r1, a, "a * 1 == a");

  Galois128 b(make_u128(0xdeadbeefcafebabeULL, 0x1234567890abcdefULL));
  auto ab = a.Mul(b);
  auto ba = b.Mul(a);
  assert_eq_g128(ab, ba, "commutativity a*b == b*a");
}

static void test_cross_check_cc_gf128Mul() {
  std::mt19937_64 rng(0xC0FFEE);
  for (int i = 0; i < 200; ++i) {
    uint64_t a_hi = rng(), a_lo = rng(), b_hi = rng(), b_lo = rng();
    uint128_t A = make_u128(a_hi, a_lo);
    uint128_t B = make_u128(b_hi, b_lo);
    uint128_t z_ref = cc_gf128Mul(A, B);
    Galois128 ga(A), gb(B);
    Galois128 gz = ga.Mul(gb);
    uint128_t z_act = gz.get<uint128_t>(0);
    if (z_ref != z_act) {
      std::cerr << "[FAIL] cross-check cc_gf128Mul mismatch\n";
      std::abort();
    }
  }
}

static void test_pow_and_inv() {
  Galois128 g1(make_u128(0x11111111ULL, 0x22222222ULL));
  Galois128 one(make_u128(0, 1));
  auto p0 = g1.Pow(0);
  assert_eq_g128(p0, one, "x^0 == 1");
  auto p1 = g1.Pow(1);
  assert_eq_g128(p1, g1, "x^1 == x");

  std::mt19937_64 rng(0xDEADBEEF);
  for (int i = 0; i < 100; ++i) {
    uint64_t hi = rng(), lo = rng();
    uint128_t v = make_u128(hi, lo);
    Galois128 gv(v);
    if (gv.get<uint128_t>(0) == (uint128_t)0) {
      --i;
      continue;
    }
    Galois128 inv = gv.Inv();
    Galois128 prod = gv.Mul(inv);
    Galois128 oneVal(make_u128(0, 1));
    assert_eq_g128(prod, oneVal, "a * a^-1 == 1");
  }
}

static void test_associativity_and_variant_consistency() {
  std::mt19937_64 rng(0xBEEF1234);
  for (int i = 0; i < 200; ++i) {
    uint64_t a_hi = rng(), a_lo = rng(), b_hi = rng(), b_lo = rng(),
             c_hi = rng(), c_lo = rng();
    Galois128 A(make_u128(a_hi, a_lo));
    Galois128 B(make_u128(b_hi, b_lo));
    Galois128 C(make_u128(c_hi, c_lo));
    assert_eq_g128(A.Mul(B).Mul(C), A.Mul(B.Mul(C)), "associativity (Mul)");
  }
}

static void test_get_and_data_consistency() {
  Galois128 g(make_u128(0x0123456789abcdefULL, 0xfedcba9876543210ULL));
  auto arr8 = g.get<uint8_t>();
  const uint8_t *d = g.data();
  for (size_t i = 0; i < 16; ++i)
    if (arr8[i] != d[i]) {
      std::cerr << "[FAIL] get<uint8_t> inconsistent with data()\n";
      std::abort();
    }

  auto arr64 = g.get<uint64_t>();
  if (arr64[0] != g.get<uint64_t>(0) || arr64[1] != g.get<uint64_t>(1)) {
    std::cerr << "[FAIL] get<uint64_t> mismatch\n";
    std::abort();
  }
}

static void test_random_stress_compare_paths() {
  std::mt19937_64 rng(0xFEED1234);
  const int N = 500;
  for (int i = 0; i < N; ++i) {
    uint64_t a_hi = rng(), a_lo = rng(), b_hi = rng(), b_lo = rng();
    uint128_t A = make_u128(a_hi, a_lo), B = make_u128(b_hi, b_lo);
    uint128_t ref = cc_gf128Mul(A, B);
    Galois128 ga(A), gb(B);
    Galois128 gres = ga.Mul(gb);
    uint128_t got = gres.get<uint128_t>(0);
    if (got != ref) {
      std::cerr << "[FAIL] random stress mismatch\n";
      std::abort();
    }
  }
}

int main() {
  std::cout << "Running galois128 tests...\n";

  test_addition_basic();
  test_basic_properties();
  test_cross_check_cc_gf128Mul();
  test_pow_and_inv();
  test_associativity_and_variant_consistency();
  test_get_and_data_consistency();
  test_random_stress_compare_paths();

  std::cout << "[OK] all galois128 tests passed\n";
  return 0;
}
