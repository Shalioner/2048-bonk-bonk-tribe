"""Isolated native search variants; precision scheduling belongs to the frontend."""


def _once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Неоднозначный/несовместимый search source anchor: " + old[:70])
    return source.replace(old, new)


def variant_source(base_bytes, arm):
    if not isinstance(base_bytes, bytes) or not isinstance(arm, dict):
        raise ValueError("Нужны bytes исходника и dict варианта")
    kind = arm.get("kind")
    if kind not in ("baseline", "canonical", "serial_shallow", "precision", "banked_endgame"):
        raise ValueError("Неизвестный search kind")
    if kind in ("baseline", "precision", "banked_endgame"):
        return base_bytes
    source = base_bytes.decode("utf-8")
    if "BONK_SEARCH_VARIANT" in source or '"search_variants.h"' in source:
        raise ValueError("Комбинации/повторные инъекции search вариантов запрещены")
    marker = "// BONK_SEARCH_VARIANT: " + kind + "\n"
    source = _once(source, '#include "2048.h"\n', '#include "2048.h"\n' + marker +
                   ('#include "search_variants.h"\n' if kind == "canonical" else ""))
    if kind == "canonical":
        anchor = "    search_cache_key cache_key = {board, state.depth_limit - state.curdepth, 0};"
        return _once(source, anchor, "    board = bonk_search::canonical(board);\n" + anchor).encode("utf-8")
    depth = arm.get("serial_max_depth", 2)
    if type(depth) is not int or depth != 2:
        raise ValueError("serial_max_depth должен быть фиксированным2")
    # Restrict replacement to progressive; solve_budget remains byte-for-byte.
    split = 'extern "C" int solve_budget_progressive('
    if source.count(split) != 1:
        raise ValueError("Нет однозначного progressive solver")
    before, progressive = source.split(split)
    old = '''        std::vector<std::thread> workers;
        for (int d = 0; d < 4; ++d) {
            if (!(allowed & (1 << d))) continue;
            workers.push_back(std::thread([&, d, pass_depth, threshold]() {
                if (clock_t_::now() >= deadline) { aborted[d] = true; stop.store(true); return; }
                eval_state st;
                st.depth_limit = pass_depth;
                st.cprob_thresh = threshold;
                st.has_deadline = true;
                st.deadline = deadline;
                st.stop = &stop;
                scores[d] = _score_toplevel_move(st, board, d);
                evaled[d] = st.moves_evaled;
                // Последний короткий фрагмент может не попасть на periodic check.
                aborted[d] = st.aborted || clock_t_::now() >= deadline;
                if (aborted[d]) stop.store(true);
            }));
        }
        for (size_t w = 0; w < workers.size(); ++w) workers[w].join();'''
    body = old.split("            workers.push_back(std::thread([&, d, pass_depth, threshold]() {\n", 1)[1].split("            }));", 1)[0]
    # The common body preserves the pre/post deadline checks, stop and local state.
    new = "        const auto evaluate = [&, pass_depth, threshold](int d) {\n" + body + "        };\n" + '''        if (pass_depth <= 2) {
            for (int d = 0; d < 4; ++d) if (allowed & (1 << d)) evaluate(d);
        } else {
            std::vector<std::thread> workers;
            for (int d = 0; d < 4; ++d)
                if (allowed & (1 << d)) workers.push_back(std::thread(evaluate, d));
            for (size_t w = 0; w < workers.size(); ++w) workers[w].join();
        }'''
    return (before + split + _once(progressive, old, new)).encode("utf-8")
