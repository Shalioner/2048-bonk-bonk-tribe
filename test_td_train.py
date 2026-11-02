import ctypes
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import td_model as td

NEAR_GOAL=[11,11,10,10]+[0]*12
SPAWN_GOAL=[11,11,1]+[0]*13
FORCED_LOSS=[0]+list(range(1,16))

def only(rank):
    table=np.zeros((16,15),dtype=np.float32);table[:,rank-1]=1;return table

class NativeTrainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bonk-td-trainer-')
        cls.binary=td.build_trainer(Path(cls.temp.name)/('trainer.dylib' if sys.platform=='darwin' else 'trainer.so'))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def trainer(self):
        trainer=td.NativeTrainer(self.binary);self.addCleanup(trainer.close);return trainer
    def test_goal_after_move_no_spawn_terminal_not_updated(self):
        trainer=self.trainer();trainer.set_spawn_table(only(15))
        for algorithm in ('td0','mc','observe'):
            before=td._parameters(trainer.model()).tobytes()
            result=trainer.episode(2**64-1,algorithm=algorithm,behavior='selfplay',epsilon=0,start_board=NEAR_GOAL,max_moves=1)
            self.assertEqual((result['result'],result['moves'],result['caps'],result['updates']),('goal',1,3,0))
            self.assertEqual(sum(v!=0 for v in result['board']),3)
            self.assertNotIn(15,result['board'])
            self.assertEqual(result['first_caps']['2'],NEAR_GOAL)
            self.assertEqual(td._parameters(trainer.model()).tobytes(),before)
    def test_spawn_goal_and_actual_no_legal_loss_use_binary_targets(self):
        trainer=self.trainer()
        for algorithm in ('td0','mc'):
            trainer.reset();trainer.set_spawn_table(only(11))
            result=trainer.episode(9,algorithm=algorithm,behavior='selfplay',epsilon=0,start_board=SPAWN_GOAL,max_moves=1)
            self.assertEqual((result['result'],result['moves'],result['updates']),('goal',1,1))
            self.assertEqual(result['caps'],3)
            model=trainer.model();self.assertTrue((model.weights>=0).all());self.assertTrue((model.weights>0).any())
            trainer.reset();trainer.set_spawn_table(only(15))
            result=trainer.episode(9,algorithm=algorithm,behavior='teacher',start_board=FORCED_LOSS,max_moves=1)
            self.assertEqual((result['result'],result['moves'],result['updates']),('loss',1,1))
            self.assertNotIn(0,result['board'])
            self.assertEqual(result['first_caps'],{'1':FORCED_LOSS}) # only the valid start, never terminal spawn
            model=trainer.model();self.assertTrue((model.weights<=0).all());self.assertTrue((model.weights<0).any())
    def test_teacher_digest_identical_for_mc_td0_observe_and_different_weights(self):
        trainer=self.trainer();trainer.set_spawn_table(only(15))
        digests=[]
        for algorithm,prior in (('td0',.9),('mc',.1),('observe',.7)):
            trainer.reset(prior)
            result=trainer.episode(54,algorithm=algorithm,behavior='teacher',start_board=FORCED_LOSS,max_moves=4)
            self.assertEqual(result['result'],'loss');digests.append(result['trajectory_digest'])
        self.assertEqual(len(set(digests)),1)
        # Several TD targets are attempted on the same fixed teacher trajectory.
        results=[]
        for algorithm,prior in (('td0',.8),('mc',.2),('observe',.6)):
            trainer.reset(prior);trainer.set_spawn_table(only(1))
            results.append(trainer.episode(109,algorithm=algorithm,behavior='teacher',start_board=[1,2]+[0]*14,max_moves=4))
        self.assertEqual(len(set(r['trajectory_digest'] for r in results)),1)
        self.assertEqual(results[0]['attempted_updates'],3)
        self.assertEqual(results[0]['updates'],0)
    def test_observe_unchanged_limit_rolls_back_and_initial_has_two_spawns(self):
        trainer=self.trainer();trainer.set_spawn_table(only(1))
        before=td._parameters(trainer.model()).tobytes()
        for algorithm in ('td0','mc','observe'):
            result=trainer.episode(42,algorithm=algorithm,behavior='selfplay',epsilon=.3,max_moves=4)
            self.assertEqual(result['result'],'limit');self.assertEqual(result['updates'],0)
            self.assertEqual(td._parameters(trainer.model()).tobytes(),before)
        result=trainer.episode(42,algorithm='observe',max_moves=0)
        self.assertEqual(result['moves'],0);self.assertEqual(sum(v!=0 for v in result['board']),2)
        self.assertEqual(sum(result['board']),2)
        started=trainer.episode(42,algorithm='observe',start_board=SPAWN_GOAL,max_moves=0)
        self.assertEqual(started['board'],SPAWN_GOAL)
    def test_validation_before_mutation_native_and_python(self):
        trainer=self.trainer();before=td._parameters(trainer.model()).tobytes()
        bad=[dict(seed=-1),dict(seed=2**64),dict(seed=True),dict(seed=0,alpha=float('nan')),
             dict(seed=0,epsilon=1.1),dict(seed=0,teacher_depth=2),dict(seed=0,algorithm='other'),
             dict(seed=0,start_board=[11,11,11]+[0]*13),dict(seed=0,start_board=[0]*16),
             dict(seed=0,start_board=[16]+[0]*15),dict(seed=0,max_moves=-1)]
        for kwargs in bad:
            with self.assertRaises(ValueError):trainer.episode(**kwargs)
            self.assertEqual(td._parameters(trainer.model()).tobytes(),before)
        badtable=only(1);badtable[5,0]=np.nan
        with self.assertRaises(ValueError):trainer.set_spawn_table(badtable)
        badmodel=td.TDModel();badmodel.weights[0,1,2]=np.inf
        with self.assertRaises(ValueError):trainer.set_model(badmodel)
        # Bypass wrapper: native validation must also reject without partial mutation.
        output=td._Episode()
        status=trainer._lib.td_train_episode(trainer._handle,0,0,0,1,float('nan'),0,1,0,0,ctypes.byref(output),ctypes.sizeof(output))
        self.assertNotEqual(status,0)
        params=np.zeros(td.PARAMETER_COUNT,dtype=np.float32);params[-1]=np.nan
        status=trainer._lib.td_train_set_parameters(trainer._handle,params.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),len(params))
        self.assertNotEqual(status,0)
        self.assertEqual(td._parameters(trainer.model()).tobytes(),before)
    def test_handle_isolation_and_exact_resume_parameter_transfer(self):
        first=self.trainer();second=self.trainer()
        first.reset(.4);second.reset(.8);first.set_spawn_table(only(11));second.set_spawn_table(only(15))
        result=first.episode(5,start_board=SPAWN_GOAL,max_moves=1,behavior='selfplay',epsilon=0)
        self.assertEqual(result['result'],'goal')
        self.assertTrue((second.model().weights==0).all())
        checkpoint=first.model();checkpoint.metadata={'fixture':1};checkpoint.training={'episodes':1}
        path=Path(self.temp.name)/'checkpoint.bin';checkpoint.save(path)
        second.set_model(td.TDModel.load(path));second.set_spawn_table(only(11))
        r1=first.episode(15,start_board=SPAWN_GOAL,max_moves=1,behavior='selfplay',epsilon=0)
        r2=second.episode(15,start_board=SPAWN_GOAL,max_moves=1,behavior='selfplay',epsilon=0)
        self.assertEqual(r1,r2)
        np.testing.assert_array_equal(td._parameters(first.model()),td._parameters(second.model()))
        self.assertEqual(second.model().metadata,{'fixture':1})
        self.assertEqual(second.model().training,{'episodes':1})
        # Each handle's spawn table is restored before its next episode.
        second.set_spawn_table(only(15));first.reset();second.reset()
        r1=first.episode(6,start_board=SPAWN_GOAL,max_moves=1,algorithm='observe',behavior='selfplay',epsilon=0)
        r2=second.episode(6,start_board=SPAWN_GOAL,max_moves=1,algorithm='observe',behavior='selfplay',epsilon=0)
        self.assertEqual(r1['result'],'goal');self.assertEqual(r2['result'],'limit')
        self.assertIn(15,r2['board'])

if __name__=='__main__':unittest.main()
