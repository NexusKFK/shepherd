"""The 期权计划 page reprices in Swift (OptionMath.swift); this keeps it numerically identical to workbench/optionviz.py."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench import optionviz as ov

SRC = Path(__file__).parent / "native" / "Sources" / "Shepherd"
CASES = [("C", 452.4, 460, 15 / 365, 0.379), ("C", 452.4, 480, 15 / 365, 0.366), ("P", 48.0, 45, 64 / 365, 0.285),
         ("C", 372.5, 410, 155 / 365, 0.27), ("C", 475.0, 460, 8 / 365, 0.36), ("P", 100.0, 100, 1 / 365, 0.5),
         ("C", 100.0, 90, 0.0, 0.3), ("P", 100.0, 90, 0.0, 0.3), ("C", 58.3, 65, 11 / 365, 0.963)]


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
class SwiftParityTests(unittest.TestCase):
    def test_swift_prices_match_python(self):
        lines = "\n".join(f'print(String(format: "%.8f", OptMath.price("{r}", S: {S}, K: {K}, T: {T}, iv: {iv})))' for r, S, K, T, iv in CASES)
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main.swift"
            main.write_text("import Foundation\n" + lines + "\n")
            binary = Path(d) / "parity"
            build = subprocess.run(["swiftc", "-swift-version", "5", str(SRC / "OptionMath.swift"), str(main), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-1500:])
            out = subprocess.run([str(binary)], capture_output=True, text=True).stdout.split()
        self.assertEqual(len(out), len(CASES))
        for (r, S, K, T, iv), got in zip(CASES, out):
            self.assertAlmostEqual(float(got), ov.bs_price(r, S, K, T, iv), places=6, msg=f"{r} {S} {K} {T} {iv}")


if __name__ == "__main__":
    unittest.main()
