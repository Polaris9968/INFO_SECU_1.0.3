#!/usr/bin/env python3
"""spso_client.py — Python client for sPSO (SPIKE 1.5 / SPIKE 2 / SPIKE 3)

通过 subprocess 调用 spso_runner 可执行文件,把 run_pso 的输出捕获回来。
临时替代 spso.so pybind11 路线 — 因为 sPSO 静态库的 PIC + weak inline std:: 符号
让 shared library 链接非常麻烦。先用 subprocess 验证协议能跑,
spso.so 挪到 SPIKE 1.6。

SPIKE 1.5 用法(随机数据):
  from spso_client import SpsoClient
  client = SpsoClient("/root/projects/INFO_SECU_1.0.3/spso_python/spso_runner")
  result = client.run(mode="psi", print_sets=True)
  print(result)

SPIKE 2 用法(真实输入数据, INFO_SECU_1.0.3 PSI 流水线):
  client = SpsoClient(...)
  intersection = client.run_psi_on_input(
      input_dir="/path/to/group_ABCD",
      output_file="/path/to/intersection.txt",
  )
  # intersection is a list[str] of recovered tokens

SPIKE 3 用法(PSU / CARD / MATCH — sPSO 模拟 PSI):
  - run_psu_on_input(...) → 写 union.txt,返回 union list[str]
  - run_card_on_input(...) → 写 cardinality.txt,返回整数
  - run_match_on_input(...) → 写 matched.txt,返回 matched_alice list[str]
                                (内部跑 psi 模式,拿交集,filter 到 alice 的子集)
"""

import subprocess
import os
import sys
from pathlib import Path

DEFAULT_RUNNER = "/root/projects/INFO_SECU_1.0.3/spso_python/spso_runner"

VALID_MODES = {"psi", "psu", "card", "psi_sum", "ss_psi"}


class SpsoClient:
    def __init__(self, runner_path: str = DEFAULT_RUNNER, timeout: int = 600):
        self.runner_path = Path(runner_path)
        if not self.runner_path.exists():
            raise FileNotFoundError(
                f"spso_runner not found at {runner_path}. "
                f"Run build_spso_runner.sh first."
            )
        self.timeout = timeout

    def run(self, mode: str, print_sets: bool = False,
            payload: list[int] = None,
            p: int = 1 << 32, q: int = 1 << 50) -> str:
        """SPIKE 1.5: 调用 spso_runner --mode <mode>,返回 stdout."""
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}, must be one of {VALID_MODES}")

        cmd = [str(self.runner_path), "--mode", mode]
        if print_sets:
            cmd.append("--print-sets")
        if payload is not None:
            cmd.extend(["--payload", ",".join(str(x) for x in payload)])
        cmd.extend(["--p", str(p), "--q", str(q)])

        print(f"[spso_client] exec: {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"spso_runner timed out after {self.timeout}s")

        if result.returncode != 0:
            raise RuntimeError(
                f"spso_runner failed (rc={result.returncode}):\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )

        return result.stdout

    def run_psi(self, print_sets: bool = False) -> str:
        return self.run(mode="psi", print_sets=print_sets)

    # ==================== SPIKE 2: real input ====================

    def run_psi_on_input(self,
                         input_dir: str,
                         output_file: str,
                         mode: str = "psi",
                         p: int = 1 << 32,
                         q: int = 1 << 50,
                         dump_dir: str = "") -> list[str]:
        """SPIKE 2: 用真实输入执行 PSI,返回交集的原始 token 列表。

        Args:
          input_dir: 包含 receiver.txt 和 sender.txt 的目录(每行一个 token)。
                     走 INFO_SECU_1.0.3 的 _generic_upload_handler 标准流水线。
          output_file: spso_runner 把恢复的交集写入此文件。
          mode: PSI 协议模式(目前只测了 'psi';ss_psi 也支持但未集成)。
          p, q: PSI-Sum 用的素数(PSI 模式忽略)。
          dump_dir: 可选。若提供,common.cpp dump OPRF 中间产物 (<dump_dir>/oprf_prf_*.txt)
                    作为 demo UI 显示。安全语义:用户明确接受 demo 妥协。

        Returns:
          list[str]: 交集的原始 token(receiver 学到)。
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}, must be one of {VALID_MODES}")

        receiver_path = os.path.join(input_dir, "receiver.txt")
        sender_path = os.path.join(input_dir, "sender.txt")
        if not os.path.exists(receiver_path):
            raise FileNotFoundError(f"receiver.txt not found in {input_dir}")
        if not os.path.exists(sender_path):
            raise FileNotFoundError(f"sender.txt not found in {input_dir}")

        cmd = [str(self.runner_path),
               "--mode", mode,
               "--input-dir", input_dir,
               "--output-file", output_file]
        if dump_dir:
            cmd.extend(["--dump-dir", dump_dir])
        cmd.extend(["--p", str(p), "--q", str(q)])

        print(f"[spso_client] SPIKE-2 exec: {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"spso_runner timed out after {self.timeout}s")

        if result.returncode != 0:
            raise RuntimeError(
                f"spso_runner failed (rc={result.returncode}):\n"
                f"--- stderr ---\n{result.stderr[:2000]}"
            )

        # Read intersection from output file (spso_runner writes original tokens)
        if not os.path.exists(output_file):
            # Empty intersection is also valid; spso_runner writes nothing in that case
            # (or writes a 0-line file). Return [].
            if result.returncode == 0:
                return []
            raise RuntimeError(
                f"spso_runner succeeded but output file not found: {output_file}"
            )

        with open(output_file, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]

    # ==================== SPIKE 3: PSU / CARD / MATCH ====================

    def run_psu_on_input(self, input_dir: str, output_file: str) -> list[str]:
        """SPIKE 3: 跑 PSU 拿 X ∪ Y,返回 union list[str](receiver 学到的并集原始 token)。"""
        return self.run_psi_on_input(input_dir, output_file, mode='psu')

    def run_card_on_input(self, input_dir: str, output_file: str) -> int:
        """SPIKE 3: 跑 CARD 拿 |X ∩ Y|,返回整数 cardinality。"""
        lines = self.run_psi_on_input(input_dir, output_file, mode='card')
        # CARD 写的是单个整数(转 str)
        if not lines:
            return 0
        try:
            return int(lines[0].strip())
        except (ValueError, IndexError):
            return 0

    def run_match_on_input(self, input_dir: str, output_file: str) -> list[str]:
        """SPIKE 3: PSI-Match(用 PSI 模拟)。

        sPSO 没有专门 match mode。策略:
        1. 跑 psi 拿 X ∩ Y(receiver 视角)
        2. receiver 的子集 ⊆ X ∩ Y,Alice = creator = receiver
        3. 把 intersection 写到 output_file(每行一个 token)
        """
        return self.run_psi_on_input(input_dir, output_file, mode='psi')

    # ==================== SPIKE 4: PSI-Sum ====================

    def run_sum_on_input(self,
                         input_dir: str,
                         output_file: str,
                         sender_values: list[int],
                         p: int = 1 << 32,
                         q: int = 1 << 50) -> int:
        """SPIKE 4: 跑 PSI-Sum 拿 sum(Σ sender_values[i] for i where sender_set[i] ∈ recver_set, mod q)。

        Args:
          input_dir: 包含 receiver.txt (alice) 和 sender.txt (bob) 的目录。
                     注意 sender.txt 中 sender_values 按每行顺序对应 sender.txt 中元素。
          output_file: spso_runner 把 recovered sum (uint64) 写入此文件(单行整数)。
          sender_values: list[int] — sender 的 values(顺序对应 sender.txt)
          p, q: sum mod q的素数(默认 2^32 / 2^50)

        Returns:
          int: recovered sum (mod q)。
        """
        if not sender_values:
            raise ValueError("sender_values 不能为空")

        receiver_path = os.path.join(input_dir, "receiver.txt")
        sender_path = os.path.join(input_dir, "sender.txt")
        if not os.path.exists(receiver_path):
            raise FileNotFoundError(f"receiver.txt not found in {input_dir}")
        if not os.path.exists(sender_path):
            raise FileNotFoundError(f"sender.txt not found in {input_dir}")

        cmd = [str(self.runner_path),
               "--mode", "psi_sum",
               "--input-dir", input_dir,
               "--output-file", output_file,
               "--payload", ",".join(str(int(x)) for x in sender_values)]
        cmd.extend(["--p", str(p), "--q", str(q)])

        print(f"[spso_client] SPIKE-4 exec: {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"spso_runner timed out after {self.timeout}s")

        if result.returncode != 0:
            raise RuntimeError(
                f"spso_runner failed (rc={result.returncode}):\n"
                f"--- stderr ---\n{result.stderr[:2000]}"
            )

        # Parse sum from captured stdout
        captured_stdout = result.stdout
        sum_value = self._parse_sum_value(captured_stdout)

        # 也写一份到 output_file (downstream 兼容)
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(str(sum_value) + "\n")
            except Exception:
                pass  # best effort
        return sum_value

    @staticmethod
    def _parse_sum_value(stdout_text: str) -> int:
        import re
        m = re.search(r"=== PSI_SUM_VALUE:\s*(\d+)\s*===", stdout_text)
        if not m:
            return 0
        try:
            return int(m.group(1))
        except ValueError:
            return 0


if __name__ == "__main__":
    # 简单 CLI: spso_client.py <mode> [--print-sets] [--input-dir X --output-file Y]
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    mode = sys.argv[1]
    print_sets = "--print-sets" in sys.argv

    # Look for --input-dir and --output-file
    input_dir = None
    output_file = None
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--input-dir" and i + 1 < len(sys.argv):
            input_dir = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--output-file" and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    client = SpsoClient()
    if input_dir is not None and output_file is not None:
        result = client.run_psi_on_input(input_dir, output_file, mode=mode)
        print(f"\n=== Intersection ({len(result)} elements) ===")
        for item in result:
            print(item)
    else:
        result = client.run(mode=mode, print_sets=print_sets)
        print(result)