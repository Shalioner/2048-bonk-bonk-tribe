import ctypes
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

import numpy as np
import td_model as td
import value_model as mc

GOOD = (1,1,2,3)+(0,)*12
LOSS = (1,2,1,2,2,1,2,1)*2
GOAL = (11,11,11)+(0,)*13

def packed(board):
    return sum(int(value)<<(4*cell) for cell,value in enumerate(board))

class TDModelTests(unittest.TestCase):
    def test_terminal_stages_clipping_and_donor_duplicate_update(self):
        model=td.TDModel()
        model.bias[:]=(.2,.4,.6)
        for caps in range(3):
            board=(11,)*caps+(1,)+(0,)*(15-caps)
            self.assertEqual(model.predict(board),float(model.bias[caps]))
        self.assertEqual(model.predict(GOAL),1)
        self.assertEqual(model.predict(LOSS),0)
        self.assertEqual(model.predict((12,)*16),0)
        self.assertGreater(model.predict((2,)*16),0)
        before=model.weights.copy()
        for board in (GOAL,LOSS,(0,)*16):
            self.assertEqual(model.update(board,1,.2),model.predict(board))
        np.testing.assert_array_equal(model.weights,before)
        board=(1,)+(0,)*15
        expected=before.copy()
        stage,indices=td.features(board)
        increment=np.float32(np.float32(.1)*np.float32(np.float32(1)-np.float32(model.predict(board)))/np.float32(4))
        for p in range(4):
            for i in indices[p]:
                expected[stage,p,i]=np.float32(expected[stage,p,i]+increment)
        bias=model.bias.copy()
        model.update(board,1,.1)
        np.testing.assert_array_equal(model.weights,expected)
        np.testing.assert_array_equal(model.bias,bias)
        model.bias[:]=(-10,10,10)
        self.assertEqual(model.predict(GOOD),float(td.EPSILON))
        self.assertEqual(model.predict((11,1)+(0,)*14),float(np.float32(1)-td.EPSILON))

    def test_invalid_update_is_transactional_and_learning_is_symmetric(self):
        model=td.TDModel()
        before=model.weights.tobytes()
        for target,alpha in ((-1,.1),(1,math.inf),(float('nan'),.1),(1,True),(1,1.01)):
            with self.assertRaises(ValueError):model.update(GOOD,target,alpha)
            self.assertEqual(model.weights.tobytes(),before)
        with self.assertRaises(ValueError):model.update([1]*15,1,.1)
        model.update(GOOD,1,.1)
        self.assertGreater(model.predict(GOOD),.5)
        for mapping in td.SYMMETRY_MAPS:
            self.assertEqual(model.predict([GOOD[i] for i in mapping]),model.predict(GOOD))

    def test_file_roundtrip_strict_target_metadata_crc_and_finite(self):
        with tempfile.TemporaryDirectory(prefix='bonk-td-format-') as directory:
            p=Path(directory)/'model.bin'; q=Path(directory)/'copy.bin'
            model=td.TDModel();model.update(GOOD,1,.1)
            model.training.update(fitted=True,episode=3)
            model.metadata.update(seed_sha='test',source_hashes={'fixture':'abc'})
            manifest=model.save(p)
            restored=td.TDModel.load(p);restored.save(q)
            self.assertEqual(p.read_bytes(),q.read_bytes())
            self.assertEqual(restored.training,model.training)
            self.assertEqual(restored.metadata,model.metadata)
            self.assertEqual(manifest['architecture']['target_type'],2)
            np.testing.assert_array_equal(restored.weights,model.weights)
            with self.assertRaises(ValueError):mc.ValueModel.load(p)
            mc.ValueModel().save(q)
            with self.assertRaises(ValueError):td.TDModel.load(q)
            raw=p.read_bytes()
            for offset in (0,8,20,32,36,40,44,len(raw)-1):
                bad=bytearray(raw);bad[offset]^=1;q.write_bytes(bad)
                with self.assertRaises(ValueError):td.TDModel.load(q)
            size=td.HEADER.unpack_from(raw)[3]
            payload=raw[td.HEADER.size+size:]
            metadata=json.loads(raw[td.HEADER.size:td.HEADER.size+size])
            metadata['architecture']['target']='classic_score'
            encoded=mc._canonical(metadata)
            h=list(td.HEADER.unpack_from(raw));h[3]=len(encoded);h[-1]=zlib.crc32(encoded+payload)
            q.write_bytes(td.HEADER.pack(*h)+encoded+payload)
            with self.assertRaises(ValueError):td.TDModel.load(q)
            model.weights[0,0,0]=np.nan
            with self.assertRaises(ValueError):model.save(p)
            self.assertEqual(p.read_bytes(),raw)

class NativeTDModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bonk-td-header-')
        folder=Path(cls.temp.name)
        source=folder/'adapter.cpp'
        source.write_text('''#include "td_model.h"
static bonk_td::Model m;
extern "C" int load(const char *path){return m.load(path);}
extern "C" void clear(){m.clear();}
extern "C" int loaded(){return m.loaded();}
extern "C" float evaluate(uint64_t b){return m.evaluate(b);}
extern "C" float update(uint64_t b,float t,float a){return m.update(b,t,a);}
extern "C" int get(float *p,size_t n){return m.get_parameters(p,n);}
''')
        binary=folder/('adapter.dylib' if sys.platform=='darwin' else 'adapter.so')
        compiler='clang++' if sys.platform=='darwin' else 'g++'
        flags=['-dynamiclib'] if sys.platform=='darwin' else ['-shared','-fPIC']
        subprocess.run([compiler,'-O3','-std=c++11']+flags+['-I',str(td.ROOT/'vendor'),str(source),'-o',str(binary)],check=True)
        cls.lib=ctypes.CDLL(str(binary))
        cls.lib.load.argtypes=[ctypes.c_char_p];cls.lib.load.restype=ctypes.c_int
        cls.lib.evaluate.argtypes=[ctypes.c_uint64];cls.lib.evaluate.restype=ctypes.c_float
        cls.lib.update.argtypes=[ctypes.c_uint64,ctypes.c_float,ctypes.c_float];cls.lib.update.restype=ctypes.c_float
        cls.lib.get.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_size_t];cls.lib.get.restype=ctypes.c_int
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_all_stages_d4_python_native_estimate_update_parameter_bits(self):
        model=td.TDModel()
        rng=np.random.default_rng(4)
        model.weights[:]=rng.normal(0,.001,model.weights.shape).astype(np.float32)
        model.bias[:]=(.2,.4,.6)
        path=Path(self.temp.name)/'weights.bin';model.save(path)
        self.assertTrue(self.lib.load(str(path).encode()))
        boards=[GOOD,LOSS,GOAL,(12,)*16,(2,)*16,(1,)+(0,)*15]
        boards += [(11,)*caps+(1,2,1)+(0,)*(13-caps) for caps in range(3)]
        for board in boards:
            for mapping in td.SYMMETRY_MAPS:
                transformed=[board[i] for i in mapping]
                self.assertEqual(self.lib.evaluate(packed(transformed)),model.predict(transformed))
        for board in boards:
            for target,alpha in ((1,.1),(0,.03),(.7,1)):
                self.assertEqual(self.lib.update(packed(board),target,alpha),model.update(board,target,alpha))
        params=np.empty(td.PARAMETER_COUNT,dtype=np.float32)
        self.assertTrue(self.lib.get(params.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),len(params)))
        np.testing.assert_array_equal(params,td._parameters(model))
        previous=self.lib.evaluate(packed(GOOD))
        self.assertTrue(math.isnan(self.lib.update(packed(GOOD),1,float('nan'))))
        self.assertEqual(previous,self.lib.evaluate(packed(GOOD)))
    def test_failed_native_load_retains_state_and_rejects_nan(self):
        path=Path(self.temp.name)/'good.bin';bad=Path(self.temp.name)/'bad.bin'
        td.TDModel(.3).save(path);self.assertTrue(self.lib.load(str(path).encode()))
        expected=self.lib.evaluate(packed(GOOD))
        raw=bytearray(path.read_bytes());size=td.HEADER.unpack_from(raw)[3]
        struct.pack_into('<f',raw,td.HEADER.size+size,float('nan'))
        struct.pack_into('<I',raw,44,zlib.crc32(raw[td.HEADER.size:]));bad.write_bytes(raw)
        self.assertFalse(self.lib.load(str(bad).encode()))
        self.assertEqual(self.lib.evaluate(packed(GOOD)),expected)
        for content in (b'bad',path.read_bytes()+b'trailing'):
            bad.write_bytes(content);self.assertFalse(self.lib.load(str(bad).encode()))
            self.assertEqual(self.lib.evaluate(packed(GOOD)),expected)
        self.lib.clear();self.assertFalse(self.lib.loaded());self.assertTrue(math.isnan(self.lib.evaluate(packed(GOAL))))

if __name__=='__main__':unittest.main()
