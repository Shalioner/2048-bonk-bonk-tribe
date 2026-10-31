import unittest
import struct
from pathlib import Path
import bot

class BotTests(unittest.TestCase):
    def test_saved_board(self):
        board=bot.read_board((bot.ROOT/'fixtures/saved-board.png').read_bytes())
        self.assertEqual(board,(0,0,0,0,1,0,0,0,5,1,0,1,7,3,3,2))
    def test_native_move_parity(self):
        import random
        rng=random.Random(123)
        for _ in range(500):
            board=tuple(rng.randrange(14) if rng.random()<.7 else 0 for _ in range(16))
            for d in range(4):
                self.assertEqual(bot.NATIVE.execute_move(bot.DIRECTIONS[d],bot.pack(board)),bot.pack(bot.move(board,d)))
    def test_native_preserves_thirteens(self):
        board=(13,13,1,0)+(0,)*12
        d=bot.choose(board)
        self.assertGreaterEqual(bot.move(board,d).count(13),2)
    def test_crop_decode(self):
        from PIL import Image
        im=bot.decode_screen((bot.ROOT/'calibration.png').read_bytes())
        raw=im.crop((0,1130,1440,2370)).convert('RGBA').tobytes()
        data=b'CROP'+struct.pack('<IIII',1440,3040,1130,1240)+raw
        self.assertEqual(bot.read_board(data),bot.read_board((bot.ROOT/'calibration.png').read_bytes()))
    def test_broken_seven(self):
        self.assertEqual(bot.read_board((bot.ROOT/'fixtures/seven-variation.png').read_bytes()),(7,3,1,1,7,1,0,0,2,0,0,0,0,0,1,0))
    def test_strips_decode(self):
        im=bot.decode_screen((bot.ROOT/'calibration.png').read_bytes())
        data=b'STRP'+struct.pack('<IIII',1440,3040,80,4)
        for y in (1150,1460,1770,2080):
            data+=struct.pack('<I',y)+im.crop((0,y,1440,y+80)).convert('RGBA').tobytes()
        self.assertEqual(bot.read_board(data),bot.read_board((bot.ROOT/'calibration.png').read_bytes()))
    def test_live_spawn_three(self):
        before=(8,4,1,1,7,3,2,2,3,2,3,2,2,1,1,1)
        actual=bot.read_board((bot.ROOT/'fixtures/spawn-three.png').read_bytes())
        self.assertEqual(actual,(8,4,1,1,7,3,2,3,3,2,3,1,2,1,1,3))
        self.assertTrue(bot.valid_successor(bot.move(before,2),actual))
    def test_native_spawn_three_affects_search(self):
        board=bot.pack((8,4,1,1,7,3,2,2,3,2,3,2,2,1,1,1))
        try:
            bot.NATIVE.set_spawn_probs(1,0,0)
            ones=bot.NATIVE.score_levels(board,0,2)
            bot.NATIVE.set_spawn_probs(0,0,1)
            threes=bot.NATIVE.score_levels(board,0,2)
            self.assertNotEqual(ones,threes)
        finally:bot.NATIVE.set_spawn_probs(85,10,5)
    def test_reject_unexplained_change(self):
        expected=(2,)+(0,)*15
        self.assertFalse(bot.valid_successor(expected,(2,5)+(0,)*14))
        self.assertFalse(bot.valid_successor(expected,(3,3)+(0,)*14))
    def test_live_spawn_four(self):
        before=(9,0,1,0,8,4,1,0,5,5,3,3,2,1,1,1)
        actual=bot.read_board((bot.ROOT/'fixtures/spawn-four.png').read_bytes())
        self.assertEqual(actual,(9,4,0,0,8,4,2,0,5,5,3,3,2,1,1,1))
        self.assertTrue(bot.valid_successor(bot.move(before,3),actual))
    def test_wait_for_acknowledged_move(self):
        last=(1,1)+(0,)*14
        expected=bot.move(last,0)
        actual=(2,1)+(0,)*14
        self.assertFalse(bot.observation_ready(last,last,expected,last))
        self.assertFalse(bot.observation_ready(actual,last,expected,last))
        self.assertTrue(bot.observation_ready(actual,actual,expected,last))
        transient=(5,1)+(0,)*14
        self.assertFalse(bot.observation_ready(transient,transient,expected,last))
        self.assertFalse(bot.observation_ready(actual,transient,expected,last))
    def test_live_level_ten(self):
        self.assertEqual(bot.read_board((bot.ROOT/'fixtures/level-ten.png').read_bytes()),(10,3,2,1,2,1,1,0,0,2,0,0,0,0,0,0))
    def test_two_digit_levels_synthetic(self):
        import io
        from PIL import Image
        base=Image.open(bot.ROOT/'fixtures/level-ten.png').convert('RGB')
        for level in (11,12,13):
            im=base.copy();im.paste((70,40,40),(327,1164,396,1219))
            first=Image.fromarray(bot.TEMPLATES[1]).resize((19,41))
            second=Image.fromarray(bot.TEMPLATES[level%10]).resize((25,41))
            im.paste((245,240,225),(333,1168),first)
            im.paste((245,240,225),(356,1168),second)
            data=io.BytesIO();im.save(data,format='PNG')
            self.assertEqual(bot.read_board(data.getvalue())[0],level)
    def test_full_frame_fallback(self):
        from unittest.mock import patch
        data=(bot.ROOT/'fixtures/level-ten.png').read_bytes()
        with patch.object(bot,'read_board',side_effect=[RuntimeError('temporary crop OCR failure'),(10,)+(0,)*15]):
            calls=[]
            board,frame,used=bot.read_with_fallback(b'failed frame',lambda:calls.append(1) or data)
        self.assertTrue(used)
        self.assertEqual(calls,[1])
        self.assertEqual(board,(10,)+(0,)*15)
        self.assertEqual(frame,data)
    def test_icon_resolves_bad_digit_three(self):
        from unittest.mock import patch
        original=bot.mask
        def wrong_digit(im,r,c,raw=False):
            if (r,c)==(1,1) and not raw:return bot.TEMPLATES[4]
            return original(im,r,c,raw=raw)
        with patch.object(bot,'mask',side_effect=wrong_digit):
            board=bot.read_board((bot.ROOT/'fixtures/icon-three-vs-four.png').read_bytes())
        self.assertEqual(board[5],3)
    def test_successor(self):
        expected=(2,)+(0,)*15
        self.assertTrue(bot.valid_successor(expected,(2,1)+(0,)*14))
        self.assertFalse(bot.valid_successor(expected,(3,1)+(0,)*14))
    def test_goal(self):
        import ctypes
        native=bot.NATIVE.goal_levels
        native.argtypes=[ctypes.c_uint64];native.restype=ctypes.c_int
        cases=[((13,13,13)+(0,)*13,True),((13,)+(0,)*15,False),
               ((13,13,12)+(0,)*13,False),((11,11,11)+(0,)*13,False),
               ((14,13,13)+(0,)*13,False)]
        for board,expected in cases:
            self.assertEqual(bot.reached_goal(board),expected)
            self.assertEqual(bool(native(bot.pack(board))),expected)
    def test_elevens_can_merge(self):
        board=(11,11,1,0)+(0,)*12
        self.assertIn(0,[d for d,_ in bot.legal_moves(board)])
    def test_preserves_thirteens_when_possible(self):
        board=(13,13,1,0)+(0,)*12
        self.assertTrue(all(a.count(13)>=2 for _,a in bot.legal_moves(board)))
    def test_merge_once(self):
        self.assertEqual(bot.move((1,1,1,1)+(0,)*12,0),(2,2,0,0)+(0,)*12)
    def test_calibration(self):
        self.assertEqual(bot.read_board((bot.ROOT/'calibration.png').read_bytes()),(1,0,0,0,2,0,0,0,1,4,1,0,3,5,3,0))
    def test_raw_matches_png(self):
        data=(bot.ROOT/'calibration.png').read_bytes()
        im=bot.decode_screen(data)
        raw=struct.pack('<IIII',*im.size,1,0)+im.convert('RGBA').tobytes()
        self.assertEqual(bot.read_board(raw),bot.read_board(data))

if __name__=='__main__':unittest.main()
