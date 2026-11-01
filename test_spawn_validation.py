"""Регрессии атомарной установки spawn-модели через публичный C ABI."""
import ctypes as C
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent


class SpawnValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'vendor' / ('engine.dylib' if sys.platform == 'darwin' else 'engine.so')
        cls.lib = C.CDLL(str(path))
        cls.lib.init_tables()
        cls.lib.set_spawn_table.argtypes = [C.POINTER(C.c_float), C.c_int, C.c_int]
        cls.lib.set_spawn_table.restype = C.c_int
        cls.lib.get_spawn_prob.argtypes = [C.c_int, C.c_int]
        cls.lib.get_spawn_prob.restype = C.c_float
        cls.lib.score_levels_thresh.argtypes = [C.c_uint64, C.c_int, C.c_int, C.c_float]
        cls.lib.score_levels_thresh.restype = C.c_float
        stages = json.loads((ROOT / 'spawn-model.default.json').read_text())['stages']
        cls.default = [stages[str(max(int(k) for k in stages if int(k) <= s))]
                       for s in range(16)]

    def install(self, rows, levels=15, stages=16):
        values = [value for row in rows for value in row]
        return self.lib.set_spawn_table((C.c_float * len(values))(*values), stages, levels)

    def setUp(self):
        # Разные активные ранги по стадиям проверяют не только массив вероятностей.
        self.sentinel = []
        for stage in range(16):
            row = [0.] * 15
            row[stage % 5] = 3.
            row[(stage + 2) % 5] = 7.
            self.sentinel.append(row)
        self.assertEqual(self.install(self.sentinel), 0)

    def tearDown(self):
        self.assertEqual(self.install(self.default), 0)

    def probabilities(self):
        return [[self.lib.get_spawn_prob(stage, rank) for rank in range(1, 16)]
                for stage in range(16)]

    def scores(self):
        # Без goal и с разными стадиями; оценка использует подготовленные active ranks.
        boards = (0x1234567000000000, 0x123456789a000000, 0x123456789c000000)
        return [[self.lib.score_levels_thresh(board, direction, 2, 1e-4)
                 for direction in range(4)] for board in boards]

    def assert_rejected_unchanged(self, rows, **shape):
        before_probs, before_scores = self.probabilities(), self.scores()
        try:
            self.assertEqual(self.install(rows, **shape), -1)
            self.assertEqual(self.probabilities(), before_probs)
            self.assertEqual(self.scores(), before_scores)
        finally:
            # Даже старая сломанная библиотека не оставит модель испорченной
            # для следующего subTest или существующих тестов bot.py.
            self.assertEqual(self.install(self.sentinel), 0)

    def test_nonfinite_weights_rejected_atomically(self):
        for label, value in (('nan', float('nan')), ('inf', float('inf')),
                             ('negative inf', -float('inf'))):
            for stage in (0, 15):
                with self.subTest(value=label, stage=stage):
                    rows = [[1.] * 15 for _ in range(16)]
                    rows[stage][7] = value
                    self.assert_rejected_unchanged(rows)

    def test_finite_sum_overflow_rejected_atomically(self):
        maximum = C.c_float(float.fromhex('0x1.fffffep+127')).value
        rows = [[1.] * 15 for _ in range(16)]
        rows[15] = [maximum, maximum] + [0.] * 13
        self.assert_rejected_unchanged(rows)

    def test_negative_and_empty_rows_preserve_model(self):
        for label, bad in (('negative', [-1.] + [1.] * 14), ('empty', [0.] * 15)):
            for stage in (0, 15):
                with self.subTest(value=label, stage=stage):
                    rows = [[1.] * 15 for _ in range(16)]
                    rows[stage] = bad
                    self.assert_rejected_unchanged(rows)

    def test_invalid_dimensions_preserve_model(self):
        for stages, levels in ((15, 15), (16, 0), (16, 16)):
            with self.subTest(stages=stages, levels=levels):
                self.assert_rejected_unchanged(self.sentinel, stages=stages, levels=levels)

    def test_valid_rows_keep_float_normalization(self):
        cases = [
            [[1.25] for _ in range(16)],
            [[.1, .2, .3] for _ in range(16)],
            [[(stage + 1) * (rank + 1) / 7. for rank in range(15)]
             for stage in range(16)],
            self.sentinel,
        ]
        for rows in cases:
            levels = len(rows[0])
            with self.subTest(levels=levels, sparse=rows is self.sentinel):
                expected = []
                for row in rows:
                    inputs = [C.c_float(value).value for value in row]
                    total = 0.
                    for value in inputs:
                        total = C.c_float(total + value).value
                    expected.append([C.c_float(value / total).value for value in inputs] +
                                    [0.] * (15 - levels))
                self.assertEqual(self.install(rows, levels=levels), 0)
                self.assertEqual(self.probabilities(), expected)


if __name__ == '__main__':
    unittest.main()
