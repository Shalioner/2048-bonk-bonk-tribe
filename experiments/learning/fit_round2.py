"""Два заранее заданных training-only уточнения, без изменения исходного цикла."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
REQUIRED_IMPROVEMENT = 0.001
VARIANTS = [dict(name=name, epochs=64, learning_rate=rate, l2=1e-4,
                 patience=12, min_delta=1e-5) for name, rate in (("lr2", 2.0), ("lr10", 10.0))]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON object: " + str(path))
    return data


def _atomic_bytes(path, raw):
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


def _atomic_json(path, data):
    _atomic_bytes(path, (json.dumps(data, sort_keys=True, ensure_ascii=False,
                                  allow_nan=False, indent=2) + "\n").encode())


def _atomic_copy(source, destination):
    _atomic_bytes(destination, Path(source).read_bytes())


def _probability(value, name):
    if isinstance(value, bool):
        raise ValueError("Некорректная метрика " + name)
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Некорректная метрика " + name)
    return value


def _check_inputs(context):
    for path, digest in context["inputs"].items():
        if not Path(path).is_file() or sha(path) != digest:
            raise ValueError("Изменилось происхождение round2: " + str(path))


def _evaluated():
    return any((ROOT / name).exists() for name in ("screen.json", "holdout.json", "finalist.json"))


def _context(cycle, module):
    plan_path = ROOT / "model-round2-plan.json"
    plan = _read(plan_path)
    if plan.get("variants") != VARIANTS:
        raise ValueError("Разрешены только два заранее заданных уточнения lr2/lr10")
    datasets = {split: sha(ROOT / ("teacher-" + split + ".json")) for split in ("train", "validation")}
    if datasets != plan.get("dataset_sha256"):
        raise ValueError("Dataset SHA не совпадает с model-round2-plan")
    archive_paths = dict(model=ROOT / "pilot-model-v1.bin", metrics=ROOT / "pilot-training-metrics-v1.json")
    archives = {name: sha(path) for name, path in archive_paths.items()}
    if archives != dict(model=plan.get("initial_model_sha256"), metrics=plan.get("initial_metrics_sha256")):
        raise ValueError("SHA архивов v1 не совпадает с model-round2-plan")
    original = _read(archive_paths["metrics"])
    base_sources = dict(cycle=sha(cycle.__file__), plan=sha(ROOT / "plan.json"), value_model=sha(module.__file__))
    if (original.get("datasets") != datasets or original.get("source_hashes") != base_sources or
            original.get("model_sha256") != archives["model"]):
        raise ValueError("Происхождение исходного fit не совпадает с текущим циклом")
    previous = module.ValueModel.load(archive_paths["model"])
    if previous.training != original.get("metrics"):
        raise ValueError("Параметры/метрики архивной модели v1 не соответствуют друг другу")
    v1_brier = _probability(original["metrics"]["validation_brier"], "v1 Brier")
    prior = _probability(original["metrics"]["stage_prior_validation_brier"], "stage prior")
    data = {}
    for split in ("train", "validation"):
        loaded = cycle.load_teacher(split)  # Никогда не продолжать сбор неявно.
        if not loaded.get("complete") or not loaded.get("records"):
            raise ValueError("Teacher dataset не завершён или не содержит записей")
        if any(game.get("result") == "limit" for game in loaded.get("games", [])):
            raise ValueError("Limit делает training-only уточнение недействительным")
        if loaded != _read(ROOT / ("teacher-" + split + ".json")):
            raise ValueError("load_teacher вернул другой dataset")
        data[split] = loaded
    train_seeds = {row["seed"] for row in data["train"]["records"]}
    validation_seeds = {row["seed"] for row in data["validation"]["records"]}
    if train_seeds & validation_seeds:
        raise ValueError("Train/validation делят seed целой партии")
    sources = dict(base_sources, refinement_runner=sha(Path(__file__)), refinement_plan=sha(plan_path))
    inputs = {Path(cycle.__file__): base_sources["cycle"], ROOT / "plan.json": base_sources["plan"],
              Path(module.__file__): base_sources["value_model"], Path(__file__): sources["refinement_runner"],
              plan_path: sources["refinement_plan"]}
    inputs.update({ROOT / ("teacher-" + split + ".json"): digest for split, digest in datasets.items()})
    inputs.update({archive_paths[name]: digest for name, digest in archives.items()})
    configuration = original["metrics"].get("configuration", {})
    context = dict(datasets=datasets, archives=archives, sources=sources, inputs=inputs, data=data,
                   v1_brier=v1_brier, prior=prior,
                   batch_size=configuration.get("batch_size", 256), seed=configuration.get("seed", 0))
    _check_inputs(context)
    return context


def _header(context, variant):
    config = {key: value for key, value in variant.items() if key != "name"}
    config.update(batch_size=context["batch_size"], seed=context["seed"])
    return dict(checkpoint_version=1, kind="fit", refinement_variant=variant["name"], config=config,
                datasets=context["datasets"], source_hashes=context["sources"], archives=context["archives"])


def _result(context, header, metrics, model_sha):
    brier = _probability(metrics["validation_brier"], "Brier")
    prior = _probability(metrics["stage_prior_validation_brier"], "stage prior")
    if not math.isclose(prior, context["prior"], rel_tol=0, abs_tol=1e-12):
        raise ValueError("Изменён stage-prior baseline")
    improvement = prior - brier
    passed = metrics.get("validation_gate_passed") is True and improvement >= REQUIRED_IMPROVEMENT
    return dict(header, metrics=metrics, model_sha256=model_sha, validation_gate_passed=passed,
                gate=dict(brier=brier, stage_prior_brier=prior, improvement=improvement,
                          required_improvement=REQUIRED_IMPROVEMENT, passed=passed))


def _variant(context, variant, model_class, *, resume, decision_exists):
    name = variant["name"]
    model_path, metrics_path = ROOT / "refinement" / (name + ".bin"), ROOT / "refinement" / (name + "-metrics.json")
    header = _header(context, variant)
    if model_path.exists():
        if not resume:
            raise ValueError("Вариант уже существует; требуется --resume")
        model = model_class.load(model_path)
        if (model.metadata.get("refinement_checkpoint") != header or
                model.metadata.get("source_sha256") != context["sources"] or
                model.metadata.get("dataset_sha256") != context["datasets"]):
            raise ValueError("Модель refinement несовместима с источниками или планом")
        expected = _result(context, header, model.training, sha(model_path))
        if metrics_path.exists():
            if _read(metrics_path) != expected:
                raise ValueError("Fit-checkpoint refinement несовместим")
        else:
            # Fit и сохранение модели уже закончены. Метрики содержатся в самом
            # файле, поэтому восстановление не требует нового обучения.
            _atomic_json(metrics_path, expected)
    else:
        if metrics_path.exists() or decision_exists:
            raise ValueError("Нет модели уже зафиксированного варианта; повторный fit запрещён")
        model = model_class()
        metrics = model.fit(context["data"]["train"]["records"],
                            validation_records=context["data"]["validation"]["records"], **header["config"])
        # Проверить метрики до записи binary; отдельный план не ослабляет gate.
        _result(context, header, metrics, "not-yet-saved")
        if metrics != model.training:
            raise ValueError("fit return и model.training расходятся")
        _check_inputs(context)
        model.metadata.update(source_sha256=context["sources"], dataset_sha256=context["datasets"],
                              refinement_checkpoint=header)
        model.save(model_path)
        expected = _result(context, header, metrics, sha(model_path))
        _atomic_json(metrics_path, expected)
    return dict(name=name, model_path=model_path, metrics_path=metrics_path, metrics=expected,
                model_sha256=sha(model_path), metrics_sha256=sha(metrics_path))


def _decision(context, variants):
    # min сохраняет объявленный порядок при точном равенстве Brier.
    best = min(variants, key=lambda item: item["metrics"]["gate"]["brier"])
    accepted = (best["metrics"]["validation_gate_passed"] is True and
                best["metrics"]["gate"]["brier"] < context["v1_brier"])
    selected = best["name"] if accepted else None
    return dict(checkpoint_version=1, kind="model_round2_selection", status="selected" if accepted else "negative",
                source_hashes=context["sources"], datasets=context["datasets"], archives=context["archives"],
                required_improvement=REQUIRED_IMPROVEMENT, v1_brier=context["v1_brier"],
                best_variant=best["name"], selected_variant=selected,
                candidates=[dict(name=item["name"], model_sha256=item["model_sha256"],
                                 metrics_sha256=item["metrics_sha256"], gate=item["metrics"]["gate"],
                                 module_gate_passed=item["metrics"]["metrics"].get("validation_gate_passed") is True)
                            for item in variants],
                desired_active=dict(model=best["model_sha256"], metrics=best["metrics_sha256"])
                if accepted else dict(context["archives"]))


def _active_hashes():
    paths = dict(model=ROOT / "model.bin", metrics=ROOT / "training-metrics.json")
    if not all(path.is_file() for path in paths.values()):
        raise ValueError("Активная пара model/metrics отсутствует или неполна")
    return {name: sha(path) for name, path in paths.items()}


def _publish(context, decision, variants):
    active = _active_hashes()
    desired = decision["desired_active"]
    if decision["status"] == "negative":
        if active != context["archives"]:
            raise ValueError("Отрицательный round2 требует неизменную активную пару v1")
        return
    for kind in ("model", "metrics"):
        if active[kind] not in (context["archives"][kind], desired[kind]):
            raise ValueError("Активный файл имеет неизвестное происхождение: " + kind)
    if _evaluated() and active != desired:
        raise ValueError("После screen/holdout изменять активную модель запрещено")
    selected = next(item for item in variants if item["name"] == decision["selected_variant"])
    _check_inputs(context)
    # Сначала положительные метрики с SHA новой модели. Если процесс прервётся
    # до замены binary, существующий cycle.model_gate отвергнет несовпадение.
    if active["metrics"] != desired["metrics"]:
        _atomic_copy(selected["metrics_path"], ROOT / "training-metrics.json")
    if active["model"] != desired["model"]:
        _atomic_copy(selected["model_path"], ROOT / "model.bin")
    if _active_hashes() != desired:
        raise ValueError("Публикация активной пары не завершена")


def run(resume=False, *, cycle_module=None, model_class=None):
    # Lazy imports нужны и для запуска из любой папки, и для коротких mock-тестов
    # без нативного bot/engine. Реальные teacher всегда читает текущий cycle.
    sys.path.insert(0, str(WORK))
    import value_model
    if cycle_module is None:
        sys.path.insert(0, str(ROOT))
        import cycle as cycle_module
    if model_class is None:
        model_class = value_model.ValueModel
    context = _context(cycle_module, value_model)
    decision_path = ROOT / "refinement" / "selection.json"
    if decision_path.exists() and not resume:
        raise ValueError("Решение round2 уже существует; требуется --resume")
    if not decision_path.exists():
        if _evaluated():
            raise ValueError("Начинать уточнение после screen/holdout запрещено")
        if _active_hashes() != context["archives"]:
            raise ValueError("До выбора round2 активная пара должна оставаться v1")
    saved_decision = _read(decision_path) if decision_path.exists() else None
    if saved_decision is not None and any(saved_decision.get(key) != value for key, value in (
            ("source_hashes", context["sources"]), ("datasets", context["datasets"]), ("archives", context["archives"]))):
        raise ValueError("Сохранённый selection несовместим с источниками")
    (ROOT / "refinement").mkdir(exist_ok=True)
    variants = [_variant(context, variant, model_class, resume=resume, decision_exists=saved_decision is not None)
                for variant in VARIANTS]
    expected = _decision(context, variants)
    if saved_decision is not None:
        if saved_decision != expected:
            raise ValueError("Selection не соответствует сохранённым кандидатам")
    else:
        _check_inputs(context)
        _atomic_json(decision_path, expected)
    _publish(context, expected, variants)
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    arguments = parser.parse_args()
    result = run(arguments.resume)
    print(json.dumps({key: result[key] for key in ("status", "best_variant", "selected_variant", "required_improvement")}), flush=True)
