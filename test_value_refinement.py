"""Протокол round2 на синтетических файлах; реальные fit/партии не запускаются."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import value_model
from experiments.learning import fit_round2 as refinement


class FakeModel(value_model.ValueModel):
    calls = []
    values = {2.0: 0.19, 10.0: 0.18}
    gates = {2.0: True, 10.0: True}

    def fit(self, records, *, validation_records, **config):
        rate = config["learning_rate"]
        self.calls.append(rate)
        self.bias[:] = rate / 100
        self.training = dict(fitted=True, configuration=config,
                             validation_brier=self.values[rate],
                             stage_prior_validation_brier=0.2,
                             validation_gate_passed=self.gates[rate], best_epoch=2,
                             train_seeds=[1], validation_seeds=[2])
        return copy.deepcopy(self.training)


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bonk-refinement-test-")
        self.root = Path(self.temp.name)
        self.original_root = refinement.ROOT
        refinement.ROOT = self.root
        FakeModel.calls = []
        FakeModel.values = {2.0: 0.19, 10.0: 0.18}
        FakeModel.gates = {2.0: True, 10.0: True}
        self.cycle_file = self.root / "cycle.py"
        self.cycle_file.write_text("# Synthetic cycle source, no engine or games\n")
        (self.root / "plan.json").write_text('{"training":{"teacher":"mock"}}\n')
        self.cycle = SimpleNamespace(__file__=str(self.cycle_file), load_teacher=self.load_teacher)
        for split, seed, label in (("train", 1, 1), ("validation", 2, 0)):
            data = dict(complete=True, games=[dict(seed=seed, result="goal" if label else "loss")],
                        records=[dict(seed=seed, board=[1, 2] + [0] * 14, label=label, episode_weight=1)])
            self.write(self.root / ("teacher-" + split + ".json"), data)
        self.datasets = {split: refinement.sha(self.root / ("teacher-" + split + ".json"))
                         for split in ("train", "validation")}
        self.base_sources = dict(cycle=refinement.sha(self.cycle_file),
                                 plan=refinement.sha(self.root / "plan.json"),
                                 value_model=refinement.sha(value_model.__file__))
        self.make_v1()

    def tearDown(self):
        refinement.ROOT = self.original_root
        self.temp.cleanup()

    @staticmethod
    def write(path, data):
        Path(path).write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")

    def load_teacher(self, split):
        return json.loads((self.root / ("teacher-" + split + ".json")).read_text())

    def make_v1(self, brier=0.2):
        v1 = value_model.ValueModel()
        v1.training = dict(fitted=True, configuration=dict(batch_size=256, seed=0),
                           validation_brier=brier, stage_prior_validation_brier=0.2,
                           validation_gate_passed=False)
        v1.metadata.update(source_sha256=self.base_sources, dataset_sha256=self.datasets)
        model_path = self.root / "pilot-model-v1.bin"
        metrics_path = self.root / "pilot-training-metrics-v1.json"
        v1.save(model_path)
        original = dict(checkpoint_version=1, kind="fit", config={"epochs": 16},
                        datasets=self.datasets, source_hashes=self.base_sources,
                        metrics=v1.training, model_sha256=refinement.sha(model_path),
                        validation_gate_passed=False, gate=dict(passed=False))
        self.write(metrics_path, original)
        (self.root / "model.bin").write_bytes(model_path.read_bytes())
        (self.root / "training-metrics.json").write_bytes(metrics_path.read_bytes())
        self.round_plan = dict(dataset_sha256=self.datasets, initial_model_sha256=refinement.sha(model_path),
                               initial_metrics_sha256=refinement.sha(metrics_path),
                               variants=copy.deepcopy(refinement.VARIANTS))
        self.write(self.root / "model-round2-plan.json", self.round_plan)
        self.initial_active = self.active()
        self.initial_archives = self.archives()

    def active(self):
        return {name: (self.root / filename).read_bytes() for name, filename in
                (("model", "model.bin"), ("metrics", "training-metrics.json"))}

    def archives(self):
        return {name: (self.root / filename).read_bytes() for name, filename in
                (("model", "pilot-model-v1.bin"), ("metrics", "pilot-training-metrics-v1.json"))}

    def run_refinement(self, resume=False):
        return refinement.run(resume, cycle_module=self.cycle, model_class=FakeModel)

    def test_selects_best_and_preserves_all_candidates_and_archives(self):
        result = self.run_refinement()
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_variant"], "lr10")
        self.assertEqual(FakeModel.calls, [2.0, 10.0])
        self.assertEqual(self.archives(), self.initial_archives)
        for name in ("lr2", "lr10"):
            self.assertTrue((self.root / "refinement" / (name + ".bin")).exists())
            metrics = refinement._read(self.root / "refinement" / (name + "-metrics.json"))
            self.assertEqual(metrics["gate"]["required_improvement"], 0.001)
            self.assertEqual(metrics["config"]["min_delta"], 1e-5)
            self.assertEqual(metrics["source_hashes"]["refinement_plan"], refinement.sha(self.root / "model-round2-plan.json"))
        self.assertEqual((self.root / "model.bin").read_bytes(), (self.root / "refinement/lr10.bin").read_bytes())
        self.assertEqual((self.root / "training-metrics.json").read_bytes(),
                         (self.root / "refinement/lr10-metrics.json").read_bytes())
        active = self.active()
        selected = self.run_refinement(resume=True)
        self.assertEqual(result, selected)
        self.assertEqual(self.active(), active)
        self.assertEqual(FakeModel.calls, [2.0, 10.0])
        with self.assertRaisesRegex(ValueError, "--resume"):
            self.run_refinement()

    def test_negative_gate_keeps_active_v1_byte_identical(self):
        FakeModel.values = {2.0: 0.1999, 10.0: 0.1995}
        result = self.run_refinement()
        self.assertEqual(result["status"], "negative")
        self.assertIsNone(result["selected_variant"])
        self.assertEqual(result["best_variant"], "lr10")
        self.assertEqual(result["archives"], dict(model=self.round_plan["initial_model_sha256"],
                                                  metrics=self.round_plan["initial_metrics_sha256"]))
        self.assertEqual(self.active(), self.initial_active)
        self.assertEqual(self.archives(), self.initial_archives)
        (self.root / "screen.json").write_text("{}")
        self.run_refinement(resume=True)  # Проверка готового решения не запускает fit.
        self.assertEqual(self.active(), self.initial_active)
        self.assertEqual(FakeModel.calls, [2.0, 10.0])

    def test_ties_use_declared_order_and_module_gate_is_required(self):
        FakeModel.values = {2.0: 0.19, 10.0: 0.19}
        result = self.run_refinement()
        self.assertEqual(result["selected_variant"], "lr2")
        # Новый независимый synthetic fixture, не перезапись существующих опытов.
        self.tearDown()
        self.setUp()
        FakeModel.gates[10.0] = False
        result = self.run_refinement()
        self.assertEqual(result["best_variant"], "lr10")
        self.assertEqual(result["status"], "negative")
        self.assertEqual(self.active(), self.initial_active)

    def test_candidate_must_also_beat_v1(self):
        self.make_v1(brier=0.17)
        result = self.run_refinement()
        self.assertEqual(result["status"], "negative")
        self.assertEqual(self.active(), self.initial_active)

    def test_publication_interrupt_blocks_hash_contract_then_resume_does_not_fit(self):
        original_copy = refinement._atomic_copy
        calls = []

        def interrupt_model(source, destination):
            calls.append(Path(destination).name)
            if Path(destination).name == "model.bin":
                raise RuntimeError("synthetic interruption between active metrics and binary")
            original_copy(source, destination)

        with patch.object(refinement, "_atomic_copy", side_effect=interrupt_model):
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                self.run_refinement()
        self.assertEqual(calls, ["training-metrics.json", "model.bin"])
        metrics = refinement._read(self.root / "training-metrics.json")
        self.assertIs(metrics["validation_gate_passed"], True)
        self.assertIs(metrics["gate"]["passed"], True)
        # Это именно условие отказа existing cycle.model_gate для положительного gate.
        self.assertNotEqual(metrics["model_sha256"], refinement.sha(self.root / "model.bin"))
        self.assertTrue((self.root / "refinement/selection.json").exists())
        result = self.run_refinement(resume=True)
        self.assertEqual(FakeModel.calls, [2.0, 10.0])
        self.assertEqual(refinement.sha(self.root / "model.bin"), result["desired_active"]["model"])
        self.assertEqual(self.archives(), self.initial_archives)

    def test_completed_model_recovers_missing_variant_metrics_without_refit(self):
        original_json = refinement._atomic_json

        def interrupt_first_metrics(path, data):
            if Path(path).name == "lr2-metrics.json":
                raise RuntimeError("synthetic interruption after trained model")
            original_json(path, data)

        with patch.object(refinement, "_atomic_json", side_effect=interrupt_first_metrics):
            with self.assertRaisesRegex(RuntimeError, "trained model"):
                self.run_refinement()
        self.assertTrue((self.root / "refinement/lr2.bin").exists())
        self.assertFalse((self.root / "refinement/lr2-metrics.json").exists())
        self.assertEqual(self.active(), self.initial_active)
        result = self.run_refinement(resume=True)
        self.assertEqual(FakeModel.calls, [2.0, 10.0])
        self.assertEqual(result["status"], "selected")

    def test_provenance_mismatch_refuses_resume_without_changing_active(self):
        self.run_refinement()
        active = self.active()
        self.cycle_file.write_text("# changed frozen cycle\n")
        with self.assertRaisesRegex(ValueError, "Происхождение"):
            self.run_refinement(resume=True)
        self.assertEqual(self.active(), active)
        self.assertEqual(FakeModel.calls, [2.0, 10.0])

    def test_datasets_archives_limits_and_post_evaluation_training_are_rejected(self):
        for kind in ("incomplete", "limit", "dataset_hash", "archive_hash", "screen"):
            with self.subTest(kind=kind):
                if kind == "dataset_hash":
                    path = self.root / "teacher-train.json"
                    path.write_text(path.read_text() + "\n")
                elif kind == "archive_hash":
                    path = self.root / "pilot-training-metrics-v1.json"
                    path.write_text(path.read_text() + "\n")
                elif kind == "screen":
                    (self.root / "screen.json").write_text("{}")
                else:
                    original_loader = self.cycle.load_teacher

                    def invalid_loader(split):
                        data = original_loader(split)
                        if kind == "incomplete":
                            data["complete"] = False
                        else:
                            data["games"][0]["result"] = "limit"
                        return data

                    self.cycle.load_teacher = invalid_loader
                with self.assertRaises(ValueError):
                    self.run_refinement()
                self.assertEqual(self.active(), self.initial_active)
                self.assertEqual(FakeModel.calls, [])
                self.tearDown()
                self.setUp()


if __name__ == "__main__":
    unittest.main()
