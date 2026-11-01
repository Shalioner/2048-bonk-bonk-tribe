"""Компактная MC-afterstate value для цели «три несливающиеся 11».

Это экспериментальный оценщик, а не калиброванная вероятность и не новая
настройка бота. fit получает исход целой партии и не запускает игру.
"""

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import zlib

import numpy as np


CAP = 11
GOAL_COUNT = 3
VERSION = 1
MAGIC = b"BONKVAL1"
STAGES = 3
TABLE_SIZE = 16 ** 4
PATTERNS = ((0, 1, 2, 3), (0, 1, 4, 5), (0, 1, 2, 5), (0, 1, 5, 6))
PATTERN_COUNT = len(PATTERNS)
SYMMETRY_COUNT = 8
WEIGHT_COUNT = STAGES * PATTERN_COUNT * TABLE_SIZE
TARGET_TYPE = 1  # MC outcome после своего хода и до появления новой плитки.
HEADER = struct.Struct("<8s10I")
MAX_METADATA_BYTES = 1024 * 1024
PAYLOAD_BYTES = 4 * (STAGES + WEIGHT_COUNT)
RESERVED_METADATA = frozenset(("format", "version", "architecture", "parameters_sha256", "training"))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


ARCHITECTURE = dict(alphabet=16, cap=CAP, goal_count=GOAL_COUNT, stages=STAGES,
                    patterns=[list(p) for p in PATTERNS], symmetries="D4",
                    symmetry_count=SYMMETRY_COUNT, normalization=8,
                    target="mc_afterstate_three_unmergeable_rank11", target_type=TARGET_TYPE,
                    goal_value=1, loss_value=0, link="sigmoid")


def _board(board):
    b = tuple(board)
    if len(b) != 16 or any(isinstance(v, (bool, np.bool_)) or
                          not isinstance(v, (int, np.integer)) or not 0 <= v <= 15 for v in b):
        raise ValueError("Нужно поле из 16 целых уровней 0..15")
    return tuple(int(v) for v in b)


def _maps():
    maps = []
    for mirrored in (False, True):
        for turns in range(4):
            indices = []
            for cell in range(16):
                r, c = divmod(cell, 4)
                for _ in range(turns):
                    r, c = 3 - c, r
                if mirrored:
                    c = 3 - c
                indices.append(4 * r + c)
            maps.append(indices)
    return np.asarray(maps, dtype=np.intp)


SYMMETRY_MAPS = _maps()
FEATURE_CELLS = SYMMETRY_MAPS[:, np.asarray(PATTERNS)].transpose(1, 0, 2)
POWERS = np.asarray((1, 16, 256, 4096), dtype=np.uint32)


def features(board):
    """Вернуть стадию 0/1/2 и индексы таблиц формы (4, 8)."""
    b = _board(board)
    indices = (np.asarray(b, dtype=np.uint32)[FEATURE_CELLS] * POWERS).sum(axis=2)
    return min(b.count(CAP), 2), indices.astype(np.uint16)


def _terminal(board):
    if board.count(CAP) >= GOAL_COUNT:
        return 1
    occupied = sum(v != 0 for v in board)
    if 0 < occupied < 16:
        return None
    if occupied == 0:
        return 0
    for r in range(4):
        for c in range(4):
            v = board[4 * r + c]
            if v < CAP and ((c < 3 and v == board[4 * r + c + 1]) or
                            (r < 3 and v == board[4 * (r + 1) + c])):
                return None
    return 0


def _sigmoid(z):
    z = np.asarray(z, dtype=np.float64)
    result = np.empty_like(z)
    positive = z >= 0
    result[positive] = 1 / (1 + np.exp(-z[positive]))
    exp_z = np.exp(z[~positive])
    result[~positive] = exp_z / (1 + exp_z)
    return result


def _dataset(records):
    clean, stage_list, index_list, terminals = [], [], [], []
    episode_sums, episode_labels = {}, {}
    for record in records:
        b = _board(record["board"])
        seed = record["seed"]
        if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)):
            raise ValueError("seed должен быть целым идентификатором партии")
        seed = int(seed)
        label = float(record["label"])
        weight = float(record.get("episode_weight", 1))
        if label not in (0.0, 1.0) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("Нужны бинарный label и положительный конечный episode_weight")
        if seed in episode_labels and episode_labels[seed] != label:
            raise ValueError("MC label должен совпадать у всех состояний одной партии")
        terminal = _terminal(b)
        if terminal is not None and label != terminal:
            raise ValueError("Исход партии противоречит терминальному полю")
        episode_labels[seed] = label
        episode_sums[seed] = episode_sums.get(seed, 0.0) + weight
        if not math.isfinite(episode_sums[seed]):
            raise ValueError("Сумма весов партии переполнена")
        stage, indices = features(b)
        ids = indices.astype(np.int64) + np.arange(PATTERN_COUNT)[:, None] * TABLE_SIZE
        ids += stage * PATTERN_COUNT * TABLE_SIZE
        stage_list.append(stage)
        index_list.append(ids.reshape(-1))
        terminals.append(-1 if terminal is None else terminal)
        clean.append(dict(board=list(b), label=label, seed=seed, episode_weight=weight))
    if not clean:
        raise ValueError("Пустой набор данных")
    # Ровно единичная масса на seed, независимо от длины партии и от того,
    # прислал ли вызывающий 1 или уже 1/n как episode_weight.
    weights = np.asarray([r["episode_weight"] / episode_sums[r["seed"]] for r in clean])
    if not np.isfinite(weights).all() or not (weights > 0).all():
        raise ValueError("Веса партии имеют неподдерживаемый динамический диапазон")
    return dict(records=clean, stages=np.asarray(stage_list, dtype=np.intp),
                indices=np.asarray(index_list, dtype=np.intp),
                terminals=np.asarray(terminals, dtype=np.int8),
                labels=np.asarray([r["label"] for r in clean]), weights=weights,
                seeds=sorted(episode_sums), digest=hashlib.sha256(_canonical(clean)).hexdigest())


class ValueModel:
    """Логистическая n-tuple модель; нулевые параметры дают нейтральные 0.5."""

    def __init__(self):
        self.weights = np.zeros((STAGES, PATTERN_COUNT, TABLE_SIZE), dtype=np.float32)
        self.bias = np.zeros(STAGES, dtype=np.float32)
        self.training = dict(fitted=False)
        self.metadata = {}  # Дополнительное происхождение: source/model/data hashes.

    def predict(self, board):
        b = _board(board)
        terminal = _terminal(b)
        if terminal is not None:
            return float(terminal)
        stage, indices = features(b)
        total = self.weights[stage, np.arange(PATTERN_COUNT)[:, None], indices].sum(dtype=np.float64)
        z = float(self.bias[stage]) + total / SYMMETRY_COUNT
        return float(np.float32(_sigmoid(z)))

    def _predict_dataset(self, data):
        z = self.bias[data["stages"]].astype(np.float64)
        z += self.weights.reshape(-1)[data["indices"]].sum(axis=1, dtype=np.float64) / SYMMETRY_COUNT
        prediction = _sigmoid(z)
        terminal = data["terminals"] >= 0
        prediction[terminal] = data["terminals"][terminal]
        return prediction

    @staticmethod
    def _brier(prediction, data):
        return float(np.average((prediction - data["labels"]) ** 2, weights=data["weights"]))

    def fit(self, records, *, validation_records=None, epochs=8, learning_rate=0.5,
            l2=1e-4, patience=3, min_delta=1e-6, batch_size=256, seed=0):
        """Обучить с нуля; выбрать epoch по Brier целых validation-партий.

        episode_weight задаёт относительные веса внутри партии. Они всегда
        нормируются к сумме1 на seed, так что уже готовые 1/n не удваиваются.
        Без validation обучение разрешено, но admission gate всегда False.
        """
        for value, name in ((epochs, "epochs"), (patience, "patience"), (batch_size, "batch_size")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(name + " должен быть положительным целым")
        if (not math.isfinite(learning_rate) or not 0 < learning_rate <= 10 or not math.isfinite(l2) or
                l2 < 0 or learning_rate * l2 >= 1 or not math.isfinite(min_delta) or min_delta < 0):
            raise ValueError("Некорректные параметры обучения")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed обучения должен быть неотрицательным целым")
        rng = np.random.default_rng(seed)
        train = _dataset(records)
        validation = _dataset(validation_records) if validation_records is not None else None
        if validation is not None and set(train["seeds"]) & set(validation["seeds"]):
            raise ValueError("Train и validation должны содержать разные seed целых партий")
        active = np.flatnonzero(train["terminals"] < 0)
        if not len(active):
            raise ValueError("Нет незавершённых afterstate для обучения")
        self.weights.fill(0)
        priors = np.empty(STAGES, dtype=np.float64)
        for stage in range(STAGES):
            mask = (train["stages"] == stage) & (train["terminals"] < 0)
            priors[stage] = (np.average(train["labels"][mask], weights=train["weights"][mask])
                             if mask.any() else 0.5)
        bounded = np.clip(priors, 1e-4, 1 - 1e-4)
        self.bias[:] = np.log(bounded / (1 - bounded)).astype(np.float32)
        evaluation = validation if validation is not None else train

        def prior_prediction(data):
            prediction = bounded[data["stages"]].copy()
            terminal = data["terminals"] >= 0
            prediction[terminal] = data["terminals"][terminal]
            return prediction

        prior_brier = self._brier(prior_prediction(evaluation), evaluation)
        history = [dict(epoch=0, train_brier=self._brier(self._predict_dataset(train), train),
                        validation_brier=self._brier(self._predict_dataset(evaluation), evaluation))]
        best_brier, best_epoch = history[0]["validation_brier"], 0
        best_weights, best_bias = self.weights.copy(), self.bias.copy()
        stale = 0
        active_weights = train["weights"][active]
        # Uniform minibatches; correction preserves the objective of equal
        # total weight for each episode despite unequal numbers of records.
        relative = np.zeros(len(train["records"]))
        relative[active] = active_weights / active_weights.sum() * len(active)
        for epoch in range(1, epochs + 1):
            order = rng.permutation(active)
            for offset in range(0, len(order), batch_size):
                ids = order[offset:offset + batch_size]
                indices = train["indices"][ids]
                stages = train["stages"][ids]
                z = self.bias[stages].astype(np.float64)
                z += self.weights.reshape(-1)[indices].sum(axis=1, dtype=np.float64) / SYMMETRY_COUNT
                gradient = learning_rate * (train["labels"][ids] - _sigmoid(z)) * relative[ids] / len(ids)
                if l2:
                    self.weights *= np.float32(1 - learning_rate * l2)
                np.add.at(self.bias, stages, gradient)
                np.add.at(self.weights.reshape(-1), indices.reshape(-1),
                          np.repeat(gradient / SYMMETRY_COUNT, PATTERN_COUNT * SYMMETRY_COUNT))
                np.clip(self.weights, -32, 32, out=self.weights)
                np.clip(self.bias, -32, 32, out=self.bias)
            row = dict(epoch=epoch, train_brier=self._brier(self._predict_dataset(train), train),
                       validation_brier=self._brier(self._predict_dataset(evaluation), evaluation))
            history.append(row)
            if row["validation_brier"] < best_brier - min_delta:
                best_brier, best_epoch = row["validation_brier"], epoch
                best_weights, best_bias = self.weights.copy(), self.bias.copy()
                stale = 0
            else:
                stale += 1
                if stale >= patience:
                    break
        self.weights, self.bias = best_weights, best_bias
        configuration = dict(epochs=epochs, learning_rate=learning_rate, l2=l2, patience=patience,
                             min_delta=min_delta, batch_size=batch_size, seed=seed)
        configuration["parameter_clip"] = 32
        configuration["prior_clip"] = 1e-4
        self.training = dict(fitted=True, method="supervised_mc_binary_logistic",
                             configuration=configuration,
                             configuration_sha256=hashlib.sha256(_canonical(configuration)).hexdigest(),
                             train_seeds=train["seeds"], validation_seeds=validation["seeds"] if validation else [],
                             train_record_sha256=train["digest"],
                             validation_record_sha256=validation["digest"] if validation else None,
                             train_records=len(train["records"]),
                             validation_records=len(validation["records"]) if validation else 0,
                             episode_weighting="normalize within seed to sum1",
                             stage_priors=priors.tolist(), history=history, best_epoch=best_epoch,
                             train_brier=self._brier(self._predict_dataset(train), train),
                             validation_brier=best_brier if validation else None,
                             stage_prior_validation_brier=prior_brier if validation else None,
                             validation_gate_passed=bool(validation is not None and
                                                         best_brier < prior_brier - min_delta),
                             calibrated_probability=False)
        return copy.deepcopy(self.training)

    def save(self, path, *, metadata=None):
        if self.weights.shape != (STAGES, PATTERN_COUNT, TABLE_SIZE) or self.bias.shape != (STAGES,):
            raise ValueError("Некорректная форма параметров")
        if self.weights.dtype != np.float32 or self.bias.dtype != np.float32:
            raise ValueError("Параметры должны иметь dtype float32")
        if not np.isfinite(self.weights).all() or not np.isfinite(self.bias).all():
            raise ValueError("Параметры содержат NaN/Inf")
        payload = self.bias.astype("<f4").tobytes() + self.weights.astype("<f4").tobytes()
        extra = copy.deepcopy(self.metadata)
        if metadata is not None:
            extra.update(metadata)
        if RESERVED_METADATA.intersection(extra):
            raise ValueError("Дополнительные metadata не должны подменять архитектуру или training")
        manifest = dict(extra, format="bonk-afterstate-value", version=VERSION,
                        architecture=copy.deepcopy(ARCHITECTURE),
                        parameters_sha256=hashlib.sha256(payload).hexdigest(), training=self.training)
        encoded = _canonical(manifest)
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError("Слишком большие metadata")
        checksum = zlib.crc32(encoded + payload)
        header = HEADER.pack(MAGIC, VERSION, HEADER.size, len(encoded), STAGES, PATTERN_COUNT,
                             TABLE_SIZE, CAP, GOAL_COUNT, TARGET_TYPE, checksum)
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(header + encoded + payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return copy.deepcopy(manifest)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > HEADER.size + MAX_METADATA_BYTES + PAYLOAD_BYTES:
            raise ValueError("Слишком большой файл модели")
        raw = path.read_bytes()
        if len(raw) < HEADER.size:
            raise ValueError("Обрезанный header модели")
        magic, version, header_size, meta_size, stages, patterns, table, cap, goal, target, checksum = HEADER.unpack_from(raw)
        if (magic, version, header_size, stages, patterns, table, cap, goal, target) != (
                MAGIC, VERSION, HEADER.size, STAGES, PATTERN_COUNT, TABLE_SIZE, CAP, GOAL_COUNT, TARGET_TYPE):
            raise ValueError("Несовместимые формат, архитектура, правила или цель модели")
        if not 1 <= meta_size <= MAX_METADATA_BYTES or len(raw) != HEADER.size + meta_size + PAYLOAD_BYTES:
            raise ValueError("Некорректный размер модели")
        if zlib.crc32(raw[HEADER.size:]) != checksum:
            raise ValueError("Неверная CRC32 модели")
        encoded = raw[HEADER.size:HEADER.size + meta_size]
        payload = raw[HEADER.size + meta_size:]
        try:
            metadata = json.loads(encoded)
            _canonical(metadata)  # Не принимать NaN/Inf и внутри JSON.
            if (metadata["format"] != "bonk-afterstate-value" or metadata["version"] != VERSION or
                    _canonical(metadata["architecture"]) != _canonical(ARCHITECTURE) or
                    metadata["parameters_sha256"] != hashlib.sha256(payload).hexdigest() or
                    not isinstance(metadata["training"], dict)):
                raise ValueError("Некорректные metadata модели")
        except (UnicodeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Некорректные metadata модели") from exc
        parameters = np.frombuffer(payload, dtype="<f4")
        if not np.isfinite(parameters).all():
            raise ValueError("Параметры содержат NaN/Inf")
        model = cls()
        model.bias[:] = parameters[:STAGES]
        model.weights[:] = parameters[STAGES:].reshape(model.weights.shape)
        model.training = copy.deepcopy(metadata["training"])
        model.metadata = {key: copy.deepcopy(value) for key, value in metadata.items() if key not in RESERVED_METADATA}
        return model
