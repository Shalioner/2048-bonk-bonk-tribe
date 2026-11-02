"""Opt-in banked_endgame: только измеренный профиль pure Bonk поиска.

Контроль035e214:63/100 против49/100; wall/ход~1.595x, CPU/ход~1.562x.
Это другая цена, не равный бюджет. Номинал10мс на вызов, до40мс при двух CAP
и накопленном credit. Wall не имеет строгого потолка: максимум в контроле~168мс;
это наблюдение, не гарантия. Телефон и режим10с/ход этим контролем не проверены.
Баланс начинается с0 для одного запуска/одной игры; перерасход остаётся долгом.
"""
import argparse
import importlib
import importlib.util
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
ARM = "banked_endgame"
DEPTH, NOMINAL, POLICY = 8, .01, "progressive"
PROFILE = dict(mode="ledger", nominal_allowance=.01, early_call_cap=.01, two_cap_call_cap=.04)
THRESHOLDS = (1e-4, 1e-5, 1e-6)


def load_cycle():
    path = ROOT / "experiments/search_alternatives/run.py"
    spec = importlib.util.spec_from_file_location("banked_entry_cycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verified_profile(cycle):
    """Read-only equivalent of the runner's confirmation gate; caller holds lease."""
    frozen = cycle.verify_freeze()
    control = cycle.checkpoint("control", True, validate_only=True)
    paired = cycle.paired(control["games"])
    summary = cycle.read(cycle.ROOT / "summary.json")
    if (control.get("complete") is not True or frozen.get("finalist") != ARM or
            paired.get("improved") is not True or summary.get("confirmed_improvement") is not True or
            summary.get("finalist") != ARM or summary.get("paired") != paired or
            summary.get("freeze_sha256") != cycle.sha(cycle.ROOT / "finalist.json") or
            summary.get("control_sha256") != cycle.sha(cycle.ROOT / "control.json")):
        raise ValueError("Нужен завершённый замороженный контроль с подтверждённым banked_endgame")
    arm = cycle.arm_config(ARM)
    plan = cycle.plan()
    profile = plan["budget_profiles"][arm["budget_profile"]]
    if (arm.get("kind") != ARM or tuple(arm["thresholds"]) != THRESHOLDS or profile != PROFILE or
            plan["search"].get("depth") != DEPTH or plan["search"].get("think") != NOMINAL or
            plan["search"].get("policy") != POLICY):
        raise ValueError("Frozen policy не соответствует измеренному профилю10мс/depth8/progressive/late40мс")
    return dict(profile)


class BankedChooser:
    """One game/session ledger; reuse the frozen runner's allocation/charge math."""
    def __init__(self, cycle, profile, choose):
        self.cycle, self.profile, self.choose = cycle, dict(profile), choose
        self.credit = 0.0

    def __call__(self, board, depth=DEPTH, think=NOMINAL, info=None, policy=POLICY):
        if depth != DEPTH or think != NOMINAL or policy != POLICY:
            raise ValueError("Banked entry поддерживает только10мс/depth8/progressive")
        budget, self.credit = self.cycle.allocate_budget(self.profile, self.credit, board.count(11))
        started = time.monotonic()
        try:
            return self.choose(board, depth, budget, info, policy=policy)
        finally:
            elapsed = time.monotonic() - started
            self.credit = self.cycle.charge_budget(self.credit, elapsed)
            if info is not None:
                info.update(budget_profile=ARM, supplied_budget_s=budget,
                            external_choose_wall_s=elapsed, budget_credit_s=self.credit)


def forwarded_argv(args):
    result = [str(ROOT / "bot.py"), "--steps", str(args.steps), "--delay", str(args.delay),
              "--think", str(NOMINAL), "--depth", str(DEPTH), "--search-policy", POLICY]
    if args.serial is not None:
        result.extend(("--serial", args.serial))
    if args.dry_run:
        result.append("--dry-run")
    return result


def launch(cycle, args):
    bot, previous = None, None
    argv = sys.argv
    try:
        with cycle.lease():
            profile = verified_profile(cycle)
            bot = importlib.import_module("bot")
            previous = bot.choose, bot.NATIVE, bot.THRESHOLDS
            configured = cycle.configure(ARM)
            if configured is not bot or tuple(bot.THRESHOLDS) != THRESHOLDS or not hasattr(bot.NATIVE, "solve_budget_progressive"):
                raise ValueError("Выбранный native engine/thresholds не поддерживают измеренный progressive")
            bot.choose = BankedChooser(cycle, profile, previous[0])
            sys.argv = forwarded_argv(args)
        return bot.main()
    finally:
        sys.argv = argv
        if previous is not None:
            bot.choose, bot.NATIVE, bot.THRESHOLDS = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--steps", type=int, default=10000)
    parser.add_argument("--delay", type=float, default=.4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--depth", type=int, default=DEPTH, help="только измеренное значение8")
    parser.add_argument("--think", type=float, default=NOMINAL, help="только номинал0.01с; late-call cap0.04с с credit")
    parser.add_argument("--search-policy", choices=(POLICY,), default=POLICY)
    args = parser.parse_args(argv)
    if args.depth != DEPTH or not math.isfinite(args.think) or args.think != NOMINAL:
        parser.error("Этот opt-in режим измерен только при --think 0.01 --depth 8 --search-policy progressive")
    try:
        return launch(load_cycle(), args)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Остановлено")
