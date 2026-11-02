"""Отдельный TD/MC-протокол: данные, обновления, заморозка и парный контроль."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
sys.path.insert(0, str(WORK))
ARMS = ("heuristic", "old_mc", "mc_teacher", "td_teacher", "td_selfplay")
GROUPS = {"matched": ("mc_teacher", "td_teacher"), "selfplay": ("td_selfplay",)}
LIBRARY = "td_trainer.dylib" if sys.platform == "darwin" else "td_trainer.so"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def plan():
    return read(ROOT / "plan.json")


def backend():
    return importlib.import_module("td_model")


def terminal(board):
    module = importlib.import_module("value_model")
    return module._terminal(module._board(board))


def valid_start(board, cap=None):
    try:
        return terminal(board) is None and (cap is None or list(board).count(11) == cap)
    except (ValueError, TypeError):
        return False


def spawn_table():
    model = read(WORK / "spawn-model.default.json")
    stages = sorted(int(k) for k in model["stages"])
    return [(list(model["stages"][str(max((k for k in stages if k <= s), default=stages[0]))]) + [0] * 15)[:15]
            for s in range(16)]


def trainer_path():
    return ROOT / "build" / LIBRARY


def producer_paths():
    paths = [Path(__file__), ROOT / "plan.json", ROOT / "seed-inventory.json", WORK / "TD-PORT-API.md",
             WORK / "td_model.py", WORK / "value_model.py", WORK / "bot.py", WORK / "benchmark.py",
             WORK / "spawn-model.default.json", WORK / "vendor/engine_adapted.cpp", WORK / "vendor/td_train.cpp",
             WORK / "vendor/td_model.h", WORK / "experiments/learning/cycle.py"]
    paths += list((WORK / "vendor").glob("*.h"))
    paths += [p for p in (WORK / "vendor/tdl2048-original").rglob("*") if p.is_file()]
    return sorted(set(paths))


def source_fingerprint(native=True):
    result = dict(contract="bonk-td-cycle-v1", files={str(p.resolve()): sha(p) for p in producer_paths()})
    if native:
        manifest = read(ROOT / "build/trainer-manifest.json")
        if manifest.get("sources") != source_fingerprint(False) or manifest.get("sha256") != sha(trainer_path()):
            raise ValueError("Trainer не соответствует manifest; implicit rebuild запрещён")
        result["trainer"] = dict(path=str(trainer_path().resolve()), sha256=sha(trainer_path()))
    return result


def training_open():
    if (ROOT / "evaluation-started.json").exists():
        raise ValueError("После начала evaluation новые pool/train/build запрещены для любого --out")


def checked_output(path):
    path = Path(path)
    if path.resolve() == (ROOT / "evaluation-started.json").resolve():
        raise ValueError("Путь --out зарезервирован для глобального evaluation marker")
    return path


def evaluation_marker(stage, out):
    path = ROOT / "evaluation-started.json"
    sources = source_fingerprint()
    if path.exists():
        marker = read(path)
        if marker.get("sources") != sources:
            raise ValueError("Производители изменены после начала evaluation")
        return marker
    marker = dict(version=1, sources=sources, first_stage=stage, first_output=str(Path(out).resolve()))
    atomic(path, marker)
    return marker


@contextmanager
def lease():
    """CLI не допускает одновременный timed-контроль и producer-процесс."""
    import fcntl
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / ".cycle.lock").open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Другой этап TD-цикла уже выполняется") from exc
        yield


def build():
    sources = source_fingerprint(False)
    path = ROOT / "build/trainer-manifest.json"
    if path.exists():
        saved = read(path)
        if saved.get("sources") != sources or saved.get("sha256") != sha(trainer_path()):
            raise ValueError("Существующая trainer-сборка несовместима, автоматическая перезапись запрещена")
        return saved
    training_open()
    intent = ROOT / "build/build-intent.json"
    if trainer_path().exists() and (not intent.exists() or read(intent) != sources):
        raise ValueError("Неизвестный бинарник без build-manifest")
    atomic(intent, sources)
    backend().build_trainer(trainer_path())
    training_open()
    saved = dict(version=1, sources=sources, sha256=sha(trainer_path()))
    atomic(path, saved)
    return saved


def new_trainer(model=None):
    trainer = backend().NativeTrainer(trainer_path())
    trainer.set_spawn_table(spawn_table())
    if model is None:
        trainer.reset(initial_value=plan()["model"]["initial_value"])
    else:
        trainer.set_model(model)
    return trainer


def parameter_hash(model):
    result = hashlib.sha256()
    result.update(model.bias.tobytes())
    result.update(model.weights.tobytes())
    return result.hexdigest()


def checked_episode(trainer, seed, *, algorithm, behavior, start_board=None, epsilon=0):
    started = time.monotonic()
    row = trainer.episode(seed, algorithm=algorithm, behavior=behavior,
                          teacher_depth=plan()["training"]["teacher_depth"], alpha=plan()["model"]["alpha"],
                          epsilon=epsilon, max_moves=plan()["max_moves"], start_board=start_board)
    row = dict(row, seconds=time.monotonic() - started, start_board=start_board)
    check_episode(row, seed)
    if algorithm == "observe" and row["updates"] != 0:
        raise ValueError("Observe выполнил обновление модели")
    return row


def check_episode(row, seed):
    try:
        board = row["board"]
        t = terminal(board)
        if (row["seed"] != seed or row["result"] not in ("goal", "loss", "limit") or
                type(row["moves"]) is not int or not 0 <= row["moves"] <= plan()["max_moves"] or
                row["caps"] != board.count(11) or (row["result"] == "goal") != (t == 1) or
                (row["result"] == "loss") != (t == 0) or type(row["updates"]) is not int or row["updates"] < 0 or
                not math.isfinite(row["seconds"]) or row["seconds"] < 0 or
                not isinstance(row["trajectory_digest"], str) or not row["trajectory_digest"]):
            raise ValueError("Некорректный исход native episode")
        for cap, snapshot in row["first_caps"].items():
            if cap not in ("1", "2") or not valid_start(snapshot, int(cap)):
                raise ValueError("first_caps должен содержать точные незавершённые board-массивы")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Некорректная native episode запись") from exc


def progress(kind, **fields):
    print(json.dumps(dict(stage=kind, **fields)), flush=True)


def pool(resume=False, out=None, *, validate_only=False):
    out = checked_output(out or ROOT / "pool.json")
    config = dict(plan()["pool"], max_moves=plan()["max_moves"])
    header = dict(version=1, kind="pool", config=config, sources=source_fingerprint(), spawn_table=spawn_table())
    if out.exists():
        if not resume:
            raise ValueError("Pool существует; нужна явная --resume")
        data = read(out)
        if any(data.get(k) != v for k, v in header.items()):
            raise ValueError("Pool-checkpoint несовместим")
        for index, row in enumerate(data["games"]):
            check_episode(row, config["seed_start"] + index)
        if len(data["games"]) > config["games"] or data.get("complete") != (len(data["games"]) == config["games"]):
            raise ValueError("Неверный префикс pool")
        expected = extract_pool(data["games"])
        if data.get("starts") != expected:
            raise ValueError("Pool snapshots изменены")
        if any(g["result"] == "limit" for g in data["games"]):
            raise ValueError("Pool содержит limit")
        if data["complete"]:
            return data
    else:
        if resume:
            raise ValueError("Pool для --resume не существует")
        data = dict(header, games=[], starts={"1": [], "2": []}, complete=False)
    if validate_only:
        raise ValueError("Pool не завершён; неявное продолжение запрещено")
    training_open()
    trainer = new_trainer()
    original = parameter_hash(trainer.model())
    atomic(out, data)
    for index in range(len(data["games"]), config["games"]):
        training_open()
        row = checked_episode(trainer, config["seed_start"] + index, algorithm="observe", behavior="teacher", epsilon=config["epsilon"])
        if parameter_hash(trainer.model()) != original:
            raise ValueError("Observe изменил веса при построении pool")
        data["games"].append(row)
        data["starts"] = extract_pool(data["games"])
        data["complete"] = len(data["games"]) == config["games"]
        atomic(out, data)
        progress("pool", episodes=len(data["games"]), result=row["result"])
        if row["result"] == "limit":
            raise ValueError("Pool limit сохранён; этап остановлен без label")
    return data


def extract_pool(games):
    starts = {"1": [], "2": []}
    seen = {"1": set(), "2": set()}
    capacity = plan()["pool"]["capacity_per_stage"]
    for row in games:
        for cap in ("1", "2"):
            board = row["first_caps"].get(cap)
            if board is not None and valid_start(board, int(cap)) and tuple(board) not in seen[cap] and len(starts[cap]) < capacity:
                starts[cap].append(dict(board=board, source_seed=row["seed"]))
                seen[cap].add(tuple(board))
    return starts


def load_pool():
    data = pool(resume=True, validate_only=True)
    if not data["complete"] or not all(data["starts"].get(stage) for stage in ("1", "2")):
        raise ValueError("Нужен законченный pool с незавершёнными позициями обеих стадий")
    return data


def start_from_pool(pool_data, seed, stage):
    if stage == "full":
        return None
    starts = pool_data["starts"][str(stage)]
    return list(starts[(seed ^ (seed >> 16)) % len(starts)]["board"])


def curriculum(pool_data, index, seed):
    if index < plan()["training"]["warmup_episodes"]:
        return None
    phase = index % 4
    return start_from_pool(pool_data, seed, str(phase + 1)) if phase < 2 else None


def validation_schedule(pool_data):
    p = plan()["validation"]
    return [(stage, seed, start_from_pool(pool_data, seed, stage))
            for stage, key in (("full", "full_seed_start"), ("1", "cap1_seed_start"), ("2", "cap2_seed_start"))
            for seed in range(p[key], p[key] + p["games_per_stage"])]


def validate_model(model_path, pool_data, out):
    schedule = validation_schedule(pool_data)
    header = dict(version=1, kind="validation", sources=source_fingerprint(), model_sha256=sha(model_path),
                  pool_sha256=sha(ROOT / "pool.json"), config=plan()["validation"])
    if out.exists():
        data = read(out)
        if any(data.get(k) != v for k, v in header.items()):
            raise ValueError("Validation-checkpoint несовместим")
    else:
        data = dict(header, games=[], complete=False)
    if len(data["games"]) > len(schedule):
        raise ValueError("Некорректный validation prefix")
    for row, (stage, seed, start) in zip(data["games"], schedule):
        check_episode(row, seed)
        if row.get("validation_stage") != stage or row.get("start_board") != start or row["updates"] != 0:
            raise ValueError("Изменён validation prefix")
        if row["result"] == "limit":
            raise ValueError("Validation содержит limit")
    if data.get("complete") != (len(data["games"]) == len(schedule)):
        raise ValueError("Некорректный validation complete")
    if not data["complete"]:
        training_open()
        trainer = new_trainer(backend().TDModel.load(model_path))
        weights = parameter_hash(trainer.model())
        atomic(out, data)
        for stage, seed, start in schedule[len(data["games"]):]:
            training_open()
            row = checked_episode(trainer, seed, algorithm="observe", behavior="selfplay", start_board=start, epsilon=0)
            row["validation_stage"] = stage
            if parameter_hash(trainer.model()) != weights:
                raise ValueError("Validation observe изменил веса")
            data["games"].append(row)
            data["complete"] = len(data["games"]) == len(schedule)
            atomic(out, data)
            if row["result"] == "limit":
                raise ValueError("Validation limit сохранён")
    metric = [sum(g["result"] == "goal" and g["validation_stage"] == stage for g in data["games"])
              for stage in ("full", "1", "2")]
    if "metric" in data:
        if data["metric"] != metric:
            raise ValueError("Изменена validation metric")
    else:
        training_open()
        data["metric"] = metric
        atomic(out, data)
    return metric


def state_header(group):
    return dict(version=1, kind="training", group=group, config=dict(training=plan()["training"], model=plan()["model"],
                validation=plan()["validation"], max_moves=plan()["max_moves"]), sources=source_fingerprint(), pool_sha256=sha(ROOT / "pool.json"))


def group_directory(out):
    return Path(str(out) + ".artifacts")


def save_current(state, trainers, directory, count):
    training_open()
    slot = 1 - state.get("slot", 1)
    models = {}
    for arm, trainer in trainers.items():
        path = directory / "current" / (str(slot) + "-" + arm + ".bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and backend().TDModel.load(path).metadata.get("td_cycle_run") != state["run_id"]:
            raise ValueError("Чужая current-модель, перезапись запрещена")
        model = trainer.model()
        model.save(path, metadata=dict(td_cycle_run=state["run_id"], episodes=count, arm=arm))
        models[arm] = dict(path=str(path.resolve()), sha256=sha(path), parameters_sha256=parameter_hash(model))
    state.update(slot=slot, current=models, checkpoint_episodes=count)


def current_models(state):
    result = {}
    for arm, entry in state["current"].items():
        if sha(entry["path"]) != entry["sha256"]:
            raise ValueError("Текущая модель не соответствует checkpoint")
        model = backend().TDModel.load(entry["path"])
        if (parameter_hash(model) != entry["parameters_sha256"] or
                model.metadata.get("td_cycle_run") != state["run_id"] or
                model.metadata.get("episodes") != state["checkpoint_episodes"] or model.metadata.get("arm") != arm):
            raise ValueError("Изменены параметры current-модели")
        result[arm] = model
    return result


def blocks(directory, run_id):
    records = []
    for block_index, path in enumerate(sorted((directory / "blocks").glob("*.json"))):
        if path.name != "%08d.json" % block_index:
            raise ValueError("Разрыв в training blocks")
        data = read(path)
        if data.get("run_id") != run_id or data.get("block") != block_index or not isinstance(data.get("records"), list):
            raise ValueError("Чужой training block")
        if not 1 <= len(data["records"]) <= plan()["training"]["checkpoint_every"]:
            raise ValueError("Неверный размер блока")
        if records and len(records) % plan()["training"]["checkpoint_every"]:
            raise ValueError("Незавершённый блок перед следующим")
        records.extend(data["records"])
    return records


def append_block(directory, run_id, index, record):
    size = plan()["training"]["checkpoint_every"]
    number, offset = divmod(index, size)
    path = directory / "blocks" / ("%08d.json" % number)
    data = read(path) if path.exists() else dict(run_id=run_id, block=number, records=[])
    if data.get("run_id") != run_id or len(data["records"]) != offset:
        raise ValueError("Training block не является ожидаемым префиксом")
    data["records"].append(record)
    atomic(path, data)


def stable_episode(row):
    return {k: v for k, v in row.items() if k != "seconds"}


def record_chain(records, previous="0" * 64):
    """Линейная проверка журнала; checkpoint не сериализует всю историю заново."""
    for row in records:
        previous = digest([previous, row])
    return previous


def training_episode(trainers, pool_data, index, group):
    p = plan()
    seed = p["training"]["seed_start"] + index
    start = curriculum(pool_data, index, seed)
    before = {arm: trainer.model() for arm, trainer in trainers.items()}
    rows = {}
    for arm, trainer in trainers.items():
        behavior = "teacher" if group == "matched" or index < p["training"]["warmup_episodes"] else "selfplay"
        row = checked_episode(trainer, seed, algorithm="mc" if arm == "mc_teacher" else "td0", behavior=behavior,
                              start_board=start, epsilon=p["training"]["teacher_epsilon"] if behavior == "teacher" else p["model"]["epsilon"])
        rows[arm] = row
        if row["result"] == "limit":
            for name in trainers:
                trainers[name].set_model(before[name])
            return dict(index=index, seed=seed, start_board=start, rows=rows, invalid="limit")
    if group == "matched":
        left, right = rows["mc_teacher"], rows["td_teacher"]
        if any(left[key] != right[key] for key in ("trajectory_digest", "board", "moves", "result", "first_caps")):
            for arm in trainers:
                trainers[arm].set_model(before[arm])
            return dict(index=index, seed=seed, start_board=start, rows=rows, invalid="trajectory mismatch")
    return dict(index=index, seed=seed, start_board=start, rows=rows)


def milestone(state, trainers, pool_data, directory, count):
    models = {}
    metrics = {}
    for arm, trainer in trainers.items():
        path = directory / "milestones" / str(count) / (arm + ".bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        model = trainer.model()
        if path.exists():
            saved = backend().TDModel.load(path)
            if parameter_hash(saved) != parameter_hash(model) or saved.metadata.get("td_cycle_run") != state["run_id"]:
                raise ValueError("Чужая milestone-модель, перезапись запрещена")
        else:
            model.save(path, metadata=dict(td_cycle_run=state["run_id"], episodes=count, arm=arm))
        models[arm] = dict(path=str(path.resolve()), sha256=sha(path), parameters_sha256=parameter_hash(model))
        metrics[arm] = validate_model(path, pool_data, directory / "validation" / str(count) / (arm + ".json"))
    metric = [sum(m[i] for m in metrics.values()) for i in range(3)]
    event = dict(episodes=count, metric=metric, arm_metrics=metrics, models=models)
    state.setdefault("checks", []).append(event)
    if "best_metric" not in state or tuple(metric) > tuple(state["best_metric"]):
        state.update(best_metric=metric, selected=event, stalls=0)
    elif count > 0:
        state["stalls"] += 1
    return event


def check_records(records, group, pool_data):
    for index, row in enumerate(records):
        seed = plan()["training"]["seed_start"] + index
        start = curriculum(pool_data, index, seed)
        if row.get("index") != index or row.get("seed") != seed or row.get("start_board") != start:
            raise ValueError("Нарушен точный префикс training seed/start")
        if row.get("invalid"):
            raise ValueError("Training содержит сохранённый " + row["invalid"])
        if set(row.get("rows", {})) != set(GROUPS[group]):
            raise ValueError("Потеряна запись training arm")
        for episode in row["rows"].values():
            check_episode(episode, seed)
            if episode["result"] == "limit" or episode.get("start_board") != start:
                raise ValueError("Неверный training outcome/start")
        if group == "matched":
            left, right = row["rows"]["mc_teacher"], row["rows"]["td_teacher"]
            if any(left[key] != right[key] for key in ("trajectory_digest", "board", "moves", "result", "first_caps")):
                raise ValueError("Matched trajectory изменена")


def check_selection(state, pool_data):
    """Проверка завершённых validation/checkpoint, без продолжения эпизодов."""
    checks = state["checks"]
    expected_counts = [0] + plan()["training"]["milestones"]
    best = None
    stalls = 0
    for index, event in enumerate(checks):
        if event.get("episodes") != expected_counts[index] or set(event["models"]) != set(GROUPS[state["group"]]):
            raise ValueError("Изменён префикс milestone checks")
        metrics = {}
        for arm, entry in event["models"].items():
            if sha(entry["path"]) != entry["sha256"]:
                raise ValueError("Milestone model изменена")
            model = backend().TDModel.load(entry["path"])
            if parameter_hash(model) != entry["parameters_sha256"] or model.metadata.get("td_cycle_run") != state["run_id"]:
                raise ValueError("Milestone параметры изменены")
            path = Path(entry["path"]).parents[2] / "validation" / str(event["episodes"]) / (arm + ".json")
            if not path.exists() or not read(path).get("complete") or "metric" not in read(path):
                raise ValueError("Validation milestone не завершён")
            metrics[arm] = validate_model(Path(entry["path"]), pool_data, path)
        metric = [sum(m[i] for m in metrics.values()) for i in range(3)]
        if metrics != event["arm_metrics"] or metric != event["metric"]:
            raise ValueError("Изменены milestone metrics")
        if best is None or tuple(metric) > tuple(best["metric"]):
            best, stalls = event, 0
        elif event["episodes"]:
            stalls += 1
    if checks and (state.get("selected") != best or state.get("best_metric") != best["metric"] or state.get("stalls") != stalls):
        raise ValueError("Изменён выбор общего milestone")


def train(group, resume=False, out=None, *, validate_only=False):
    out = checked_output(out or ROOT / ("train-" + group + ".json"))
    header = state_header(group)
    directory = group_directory(out)
    pool_data = load_pool()
    if out.exists():
        if not resume:
            raise ValueError("Training-checkpoint существует; требуется --resume")
        state = read(out)
        if any(state.get(k) != v for k, v in header.items()):
            raise ValueError("Training-checkpoint несовместим")
        models = current_models(state)
        records = blocks(directory, state["run_id"])
        check_records(records, group, pool_data)
        count = state["checkpoint_episodes"]
        if count > len(records) or state.get("records_sha256") != record_chain(records[:count]):
            raise ValueError("Модель опережает training records")
        check_selection(state, pool_data)
        if state.get("complete"):
            plateau = len(state["checks"]) - 1 >= 3 and state["stalls"] >= 2
            if (count != len(records) or state["checks"][-1]["episodes"] != count or
                    state.get("stop_reason") != ("plateau" if plateau else "budget_limited") or
                    not (plateau or count == plan()["training"]["milestones"][-1])):
                raise ValueError("Завершённый checkpoint имеет незаписанный хвост")
            return state
    else:
        if resume:
            raise ValueError("Training для --resume не существует")
        if validate_only:
            raise ValueError("Training не завершён")
        training_open()
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Чужая папка training artifacts")
        state = dict(header, run_id=digest(header), checkpoint_episodes=0, checks=[], stalls=0, complete=False,
                     records_sha256=record_chain([]))
        trainers = {arm: new_trainer() for arm in GROUPS[group]}
        save_current(state, trainers, directory, 0)
        atomic(out, state)
        models, records = current_models(state), []
    if validate_only:
        raise ValueError("Training не завершён; неявное продолжение запрещено")
    training_open()
    trainers = {arm: new_trainer(model) for arm, model in models.items()}
    chain = state["records_sha256"]
    for old in records[state["checkpoint_episodes"]:]:
        replay = training_episode(trainers, pool_data, old["index"], group)
        if any(stable_episode(replay["rows"][arm]) != stable_episode(old["rows"][arm]) for arm in GROUPS[group]) or replay.get("invalid"):
            raise ValueError("Детерминированное восстановление model-tail не совпало с журналом")
        chain = record_chain([old], chain)
    milestones = plan()["training"]["milestones"]
    def finish_boundary(count):
        # Вызывается и после replay: interruption между model-checkpoint и
        # validation не позволяет перескочить незаконченный milestone.
        if count not in [0, *milestones] or any(c["episodes"] == count for c in state["checks"]):
            return
        if state["checkpoint_episodes"] != count:
            save_current(state, trainers, directory, count)
        state["records_sha256"] = chain
        state["pending_validation"] = count
        atomic(out, state)
        milestone(state, trainers, pool_data, directory, count)
        state.pop("pending_validation", None)
        checked = len(state["checks"]) - 1
        plateau = checked >= 3 and state["stalls"] >= 2
        state["complete"] = plateau or count == milestones[-1]
        state["stop_reason"] = "plateau" if plateau else "budget_limited" if state["complete"] else None
        atomic(out, state)
        progress("validation", group=group, episodes=count, metric=state["checks"][-1]["metric"], complete=state["complete"])
    finish_boundary(len(records))
    if state["complete"]:
        return state
    for index in range(len(records), milestones[-1]):
        training_open()
        row = training_episode(trainers, pool_data, index, group)
        append_block(directory, state["run_id"], index, row)
        records.append(row)
        if row.get("invalid"):
            raise ValueError("Training остановлен: " + row["invalid"] + "; веса эпизода не сохранены")
        chain = record_chain([row], chain)
        count = index + 1
        if count % plan()["training"]["checkpoint_every"] == 0 or count in milestones:
            save_current(state, trainers, directory, count)
            state["records_sha256"] = chain
            atomic(out, state)
            progress("train", group=group, episodes=count)
        if count in milestones:
            finish_boundary(count)
            if state["complete"]:
                return state
    return state


def selected_models():
    result = {}
    for group in GROUPS:
        path = ROOT / ("train-" + group + ".json")
        data = train(group, resume=True, out=path, validate_only=True)
        for arm, entry in data["selected"]["models"].items():
            if sha(entry["path"]) != entry["sha256"]:
                raise ValueError("Selected model изменена")
            result[arm] = Path(entry["path"])
    result["old_mc"] = WORK / "experiments/learning/model.bin"
    return result


def learning_helpers():
    path = WORK / "experiments/learning/cycle.py"
    spec = importlib.util.spec_from_file_location("td_timed_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_arm(arm, models):
    bot = importlib.import_module("bot")
    bot.NATIVE = bot.load_engine(bot.ENGINE)
    bot.configure_value_model(None if arm == "heuristic" else models[arm])
    return bot


def timed_play(seed, arm, models, config):
    bot = configure_arm(arm, models)
    benchmark = importlib.import_module("benchmark")
    table = spawn_table()
    benchmark.apply_table(table)
    rng = random.Random(seed)
    started = time.monotonic()
    board = benchmark.spawn(benchmark.spawn((0,) * 16, rng, table), rng, table)
    moves, calls, positions, fallbacks, cuts = 0, 0, 0, 0, 0
    search_wall = 0.0
    depths, requests, firsts, snapshots = {}, {}, {}, {}
    while moves < config["max_moves"] and not bot.reached_goal(board):
        info = {}
        began = time.monotonic()
        direction = bot.choose(board, config["depth"], config["think"], info, policy="progressive")
        search_wall += time.monotonic() - began
        calls += 1
        positions += int(info.get("positions", 0))
        fallbacks += bool(info.get("fallback", False))
        cuts += bool(info.get("cut", False))
        if direction is None:
            break
        if direction not in [d for d, _ in bot.legal_moves(board)]:
            raise ValueError("Решатель вернул нелегальный ход")
        after = bot.move(board, direction)
        actual = str(int(info.get("depth", 1)))
        requested = str(int(info.get("requested_depth", config["depth"])))
        depths[actual], requests[requested] = depths.get(actual, 0) + 1, requests.get(requested, 0) + 1
        # Goal заканчивает эпизод до spawn, как в native teacher/trainer.
        board = after if bot.reached_goal(after) else benchmark.spawn(after, rng, table)
        moves += 1
        caps = board.count(bot.CAP)
        if caps and str(caps) not in firsts:
            firsts[str(caps)] = moves
            if caps in (1, 2) and valid_start(board, caps):
                snapshots[str(caps)] = list(board)
    result = "goal" if bot.reached_goal(board) else "loss" if not bot.legal_moves(board) else "limit"
    return dict(seed=seed, arm=arm, depth=config["depth"], endgame_depth=None, think=config["think"],
                result=result, moves=moves, caps=board.count(bot.CAP), max=max(board), board=list(board),
                seconds=time.monotonic() - started, search_wall_seconds=search_wall, search_calls=calls,
                depth_counts=depths, requested_depth_counts=requests, fallbacks=fallbacks, cuts=cuts,
                positions=positions, cap_reached_at=firsts, cap_boards=snapshots, sampled_positions=0)


def evaluation_sources(models):
    bot = importlib.import_module("bot")
    return dict(producers=source_fingerprint(), engine=dict(path=str(Path(bot.ENGINE).resolve()), sha256=sha(bot.ENGINE)),
                models={arm: dict(path=str(path.resolve()), sha256=sha(path)) for arm, path in models.items()})


def verify_freeze(frozen):
    if (not isinstance(frozen, dict) or type(frozen.get("version")) is not int or frozen["version"] != 1 or
            frozen.get("finalist") not in ("td_teacher", "td_selfplay") or
            type(frozen.get("promoted")) is not bool or frozen.get("policy") != plan()["holdout"]):
        raise ValueError("Некорректная версия/TD-finalist/gate/policy заморозки")
    hashes = frozen.get("hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Нет hashes заморозки")
    for path, expected in hashes.items():
        if sha(path) != expected:
            raise ValueError("Замороженный файл изменён: " + path)
    screen = evaluate("screen", resume=True, validate_only=True)
    table, finalist, promoted = screen_decision(screen)
    models = {arm: str(Path(entry["path"]).resolve()) for arm, entry in screen["sources"]["models"].items()}
    if (frozen.get("screen") != table or frozen["finalist"] != finalist or
            frozen["promoted"] != promoted or frozen.get("models") != models):
        raise ValueError("Содержимое заморозки не соответствует завершённому screen/моделям/отбору")


def evaluate(stage, resume=False, out=None, *, validate_only=False):
    out = checked_output(out or ROOT / (stage + ".json"))
    models = selected_models()
    config = dict(plan()[stage], max_moves=plan()["max_moves"])
    if stage == "holdout":
        frozen = read(ROOT / "finalist.json")
        verify_freeze(frozen)
        config["arms"] = ["heuristic", "mc_teacher", frozen["finalist"]]
        config["finalist_sha256"] = sha(ROOT / "finalist.json")
    sources = evaluation_sources(models)
    header = dict(version=1, kind=stage, config=config, sources=sources, spawn_table=spawn_table())
    arms = config["arms"]
    schedule = [(seed, arm) for offset, seed in enumerate(range(config["seed_start"], config["seed_start"] + config["games"]))
                for arm in arms[offset % len(arms):] + arms[:offset % len(arms)]]
    if out.exists():
        if not resume:
            raise ValueError("Evaluation существует; нужна --resume")
        data = read(out)
        if any(data.get(k) != v for k, v in header.items()):
            raise ValueError("Evaluation-checkpoint несовместим")
        learning_helpers().validate_games(data["games"], schedule, config)
        if data.get("complete") != (len(data["games"]) == len(schedule)):
            raise ValueError("Неверный evaluation prefix")
        if any(row["result"] == "limit" for row in data["games"]):
            raise ValueError("Evaluation содержит limit")
        if data["complete"]:
            return data
    else:
        if resume:
            raise ValueError("Evaluation для --resume не существует")
        data = dict(header, games=[], complete=False)
    if validate_only:
        raise ValueError("Evaluation не завершён; неявное продолжение запрещено")
    evaluation_marker(stage, out)  # Всегда глобально, в том числе при произвольном --out.
    atomic(out, data)
    for seed, arm in schedule[len(data["games"]):]:
        row = timed_play(seed, arm, models, config)
        data["games"].append(row)
        learning_helpers().validate_games(data["games"], schedule, config)
        data["complete"] = len(data["games"]) == len(schedule)
        atomic(out, data)
        progress(stage, seed=seed, arm=arm, result=row["result"])
        if row["result"] == "limit":
            raise ValueError("Evaluation limit сохранён, этап остановлен")
    return data


def summaries(games):
    result = {}
    for arm in sorted({g["arm"] for g in games}):
        rows = [g for g in games if g["arm"] == arm]
        moves = sum(g["moves"] for g in rows)
        result[arm] = dict(games=len(rows), goals=sum(g["result"] == "goal" for g in rows),
                           win_rate=sum(g["result"] == "goal" for g in rows) / len(rows),
                           losses=sum(g["result"] == "loss" for g in rows), limits=sum(g["result"] == "limit" for g in rows),
                           moves=moves, seconds=sum(g["seconds"] for g in rows),
                           search_wall_seconds=sum(g["search_wall_seconds"] for g in rows),
                           choose_wall_per_move=sum(g["search_wall_seconds"] for g in rows) / moves if moves else None)
    return result


def screen_decision(screen):
    """Один и тот же зафиксированный отбор при записи и чтении заморозки."""
    if not screen["complete"]:
        raise ValueError("Screen не завершён")
    table = summaries(screen["games"])
    order = ["td_teacher", "td_selfplay"]
    finalist = min(order, key=lambda arm: (-table[arm]["goals"], table[arm]["choose_wall_per_move"]
                                         if table[arm]["choose_wall_per_move"] is not None else math.inf, order.index(arm)))
    promoted = (table[finalist]["goals"] > table["heuristic"]["goals"] and
                table[finalist]["goals"] >= table["mc_teacher"]["goals"] and
                table[finalist]["goals"] >= table["old_mc"]["goals"])
    return table, finalist, promoted


def freeze(out=None):
    out = checked_output(out or ROOT / "finalist.json")
    if out.exists() or (ROOT / "finalist.json").exists() or (ROOT / "holdout.json").exists():
        raise ValueError("Заморозка уже существует или контроль уже начат")
    screen = evaluate("screen", resume=True, validate_only=True)
    table, finalist, promoted = screen_decision(screen)
    models = selected_models()
    engine_path = Path(screen["sources"]["engine"]["path"])
    paths = set(producer_paths()) | {trainer_path(), engine_path}
    paths |= {p for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".json", ".bin")}
    paths |= set(models.values())
    paths.discard(out)
    paths.discard(ROOT / "summary.json")
    data = dict(version=1, finalist=finalist, promoted=promoted, screen=table, models={arm: str(path.resolve()) for arm, path in models.items()},
                policy=plan()["holdout"], hashes={str(path.resolve()): sha(path) for path in sorted(paths)})
    atomic(out, data)
    return data


def paired(games, candidate, baseline):
    left = {g["seed"]: g for g in games if g["arm"] == candidate}
    right = {g["seed"]: g for g in games if g["arm"] == baseline}
    if (len(left) != sum(g["arm"] == candidate for g in games) or
            len(right) != sum(g["arm"] == baseline for g in games) or
            len(left) != len(right) or left.keys() != right.keys() or not left):
        raise ValueError("Непарные seed")
    if any(g["result"] == "limit" for g in [*left.values(), *right.values()]):
        raise ValueError("Censored comparison")
    gained = sorted(seed for seed in left if left[seed]["result"] == "goal" and right[seed]["result"] != "goal")
    lost = sorted(seed for seed in left if left[seed]["result"] != "goal" and right[seed]["result"] == "goal")
    n = len(gained) + len(lost)
    p = sum(math.comb(n, k) for k in range(len(gained), n + 1)) / 2 ** n if n else 1.0
    return dict(candidate=candidate, baseline=baseline, games=len(left), gained=gained, lost=lost, one_sided_p=p,
                candidate_goals=sum(g["result"] == "goal" for g in left.values()), baseline_goals=sum(g["result"] == "goal" for g in right.values()))


def finalize():
    frozen = read(ROOT / "finalist.json")
    verify_freeze(frozen)
    data = evaluate("holdout", resume=True, validate_only=True)
    if not data["complete"]:
        raise ValueError("Контроль не завершён")
    checks = [paired(data["games"], frozen["finalist"], baseline) for baseline in ("heuristic", "mc_teacher")]
    confirmed = frozen["promoted"] and all(c["candidate_goals"] > c["baseline_goals"] and c["one_sided_p"] <= .025 for c in checks)
    cost = summaries(data["games"])
    for comparison in checks:
        candidate, reference = (cost[comparison[side]]["choose_wall_per_move"] for side in ("candidate", "baseline"))
        comparison["choose_wall_ratio"] = candidate / reference if reference and candidate is not None else None
        ratio = comparison["choose_wall_ratio"]
        comparison["actual_cost_within_10_percent"] = ratio is not None and .9 <= ratio <= 1.1
    result = dict(version=1, finalist=frozen["finalist"], screen_promoted=frozen["promoted"], joint_confirmed=confirmed,
                  comparisons=checks, cost=cost, scope="serial10ms offline only; no default or phone switch",
                  holdout_sha256=sha(ROOT / "holdout.json"), freeze_sha256=sha(ROOT / "finalist.json"))
    atomic(ROOT / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build")
    for action in ("pool", "train", "screen", "freeze", "holdout", "finalize"):
        command = sub.add_parser(action)
        if action in ("pool", "train", "screen", "holdout"):
            command.add_argument("--resume", action="store_true")
        if action == "train":
            command.add_argument("--group", choices=GROUPS, required=True)
        if action != "finalize":
            command.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        with lease():
            if args.action == "build":
                result = build()
            elif args.action == "pool":
                result = pool(args.resume, args.out)
            elif args.action == "train":
                result = train(args.group, args.resume, args.out)
            elif args.action == "freeze":
                result = freeze(args.out)
            elif args.action == "finalize":
                result = finalize()
            else:
                result = evaluate(args.action, args.resume, args.out)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    progress("done", action=args.action, complete=result.get("complete"), confirmed=result.get("joint_confirmed"))


if __name__ == "__main__":
    main()
