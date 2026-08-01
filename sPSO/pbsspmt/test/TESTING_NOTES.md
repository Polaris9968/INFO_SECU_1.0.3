# sPSO Test Notes

测试相关的事实 / 约定 / 已知偏差,记在这里避免下次重复踩坑。

---

## 1. `set_gen` 存在 off-by-one（已修）

历史：`set_gen(n, 1, sender_set, ...)` 使 S 落在 `[1, 1+n)`，叠加 R 的 `[n-inter, 2n-inter)`，交集 = `inter + 1`（包含两端点）。inter=32 实际 33，所有 ±1 都来自这里，不是协议噪声。

**修复**(已 commit)：S 的 start 从 `1` 改成 `0`。

```cpp
set_gen(n, (uint64_t)0,   sender_set, setGenSeed);   // S = [0, n)
set_gen(n, n - inter,     recver_set, setGenSeed);   // R = [n-inter, 2n-inter)
//   ⇒ |S ∩ R| = |[n-inter, n)| = inter  ✓
```

**验证**：test_psi `|X∩Y|=32`，test_psu `|X∪Y|=480 = 256+256-32`，test_card `card=32`，全部对得上 `inter=32`。

---

## 2. 固定种子 `block(123456)` - 测试结果可重复

```cpp
// pbsspmt/test/common.cpp L39
PRNG commPrng(block(123456));
```

- S / R / cuckoo / OPRF / OKVS **所有 PRNG 都从这一个固定种子派生**
- 每次跑 `test_psi` / `test_psu` / `test_card`,S / R 完全一样(同名 binary 不同进程也一样)
- 这是 unit-test 通用做法,目的是测试结果可重现

**改成随机**(backend 集成时):
```cpp
PRNG commPrng(sysRandomSeed());
```

---

## 3. Plaintext Dump 设计(`--print-sets`)

- **阈值**:`n ≤ 128` 自动开
- **强制**:`--print-sets` 命令行 flag 强制覆盖阈值
- **CARD 模式**:完全不响应 `--print-sets`(协议只输出基数,没东西可"明文对比")
- **格式**:每集合一组,每行 8 个 hex(总是 16 位补齐),前缀:
  - `S: sender_set`
  - `R: recver_set`
  - `X∩Y: intersection (receiver learned)` + `X∩Y: expected (S ∩ R)` + `(match = YES/NO)`
  - `X\Y: kept (receiver learned X minus R)` + `X∪Y: union (receiver learned = R + kept)` + `X∪Y: expected (S ∪ R)` + `(match = YES/NO)`

实现:`pbsspmt/test/common.h` 的 `print_set_hex<>` + `common.cpp` 在 `th_sender.join() / th_recver.join()` 后的块。

---

## 4. 集合是有序的吗?

- **S / R**:`vector<uint64_t>`,`set_gen` 按 `i` 顺序填,但每个值是 blake3 哈希(伪随机均匀分布)→ **看起来乱序**
- **X∩Y / X\Y / X∪Y**:`std::set<uint64_t>`,**自动按数值升序排好序**(C++ std::set 用 `std::less<uint64_t>`)

---

## 5. 默认大小(demo 用,不是生产)

```cpp
// pbsspmt/test/common.h
constexpr uint64_t DEFAULT_N      = (1ull << 8);   // 256 (debug,生产用 2^16)
constexpr uint64_t DEFAULT_INTER  = (1ull << 5);   // 32  (debug,生产用 2^14)
constexpr double   DEFAULT_OKVS_EXP = 1.30;
constexpr uint64_t DEFAULT_OKVS_W   = 96;
```

生产参数(参考,没测过):`DEFAULT_N = 2^16`、`DEFAULT_INTER = 2^14`。

---

## 6. PSU 根因（已修 + commit）

`pbsspmt/test/common.cpp` 里 cuckoo init 传了第 4 参数 `cuckooDummy`（随机值），与 cuckoo3 默认 dummy `0xff` 不一致。Receiver 端 `if (hi == DUMMY_HI=0xff) continue` 过滤不掉那些随机 dummy 的空 bin，导致 phantom 元素一路传到 X∪Y。

**修复**(已 commit)：
```cpp
// Sender:
cuckoo.init(n, cuco_sz, cuckooSeed);   // 3 参数、走 cuckoo3 默认 dummy block(0xff, 0)
                                       // （历史代码曾用 `uint64_t cuckooDummy = commPrng.get()`
                                       //   作为第 4 参数——已删除，连同对应的 PRNG 消费
                                       //   （避免浪费 entropy & 偏移下游 seed 流））
// Receiver:
constexpr uint64_t DUMMY_HI = 0xff;
if (msg.get<uint64_t>(1) == DUMMY_HI) continue;   // hi == 0xff → cuckoo3 默认 dummy
```

验证 `n=256, inter=32`：X∪Y match = YES，phantom = 0，false_pos = 0。

---

## 7. 还没做的清理

- `[debug-sender]` cuckooDummy / cuckoo_tab scan 的 debug 输出还在 `common.cpp` 里（调试 phantom 时加的）—— 部分已清理（PSI-Sum V_\_* 生成部分仍有一句明确注释指向 §6）
- Secret-Shared PSI / set_gen 同名参数复用 / 大 n 压力测试