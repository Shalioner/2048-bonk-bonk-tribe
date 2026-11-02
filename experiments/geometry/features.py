"""Pure fixed-build feature variants; no product-core mutation or runtime knobs."""
import math


def _once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Неоднозначный/несовместимый pure source anchor: " + old[:80])
    return source.replace(old, new)


def variant_source(base_bytes, arm):
    """Return one independent baseline/segments/geometry/risk engine source.

    arm contains kind and strength; optional name/other metadata are ignored.
    Baseline is the original byte object, without even an extra include.
    """
    if not isinstance(base_bytes, bytes) or not isinstance(arm, dict):
        raise ValueError("Нужны bytes исходника и dict варианта")
    kind, strength = arm.get("kind"), arm.get("strength")
    allowed = {"baseline": (0,), "segments": (.5, 1), "geometry": (2000, 4000), "risk": (.5, 1)}
    if (kind not in allowed or type(strength) not in (int, float) or not math.isfinite(strength)
            or strength not in allowed[kind]):
        raise ValueError("Неизвестный kind/strength структурного варианта")
    if kind == "baseline":
        return base_bytes
    source = base_bytes.decode("utf-8")
    if '"geometry_features.h"' in source:
        raise ValueError("Комбинации/повторные инъекции вариантов запрещены")
    source = _once(source, '#include "2048.h"\n', '#include "2048.h"\n#include "geometry_features.h"\n')
    literal = format(float(strength), ".1f") + "f"
    if kind == "segments":
        source = _once(source, "std::min(monotonicity_left, monotonicity_right)",
                       "bonk_geometry::segmented_penalty(line, SCORE_MONOTONICITY_POWER, "
                       "monotonicity_left, monotonicity_right, " + literal + ")")
    else:
        if kind == "geometry":
            source = _once(source, "void init_tables() {\n",
                           "void init_tables() {\n    bonk_geometry::init_geometry_tables();\n")
        old = ("static float score_heur_board(board_t board) {\n"
               "    if(goal(board)) return 1e9f;\n"
               "    return score_helper(          board , heur_score_table) +\n"
               "           score_helper(transpose(board), heur_score_table);\n}")
        expression = ("raw + " + literal + " * bonk_geometry::geometry_penalty(board)" if kind == "geometry" else
                      "bonk_geometry::risk_adjusted(board, raw, " + literal + ", spawn_table[get_max_rank(board)])")
        new = ("static float score_heur_board(board_t board) {\n"
               "    if(goal(board)) return 1e9f;\n"
               "    const float raw = score_helper(          board , heur_score_table) +\n"
               "           score_helper(transpose(board), heur_score_table);\n"
               "    return " + expression + ";\n}")
        source = _once(source, old, new)
    return source.encode("utf-8")
