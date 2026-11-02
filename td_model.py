"""Bonk TD/MC afterstate value, adapted from pinned moporgic/TDL2048.

Source: a99f620aec0d30a75943a4c9646743f1f53b0197, 2048.cpp:
method::isomorphic::estimate/optimize (1510–1538), state::instruct (1771–1776),
run optimize:forward (2037–2057). The adaptation changes the task to three
unmergeable rank11 tiles, stage0/1/2, clipped-linear binary success value,
four4-tuples and Bonk's moves/spawn; no donor game engine or trained weights run.

MIT License
Copyright (c) 2021 Hung Guei
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import copy
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

import numpy as np

from value_model import (CAP, GOAL_COUNT, STAGES, TABLE_SIZE, PATTERNS, PATTERN_COUNT,
                         SYMMETRY_COUNT, WEIGHT_COUNT, FEATURE_CELLS, SYMMETRY_MAPS,
                         _board, _terminal, _canonical, features)

ROOT = Path(__file__).resolve().parent
VERSION = 1
MAGIC = b"BONKTD01"
TARGET_TYPE = 2
HEADER = struct.Struct("<8s10I")
MAX_METADATA_BYTES = 1024 * 1024
PARAMETER_COUNT = STAGES + WEIGHT_COUNT
PAYLOAD_BYTES = 4 * PARAMETER_COUNT
EPSILON = np.float32(1e-6)
RESERVED_METADATA = frozenset(("format", "version", "architecture", "parameters_sha256", "training", "donor"))
DONOR = dict(repo="moporgic/TDL2048", commit="a99f620aec0d30a75943a4c9646743f1f53b0197",
             source="2048.cpp", source_sha256="936e045167e2d168ad45095194ebd12ffc7d7aa79d4fe335dafe119d9a1bdaff",
             license="MIT", copyright="Copyright (c) 2021 Hung Guei")
ARCHITECTURE = dict(alphabet=16, cap=CAP, goal_count=GOAL_COUNT, stages=STAGES,
                    patterns=[list(p) for p in PATTERNS], symmetries="D4", symmetry_count=8,
                    normalization=8, target="td_afterstate_three_unmergeable_rank11", target_type=2,
                    goal_value=1, loss_value=0, link="clipped_linear", clip_epsilon=1e-6,
                    update="float32 alpha*(target-predict)/4 per occurrence; bias fixed")


def _probability(value, name):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(name + " должен быть числом в[0,1]")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(name + " должен быть конечным числом в[0,1]")
    return np.float32(value)


def _parameters(model):
    if (model.weights.shape != (STAGES, PATTERN_COUNT, TABLE_SIZE) or model.bias.shape != (STAGES,) or
            model.weights.dtype != np.float32 or model.bias.dtype != np.float32):
        raise ValueError("Параметры TD должны иметь согласованную форму и dtype float32")
    if not np.isfinite(model.weights).all() or not np.isfinite(model.bias).all():
        raise ValueError("Параметры TD содержат NaN/Inf")
    return np.concatenate((model.bias, model.weights.reshape(-1))).astype(np.float32, copy=False)


def _atomic_write(path, raw):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class TDModel:
    """Перенос n-tuple estimate/update; это не сигмоидальный MC-пилот."""

    def __init__(self, initial_value=0.5):
        initial_value = _probability(initial_value, "initial_value")
        self.bias = np.full(STAGES, initial_value, dtype=np.float32)
        self.weights = np.zeros((STAGES, PATTERN_COUNT, TABLE_SIZE), dtype=np.float32)
        self.metadata = {}
        self.training = dict(fitted=False, initial_value=float(initial_value), calibrated_probability=False)

    def predict(self, board):
        b = _board(board)
        terminal = _terminal(b)
        if terminal is not None:
            return float(terminal)
        stage, indices = features(b)
        total = self.weights[stage, np.arange(PATTERN_COUNT)[:, None], indices].sum(dtype=np.float64)
        raw = np.float32(float(self.bias[stage]) + total / SYMMETRY_COUNT)
        return float(np.clip(raw, EPSILON, np.float32(1) - EPSILON))

    def update(self, board, target, alpha):
        b = _board(board)
        target = _probability(target, "target")
        alpha = _probability(alpha, "alpha")
        prediction = np.float32(self.predict(b))
        if not np.isfinite(prediction):
            raise ValueError("Предсказание TD содержит NaN/Inf")
        if _terminal(b) is not None:
            return float(prediction)
        stage, indices = features(b)
        error = np.float32(alpha * np.float32(target - prediction))
        increment = np.float32(error / np.float32(PATTERN_COUNT))
        # Сохраняется мультипликативность вхождений donor isomorphic optimize:
        # повторный индекс обновляется повторно, а не один раз после unique().
        np.add.at(self.weights[stage], (np.repeat(np.arange(PATTERN_COUNT), SYMMETRY_COUNT),
                                      indices.reshape(-1)), increment)
        return self.predict(b)

    def save(self, path, *, metadata=None):
        payload = _parameters(self).astype("<f4").tobytes()
        extra = copy.deepcopy(self.metadata)
        if metadata is not None:
            extra.update(metadata)
        if RESERVED_METADATA.intersection(extra):
            raise ValueError("Metadata не должны подменять архитектуру, цель или training TD")
        manifest = dict(extra, format="bonk-td-afterstate-value", version=VERSION,
                        architecture=copy.deepcopy(ARCHITECTURE), donor=copy.deepcopy(DONOR),
                        parameters_sha256=hashlib.sha256(payload).hexdigest(), training=self.training)
        encoded = _canonical(manifest)
        if not 1 <= len(encoded) <= MAX_METADATA_BYTES:
            raise ValueError("Некорректный размер TD metadata")
        header = HEADER.pack(MAGIC, VERSION, HEADER.size, len(encoded), STAGES, PATTERN_COUNT,
                             TABLE_SIZE, CAP, GOAL_COUNT, TARGET_TYPE, zlib.crc32(encoded + payload))
        _atomic_write(path, header + encoded + payload)
        return copy.deepcopy(manifest)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > HEADER.size + MAX_METADATA_BYTES + PAYLOAD_BYTES:
            raise ValueError("Слишком большой TD-файл")
        raw = path.read_bytes()
        if len(raw) < HEADER.size:
            raise ValueError("Обрезанный TD header")
        magic, version, header_size, meta_size, stages, patterns, table, cap, goal, target, crc = HEADER.unpack_from(raw)
        if (magic, version, header_size, stages, patterns, table, cap, goal, target) != (
                MAGIC, VERSION, HEADER.size, STAGES, PATTERN_COUNT, TABLE_SIZE, CAP, GOAL_COUNT, TARGET_TYPE):
            raise ValueError("Несовместимые формат/архитектура/правила/цель TD")
        if not 1 <= meta_size <= MAX_METADATA_BYTES or len(raw) != HEADER.size + meta_size + PAYLOAD_BYTES:
            raise ValueError("Некорректный размер TD-файла")
        if zlib.crc32(raw[HEADER.size:]) != crc:
            raise ValueError("CRC32 TD не совпадает")
        encoded, payload = raw[HEADER.size:HEADER.size + meta_size], raw[HEADER.size + meta_size:]
        try:
            manifest = json.loads(encoded)
            if encoded != _canonical(manifest):
                raise ValueError("TD metadata должны быть каноническим JSON")
            valid = (manifest["format"] == "bonk-td-afterstate-value" and manifest["version"] == VERSION and
                     _canonical(manifest["architecture"]) == _canonical(ARCHITECTURE) and
                     _canonical(manifest["donor"]) == _canonical(DONOR) and
                     manifest["parameters_sha256"] == hashlib.sha256(payload).hexdigest() and
                     isinstance(manifest["training"], dict))
            if not valid:
                raise ValueError("Несовместимые TD metadata")
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Некорректные TD metadata") from exc
        parameters = np.frombuffer(payload, dtype="<f4")
        if not np.isfinite(parameters).all():
            raise ValueError("TD-параметры содержат NaN/Inf")
        model = cls()
        model.bias[:] = parameters[:STAGES]
        model.weights[:] = parameters[STAGES:].reshape(model.weights.shape)
        model.training = copy.deepcopy(manifest["training"])
        model.metadata = {key: copy.deepcopy(value) for key, value in manifest.items() if key not in RESERVED_METADATA}
        return model


def build_trainer(path):
    """Явная сборка локального порта; import/resume ничего не пересобирают."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.stem + ".", suffix=path.suffix, delete=False) as stream:
            temporary = Path(stream.name)
        flags = (["clang++", "-O3", "-std=c++11", "-dynamiclib", "-pthread"] if sys.platform == "darwin" else
                 ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"])
        subprocess.run(flags + ["-I", str(ROOT / "vendor"), str(ROOT / "vendor/td_train.cpp"), "-o", str(temporary)], check=True)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


class _Episode(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("seed", "board", "moves", "caps", "updates",
                                                   "attempted_updates", "trajectory_digest", "first_one", "first_two")]
    _fields_ += [(name, ctypes.c_uint32) for name in ("has_first_one", "has_first_two", "result")]


def _default_table():
    data = json.loads((ROOT / "spawn-model.default.json").read_text())
    keys = sorted(int(key) for key in data["stages"])
    return [(list(data["stages"][str(max([k for k in keys if k <= stage], default=keys[0]))]) + [0] * 15)[:15]
            for stage in range(16)]


class NativeTrainer:
    """Отдельный native handle: параметры передаются без потери float32-битов."""

    def __init__(self, library_path):
        self.library_path = Path(library_path).resolve()
        self._handle = None
        self._lib = ctypes.CDLL(str(self.library_path))
        lib = self._lib
        lib.td_train_api_version.restype = ctypes.c_int
        lib.td_train_result_size.restype = ctypes.c_size_t
        lib.td_train_parameter_count.restype = ctypes.c_size_t
        if (lib.td_train_api_version() != 1 or lib.td_train_result_size() != ctypes.sizeof(_Episode) or
                lib.td_train_parameter_count() != PARAMETER_COUNT):
            raise RuntimeError("Несовместимый API TD-тренера; выполните явный build_trainer")
        lib.td_train_create.restype = ctypes.c_void_p
        lib.td_train_destroy.argtypes = [ctypes.c_void_p]
        lib.td_train_destroy.restype = None
        lib.td_train_error.argtypes = [ctypes.c_void_p]
        lib.td_train_error.restype = ctypes.c_char_p
        lib.td_train_reset.argtypes = [ctypes.c_void_p, ctypes.c_float]
        lib.td_train_reset.restype = ctypes.c_int
        lib.td_train_get_parameters.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t]
        lib.td_train_get_parameters.restype = ctypes.c_int
        lib.td_train_set_parameters.argtypes = lib.td_train_get_parameters.argtypes
        lib.td_train_set_parameters.restype = ctypes.c_int
        lib.td_train_set_spawn_table.argtypes = lib.td_train_get_parameters.argtypes
        lib.td_train_set_spawn_table.restype = ctypes.c_int
        lib.td_train_episode.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint64, ctypes.POINTER(_Episode), ctypes.c_size_t]
        lib.td_train_episode.restype = ctypes.c_int
        self._handle = lib.td_train_create()
        if not self._handle:
            raise RuntimeError("Не удалось создать TD trainer")
        self.reset()
        self.set_spawn_table(_default_table())

    def _ready(self):
        if self._handle is None:
            raise RuntimeError("TD trainer уже закрыт")

    def _check(self, status):
        if status != 0:
            message = self._lib.td_train_error(self._handle)
            raise ValueError(message.decode("utf-8", errors="replace") if message else "Ошибка TD trainer")

    def close(self):
        if self._handle is not None:
            self._lib.td_train_destroy(self._handle)
            self._handle = None

    def __del__(self):
        self.close()

    def __enter__(self):
        self._ready()
        return self

    def __exit__(self, *_):
        self.close()

    def reset(self, initial_value=0.5):
        self._ready()
        initial_value = _probability(initial_value, "initial_value")
        self._check(self._lib.td_train_reset(self._handle, float(initial_value)))
        model = TDModel(float(initial_value))
        self._metadata, self._training = model.metadata, model.training

    def set_model(self, model):
        self._ready()
        if not isinstance(model, TDModel):
            raise ValueError("Нужен TDModel target2, а не MC/value_model")
        parameters = _parameters(model)
        self._check(self._lib.td_train_set_parameters(self._handle, parameters.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(parameters)))
        self._metadata, self._training = copy.deepcopy(model.metadata), copy.deepcopy(model.training)

    def model(self):
        self._ready()
        parameters = np.empty(PARAMETER_COUNT, dtype=np.float32)
        self._check(self._lib.td_train_get_parameters(self._handle, parameters.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(parameters)))
        model = TDModel()
        model.bias[:] = parameters[:STAGES]
        model.weights[:] = parameters[STAGES:].reshape(model.weights.shape)
        model.metadata, model.training = copy.deepcopy(self._metadata), copy.deepcopy(self._training)
        return model

    def set_spawn_table(self, table16x15):
        self._ready()
        table = np.asarray(table16x15, dtype=np.float64)
        if (table.shape != (16, 15) or not np.isfinite(table).all() or (table < 0).any() or
                not np.isfinite(table.sum(axis=1)).all() or (table.sum(axis=1) <= 0).any()):
            raise ValueError("Нужны 16 конечных ненулевых строк по15 неотрицательных весов")
        # Нормировать до float32-конверсии, чтобы большие конечные веса не стали Inf.
        normalized = (table / table.sum(axis=1)[:, None]).astype(np.float32).reshape(-1)
        self._check(self._lib.td_train_set_spawn_table(self._handle, normalized.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(normalized)))

    def episode(self, seed, algorithm="td0", behavior="teacher", teacher_depth=1,
                alpha=0.1, epsilon=0.05, max_moves=6000, start_board=None):
        self._ready()
        if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)) or not 0 <= seed < 2 ** 64:
            raise ValueError("seed должен быть uint64")
        algorithms, behaviors = {"td0": 0, "mc": 1, "observe": 2}, {"teacher": 0, "selfplay": 1}
        if algorithm not in algorithms or behavior not in behaviors:
            raise ValueError("Неизвестный algorithm/behavior")
        if type(teacher_depth) is not int or teacher_depth != 1:
            raise ValueError("Teacher этого протокола имеет фиксированную depth1")
        alpha, epsilon = _probability(alpha, "alpha"), _probability(epsilon, "epsilon")
        if type(max_moves) is not int or not 0 <= max_moves <= 2 ** 31 - 1:
            raise ValueError("max_moves должен быть неотрицательным int32")
        packed = 0
        if start_board is not None:
            board = _board(start_board)
            if _terminal(board) is not None:
                raise ValueError("start_board должен быть валидным незавершённым полем")
            packed = sum(value << (4 * cell) for cell, value in enumerate(board))
        result = _Episode()
        self._check(self._lib.td_train_episode(self._handle, int(seed), algorithms[algorithm], behaviors[behavior],
                                              teacher_depth, float(alpha), float(epsilon), max_moves,
                                              int(start_board is not None), packed, ctypes.byref(result), ctypes.sizeof(result)))
        unpack = lambda value: [int((value >> (4 * cell)) & 15) for cell in range(16)]
        first_caps = {}
        if result.has_first_one:
            first_caps["1"] = unpack(result.first_one)
        if result.has_first_two:
            first_caps["2"] = unpack(result.first_two)
        return dict(seed=int(result.seed), result=("loss", "goal", "limit")[result.result],
                    moves=int(result.moves), caps=int(result.caps), board=unpack(result.board),
                    first_caps=first_caps, updates=int(result.updates),
                    attempted_updates=int(result.attempted_updates),
                    trajectory_digest=format(result.trajectory_digest, "016x"))
