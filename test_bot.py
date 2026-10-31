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
            board=tuple(rng.randrange(1,bot.CAP+1) if rng.random()<.7 else 0 for _ in range(16))
            for d in range(4):
                self.assertEqual(bot.NATIVE.execute_move(bot.DIRECTIONS[d],bot.pack(board)),bot.pack(bot.move(board,d)))
    def test_cap_tiles_never_merge(self):
        board=(11,11,1,0, 10,10,0,0)+(0,)*8
        self.assertEqual(bot.move(board,0),(11,11,1,0, 11,0,0,0)+(0,)*8)
        self.assertEqual(bot.NATIVE.execute_move(bot.DIRECTIONS[0],bot.pack(board)),bot.pack(bot.move(board,0)))
        column=(11,0,0,0, 11,0,0,0, 11,0,0,0, 11,0,0,0)
        for d in range(4):
            self.assertEqual(bot.move(column,d).count(11),4)
            self.assertEqual(bot.NATIVE.execute_move(bot.DIRECTIONS[d],bot.pack(column)),bot.pack(bot.move(column,d)))
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
        finally:bot.apply_spawn_model(bot.default_spawn_model())
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
        cases=[((11,11,11)+(0,)*13,True),((11,)+(0,)*15,False),
               ((11,11,10)+(0,)*13,False),((10,10,10)+(0,)*13,False),
               ((11,11,11,11)+(0,)*12,True),((11,0,11,0,0,11)+(0,)*10,True)]
        for board,expected in cases:
            self.assertEqual(bot.reached_goal(board),expected)
            self.assertEqual(bool(native(bot.pack(board))),expected)
    def test_elevens_do_not_merge(self):
        board=(11,11,1,0)+(0,)*12
        legal=bot.legal_moves(board)
        self.assertNotIn(0,[d for d,_ in legal]) # влево поле не меняется: 11+11 не сливаются
        self.assertTrue(all(a.count(11)==2 for _,a in legal))
    def test_cap_count_never_decreases(self):
        import random
        rng=random.Random(7)
        for _ in range(300):
            board=tuple(rng.choice((0,0,1,2,9,10,11,11)) for _ in range(16))
            for d,after in bot.legal_moves(board):
                self.assertGreaterEqual(after.count(bot.CAP),board.count(bot.CAP))
    def test_default_spawn_stages(self):
        p=bot.NATIVE.get_spawn_prob
        for stage in range(0,8):
            self.assertGreater(p(stage,1),.85);self.assertGreater(p(stage,2),0);self.assertEqual(p(stage,3),0)
        self.assertGreater(p(8,3),0);self.assertEqual(p(8,4),0)
        self.assertGreater(p(9,4),0);self.assertEqual(p(9,5),0)
        for stage in (10,11):
            self.assertGreater(p(stage,5),0);self.assertEqual(p(stage,6),0)
            self.assertAlmostEqual(sum(p(stage,r) for r in range(1,16)),1,places=5)
    def test_engine_uses_stage_of_node(self):
        model=bot.default_spawn_model()
        low=bot.pack((7,6,5,0, 4,3,2,0, 1,1,0,0)+(0,)*4)
        high=bot.pack((10,6,5,0, 4,3,2,0, 1,1,0,0)+(0,)*4)
        try:
            left=bot.DIRECTIONS[0] # сливает 1+1 в третьей строке
            before=(bot.NATIVE.score_levels(low,left,2),bot.NATIVE.score_levels(high,left,2))
            model["stages"]["10"]=[0,0,0,0,1]+[0]*10 # на стадии 10 падают только 32
            bot.apply_spawn_model(model)
            after=(bot.NATIVE.score_levels(low,left,2),bot.NATIVE.score_levels(high,left,2))
        finally:bot.apply_spawn_model(bot.default_spawn_model())
        self.assertGreater(before[0],0)
        self.assertEqual(before[0],after[0])    # стадия 7 не затронута
        self.assertNotEqual(before[1],after[1]) # стадия 10 берёт свою строку
    def test_record_spawn_per_stage(self):
        import json
        model=bot.default_spawn_model()
        ten=list(model["stages"]["10"])
        bot.record_spawn(model,11,6)
        self.assertEqual(model["stages"]["10"],ten)
        self.assertEqual(model["stages"]["11"][5],1)
        self.assertEqual(model["stages"]["11"][:5],ten[:5])
        self.assertEqual(json.loads(bot.format_spawn_model(model)),model)
    def test_old_spawn_model_ignored(self):
        import tempfile,json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'spawn-model.json'
            path.write_text(json.dumps({"weights":[667,63,26,13,5]}))
            with patch.object(bot,'SPAWN_LEARNED',path):
                self.assertEqual(bot.load_spawn_model(),bot.default_spawn_model())
    def test_budget_first_pass_matches_sequential(self):
        from unittest.mock import patch
        board=(10,7,6,4, 4,3,1,2, 3,2,1,0, 2,1,0,1)
        info={}
        with patch.object(bot,'THRESHOLDS',(1e-4,)):
            d=bot.choose(board,depth=5,think=60,info=info)
        self.assertEqual((info['passes'],info['threshold'],info['fallback']),(1,1e-4,False))
        scores={m:bot.NATIVE.score_levels_thresh(bot.pack(board),bot.DIRECTIONS[m],5,1e-4) for m,_ in bot.legal_moves(board)}
        self.assertEqual(d,max(scores,key=scores.get))
    def test_budget_deadline(self):
        import time
        from unittest.mock import patch
        board=(10,7,6,4, 4,3,1,2, 3,2,1,0, 2,1,0,1) # реальная позиция из play.log, 2 пустые клетки
        first={}
        with patch.object(bot,'THRESHOLDS',(1e-4,)):
            bot.choose(board,depth=8,think=60,info=first)
        think=first['search_s']*1.5+.3 # первый проход успевает, второй — нет
        info={};started=time.monotonic()
        with patch.object(bot,'GROWTH',0.0): # заставляем начать следующий проход
            d=bot.choose(board,depth=8,think=think,info=info)
        elapsed=time.monotonic()-started
        self.assertIn(d,[m for m,_ in bot.legal_moves(board)])
        self.assertEqual((info['passes'],info['cut'],info['fallback']),(1,True,False)) # второй проход прерван
        self.assertLessEqual(elapsed,think+.05)
    def test_budget_is_hard(self):
        import time
        board=(10,7,6,4, 4,3,1,2, 3,2,1,0, 2,1,0,1) # первый проход глубины 8 здесь идёт ~1 с
        for think in (0,.01,.05):
            info={};started=time.monotonic()
            d=bot.choose(board,depth=8,think=think,info=info)
            elapsed=time.monotonic()-started
            self.assertIn(d,[m for m,_ in bot.legal_moves(board)])
            self.assertTrue(info['fallback']) # ни один проход не успел: запасной ход глубины 1
            self.assertLessEqual(elapsed,think+.05)
    def test_old_engine_rejected(self):
        from types import SimpleNamespace
        old=SimpleNamespace(init_tables=lambda:None,execute_move=lambda d,b:b,solve_levels=lambda b,a,d:0)
        with self.assertRaisesRegex(RuntimeError,'старой версии.*solve_budget.*пересоберите'):
            bot.check_engine(old)
        current={n:getattr(bot.NATIVE,n) for n in bot.REQUIRED_SYMBOLS}
        current['engine_api_version']=lambda:1
        with self.assertRaisesRegex(RuntimeError,'другой версии.*пересоберите'):
            bot.check_engine(SimpleNamespace(**current))
        bot.check_engine(bot.NATIVE) # текущий движок проходит проверку
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
