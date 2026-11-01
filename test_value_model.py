import ctypes
import math
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

import numpy as np

import value_model as vm


GOOD = (1, 1, 2, 3) + (0,) * 12
BAD = (8, 3, 1, 4, 2, 7, 1, 2) + (0,) * 8
LOSS = (1, 2, 1, 2, 2, 1, 2, 1) * 2
GOAL = (11, 11, 11) + (0,) * 13


def examples(offset=0, duplicates=1, weighted=False):
    return [dict(seed=offset + seed, board=list(GOOD if seed % 2 else BAD),
                 label=seed % 2, episode_weight=1 / duplicates if weighted else 1)
            for seed in range(1, 13) for _ in range(duplicates)]


class ValueModelTests(unittest.TestCase):
    def test_features_known_encoding_and_d4(self):
        board = tuple(range(16))
        stage, indices = vm.features(board)
        self.assertEqual(stage, 1)
        self.assertEqual(indices.shape, (4, 8))
        self.assertEqual(int(indices[0, 0]), 0x3210)
        self.assertEqual(int(indices[1, 0]), 0x5410)
        self.assertEqual(int(indices[0, 1]), 0x048c)
        self.assertEqual(int(indices[0, 4]), 0x0123)
        for mapping in vm.SYMMETRY_MAPS:
            transformed = tuple(board[i] for i in mapping)
            other_stage, other = vm.features(transformed)
            self.assertEqual(stage, other_stage)
            np.testing.assert_array_equal(np.sort(indices, axis=1), np.sort(other, axis=1))

    def test_neutral_stages_and_terminal_semantics(self):
        model = vm.ValueModel()
        self.assertEqual(model.predict(GOOD), 0.5)
        model.bias[:] = (-2, 0, 2)
        for caps, expected in ((0, 1 / (1 + math.exp(2))), (1, 0.5), (2, 1 / (1 + math.exp(-2)))):
            board = (11,) * caps + (1,) + (0,) * (15 - caps)
            self.assertEqual(vm.features(board)[0], caps)
            self.assertAlmostEqual(model.predict(board), expected, places=7)
        self.assertEqual(model.predict(GOAL), 1)
        self.assertEqual(model.predict(LOSS), 0)
        self.assertEqual(model.predict((12,) * 16), 0)  # >=CAP не сливаются и не считаются CAP.
        self.assertGreater(model.predict((2,) * 16), 0)  # Пара2 остаётся легальным слиянием.

    def test_learning_determinism_and_held_episode_gate(self):
        first, second = vm.ValueModel(), vm.ValueModel()
        settings = dict(validation_records=examples(100), epochs=8, learning_rate=0.8,
                        batch_size=32, seed=42, l2=1e-4)
        metrics = first.fit(examples(), **settings)
        self.assertTrue(metrics["validation_gate_passed"])
        self.assertLess(metrics["validation_brier"], metrics["stage_prior_validation_brier"])
        self.assertGreater(first.predict(GOOD), first.predict(BAD))
        second.fit(examples(), **settings)
        np.testing.assert_array_equal(first.bias, second.bias)
        np.testing.assert_array_equal(first.weights, second.weights)
        for mapping in vm.SYMMETRY_MAPS:
            self.assertAlmostEqual(first.predict(tuple(GOOD[i] for i in mapping)), first.predict(GOOD), places=7)
        with self.assertRaisesRegex(ValueError, "разные seed"):
            first.fit(examples(), validation_records=examples())
        np.testing.assert_array_equal(first.weights, second.weights)
        no_validation = vm.ValueModel().fit(examples(), epochs=1)
        self.assertFalse(no_validation["validation_gate_passed"])

    def test_episode_weights_do_not_double_weight_long_games(self):
        plain = vm._dataset(examples(duplicates=7))
        prepared = vm._dataset(examples(duplicates=7, weighted=True))
        np.testing.assert_allclose(plain["weights"], prepared["weights"], rtol=0, atol=1e-15)
        for seed in plain["seeds"]:
            mask = np.asarray([r["seed"] == seed for r in plain["records"]])
            self.assertAlmostEqual(plain["weights"][mask].sum(), 1)
        long_record = examples() + [dict(seed=999, board=list(GOOD), label=1)] * 100
        data = vm._dataset(long_record)
        self.assertAlmostEqual(sum(data["weights"][-100:]), 1)
        with self.assertRaisesRegex(ValueError, "MC label"):
            vm.ValueModel().fit([dict(seed=1, board=GOOD, label=1), dict(seed=1, board=BAD, label=0)])

    def test_save_load_roundtrip_and_rules_validation(self):
        model = vm.ValueModel()
        model.fit(examples(), validation_records=examples(100), epochs=2, seed=3)
        model.metadata.update(source_sha256={"fixture": "0123456789abcdef"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            meta = model.save(path)
            restored = vm.ValueModel.load(path)
            self.assertEqual(meta["architecture"], vm.ARCHITECTURE)
            self.assertEqual(restored.training, model.training)
            self.assertEqual(restored.metadata, model.metadata)
            np.testing.assert_array_equal(restored.weights, model.weights)
            np.testing.assert_array_equal(restored.bias, model.bias)
            self.assertEqual(restored.predict(GOOD), model.predict(GOOD))
            other = Path(directory) / "copy.bin"
            restored.save(other)
            self.assertEqual(path.read_bytes(), other.read_bytes())
            raw = bytearray(path.read_bytes())
            struct.pack_into("<I", raw, 32, 13)
            other.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "правила"):
                vm.ValueModel.load(other)
            other.write_bytes(path.read_bytes() + b"trailing")
            with self.assertRaisesRegex(ValueError, "размер"):
                vm.ValueModel.load(other)
            model.weights[0, 0, 0] = np.nan
            with self.assertRaisesRegex(ValueError, "NaN"):
                model.save(path)
            self.assertEqual(vm.ValueModel.load(path).predict(GOOD), restored.predict(GOOD))

    def test_reject_bad_boards_labels_and_hyperparameters(self):
        for board in ((1,) * 15, (16,) + (0,) * 15, (1.5,) + (0,) * 15, (True,) + (0,) * 15):
            with self.assertRaises(ValueError):
                vm.features(board)
        with self.assertRaisesRegex(ValueError, "терминальному"):
            vm.ValueModel().fit([dict(seed=1, board=GOAL, label=0)])
        with self.assertRaises(ValueError):
            vm.ValueModel().fit(examples(), learning_rate=float("inf"))
        with self.assertRaises(ValueError):
            vm.ValueModel().fit(examples(), seed=-1)


class NativeValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bonk-value-test-")
        folder = Path(cls.temp.name)
        source = folder / "adapter.cpp"
        source.write_text('''#include "value_model.h"
static bonk_value::Model model;
extern "C" int value_load(const char *path) { return path && model.load(path); }
extern "C" float value_evaluate(uint64_t board) { return model.evaluate(board); }
extern "C" void value_clear() { model.clear(); }
extern "C" int value_loaded() { return model.loaded(); }
''')
        binary = folder / ("adapter.dylib" if sys.platform == "darwin" else "adapter.so")
        compiler = shutil.which("clang++" if sys.platform == "darwin" else "g++")
        if not compiler:
            raise unittest.SkipTest("Для C++ parity требуется штатный компилятор проекта")
        flags = [compiler, "-O3", "-std=c++11"]
        flags += ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        subprocess.run(flags + ["-I", str(Path(__file__).parent / "vendor"), str(source), "-o", str(binary)], check=True)
        cls.native = ctypes.CDLL(str(binary))
        cls.native.value_load.argtypes = [ctypes.c_char_p]
        cls.native.value_load.restype = ctypes.c_int
        cls.native.value_evaluate.argtypes = [ctypes.c_uint64]
        cls.native.value_evaluate.restype = ctypes.c_float
        cls.native.value_loaded.restype = ctypes.c_int
        cls.native.value_clear.restype = None

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.native.value_clear()

    @staticmethod
    def packed(board):
        return sum(int(v) << (4 * i) for i, v in enumerate(board))

    def test_python_cpp_predictions_all_stages_and_symmetries(self):
        rng = np.random.default_rng(50)
        model = vm.ValueModel()
        model.weights[:] = rng.normal(0, 0.2, model.weights.shape).astype(np.float32)
        model.bias[:] = (-0.5, 0.5, 1.5)
        path = Path(self.temp.name) / "random.bin"
        model.save(path)
        self.assertTrue(self.native.value_load(str(path).encode()))
        boards = [GOOD, BAD, LOSS, GOAL, (12,) * 16, (2,) * 16]
        python_rng = random.Random(391)
        boards += [tuple(python_rng.randrange(16) if python_rng.random() < 0.7 else 0 for _ in range(16)) for _ in range(40)]
        boards += [(11,) * caps + (1,) + (0,) * (15 - caps) for caps in range(3)]
        for board in boards:
            expected = model.predict(board)
            for mapping in vm.SYMMETRY_MAPS:
                transformed = tuple(board[i] for i in mapping)
                actual = self.native.value_evaluate(self.packed(transformed))
                self.assertTrue(math.isfinite(actual))
                self.assertAlmostEqual(actual, expected, places=7)

    def test_malformed_load_preserves_previous_model(self):
        path = Path(self.temp.name) / "valid.bin"
        model = vm.ValueModel()
        model.bias[0] = 1
        model.save(path)
        self.assertTrue(self.native.value_load(str(path).encode()))
        prediction = self.native.value_evaluate(self.packed(GOOD))
        original = path.read_bytes()
        variants = [b"", original[:100], original + b"extra"]
        for offset, replacement in ((0, b"CLASSIC!"), (8, struct.pack("<I", 9)),
                                    (32, struct.pack("<I", 13)), (36, struct.pack("<I", 1)),
                                    (40, struct.pack("<I", 2))):
            changed = bytearray(original)
            changed[offset:offset + len(replacement)] = replacement
            variants.append(changed)
        broken = bytearray(original)
        broken[-1] ^= 1
        variants.append(broken)
        for bad_float in (float("nan"), float("inf"), -float("inf")):
            changed = bytearray(original)
            metadata_length = struct.unpack_from("<I", changed, 16)[0]
            struct.pack_into("<f", changed, vm.HEADER.size + metadata_length, bad_float)
            struct.pack_into("<I", changed, 44, zlib.crc32(changed[vm.HEADER.size:]))
            variants.append(changed)
        bad = Path(self.temp.name) / "bad.bin"
        for raw in variants:
            bad.write_bytes(raw)
            self.assertFalse(self.native.value_load(str(bad).encode()))
            self.assertTrue(self.native.value_loaded())
            self.assertEqual(self.native.value_evaluate(self.packed(GOOD)), prediction)
            with self.assertRaises(ValueError):
                vm.ValueModel.load(bad)
        self.native.value_clear()
        self.assertFalse(self.native.value_loaded())
        self.assertTrue(math.isnan(self.native.value_evaluate(self.packed(GOOD))))


if __name__ == "__main__":
    unittest.main()
