"""Контракт кэша: одинаковые горизонт/вероятность дают reference-оценку."""
import ctypes
import unittest

import bot


class SearchCacheTests(unittest.TestCase):
    WITNESS = (0,0,0,0, 0,1,1,0, 0,1,3,0, 2,3,2,11)
    # Источник: baseline/training.json, первые снимки cap1/cap2 этих seed.
    SAMPLES = (
        (0,0,0,2, 1,1,3,5, 0,3,5,3, 0,0,1,11),  # 202611010, cap1
        (0,6,1,11, 1,4,5,11, 0,2,4,3, 1,2,3,1),  # 202611011, cap2
        (3,0,0,0, 6,2,1,0, 11,4,0,5, 11,4,1,0),  # 202611012, cap2
    )
    CLOCKWISE = (2,3,1,0)  # Python L,R,U,D -> U,D,R,L

    def setUp(self):
        bot.apply_spawn_model(bot.default_spawn_model())
        if hasattr(bot.NATIVE,'score_levels_reference'):
            reference = bot.NATIVE.score_levels_reference
            reference.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_float]
            reference.restype = ctypes.c_float

    @staticmethod
    def rotate(board):
        return tuple(board[(3-c)*4+r] for r in range(4) for c in range(4))

    @staticmethod
    def scores(board, depth, threshold, reference=False):
        function = bot.NATIVE.score_levels_reference if reference else bot.NATIVE.score_levels_thresh
        function.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_float]
        function.restype = ctypes.c_float
        return {d: float(function(bot.pack(board), bot.DIRECTIONS[d], depth, threshold))
                for d,_ in bot.legal_moves(board)}

    def test_rotation_witness_no_longer_changes_decision(self):
        board = self.WITNESS
        rotated = self.rotate(board)
        for d,_ in bot.legal_moves(board):
            self.assertEqual(self.rotate(bot.move(board,d)), bot.move(rotated,self.CLOCKWISE[d]))
        for threshold in (0.0, 1e-4):
            original = self.scores(board,3,threshold)
            actual = self.scores(rotated,3,threshold)
            mapped = {d:actual[self.CLOCKWISE[d]] for d in original}
            self.assertEqual(max(original,key=original.get), max(mapped,key=mapped.get))
            for d in original:
                self.assertAlmostEqual(original[d],mapped[d],delta=1.0)

    def test_probability_context_not_reused(self):
        # Простая замена <= на == без cprob-ключа здесь расходится с reference
        # на 20 баллов (depth4, исходный default cutoff).
        board = self.SAMPLES[0]
        native_direction = bot.DIRECTIONS[3]
        cached = bot.NATIVE.score_levels_thresh(bot.pack(board),native_direction,4,1e-4)
        reference = self.scores(board,4,1e-4,reference=True)[3]
        self.assertEqual(cached,reference)

    def test_cached_scores_match_uncached_reference(self):
        for sample,board in enumerate((self.WITNESS,)+self.SAMPLES):
            cases = ((2,0.0),(3,0.0),(2,1e-4),(3,1e-4),(4,1e-4))
            # Глубокий unpruned reference ограничен плотным реальным полем.
            if sample == 2:
                cases += ((4,0.0),)
            for depth,threshold in cases:
                with self.subTest(sample=sample,depth=depth,threshold=threshold):
                    self.assertEqual(self.scores(board,depth,threshold),
                                     self.scores(board,depth,threshold,reference=True))

    def test_rotations_preserve_scores_with_rounding_tolerance(self):
        for sample,board in enumerate(self.SAMPLES):
            for depth,threshold in ((2,0.0),(3,0.0),(4,1e-4)):
                original = self.scores(board,depth,threshold)
                rotated = board
                mapping = tuple(range(4))
                for turns in range(1,4):
                    rotated = self.rotate(rotated)
                    mapping = tuple(self.CLOCKWISE[d] for d in mapping)
                    actual = self.scores(rotated,depth,threshold)
                    with self.subTest(sample=sample,depth=depth,threshold=threshold,turns=turns):
                        for d,score in original.items():
                            self.assertAlmostEqual(score,actual[mapping[d]],
                                                   delta=max(1.0,abs(score)*1e-6))

    def test_reference_preserves_invalid_move_and_goal(self):
        blocked = (1,2,1,2, 2,1,2,1)*2
        for d in range(4):
            self.assertEqual(float(bot.NATIVE.score_levels_reference(bot.pack(blocked),
                                   bot.DIRECTIONS[d],3,1e-4)),0.0)
        goal = (11,11,11,0)+(0,)*12
        self.assertEqual(self.scores(goal,3,1e-4),self.scores(goal,3,1e-4,reference=True))


if __name__ == '__main__':
    unittest.main()
