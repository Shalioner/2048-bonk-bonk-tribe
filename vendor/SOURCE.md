Source: https://github.com/nneonneo/2048-ai
Downloaded: 2026-10-29, master (no commit pin; downloaded sources retained).
License: MIT, see LICENSE.

Original files: 2048.cpp, 2048.h, platdefs.h, LICENSE.
Local additions: config.h, build.sh, engine_adapted.cpp.
Adaptation: goal three 13; preserve 13 in search when legal alternatives exist; export solve_levels with fixed depth; suppress interactive main in library.

2048.cpp: sha256 96c64da0a851935b708a325f3f8abb2c6eb8a07a0b8efadf90dd085d3e8ce00b
2048.h: sha256 2597ae4726e872494bb57997154fdb0279cfc706bb0aad7dcb758be5231ec85d
platdefs.h: sha256 5b3923979efb21112335319ff92a807cf47620002b446864eddea5f9925d983c
LICENSE: sha256 7916c6cf82fc4ffcec8ffbe70a202851c1cfc5868cf96bc648ab40c1ce5cf30b

2026-10-29: add rank-3 spawn branch, normalized runtime spawn probabilities (set_spawn_probs), score_levels diagnostics; Python updates prior weights from observed spawns. Search depth 5/6.

Extend spawn vector to 15 ranks with set_spawn_vector; Python confirms newly observed ranks in a second stable frame before updating the vector. Rank 4 witnessed through ADB.

2026-10-31: rules of the merge mode — rank 11 tiles never merge (move tables and the merge term
of the heuristic), goal is three rank-11 tiles; the rank-13 goal and preservation logic were removed.
Spawn probabilities are a 16x15 table indexed by the maximum rank of the board in each chance node
(set_spawn_table, get_spawn_prob; set_spawn_vector/set_spawn_probs fill every row with one vector).
The pruning threshold moved into eval_state. New solve_budget: passes with thresholds 1e-4, 1e-5,
1e-6, first moves evaluated in parallel std::threads, deadline with a margin inside the budget.
Exports get_cap_rank, get_goal_count, score_levels_thresh. config.h uses arc4random only on macOS;
build.sh also builds engine.so on Linux (tests and simulation).

2026-10-31 (review): the budget is hard — every full pass, including the first, stops at the
deadline; a depth-1 fallback computed before the passes provides the move if none completes
(info[7]). engine_api_version() = 2 lets bot.py reject a library built from older sources.

2026-11-01, local/strategy-learning-20261101, baseline 9b4120f:
the transposition key now includes remaining depth and exact cprob bits (cprob
omitted when the probability cutoff is disabled). An optional uncached
score_levels_reference validates the cache contract. Added optional
solve_budget_progressive; the original solve_budget algorithm remains available.
Added local value_model.h and load/clear diagnostics for an optional learned
afterstate value with consistent goal=1/loss=0. API2 is retained; Python checks
optional symbols when explicitly requested. Original 2048.cpp, 2048.h,
platdefs.h and LICENSE are unchanged. Local models, provenance, tests and
offline controls are documented in ../LEARNING.md and ../VALUE-IMPLEMENTATION.md.

2026-11-02, local/strategy-td-port-20261102, baseline 45fd360:
adapted n-tuple estimate/isomorphic update, state correction and forward TD(0)
from moporgic/TDL2048 commit a99f620aec0d30a75943a4c9646743f1f53b0197 (MIT,
Copyright2021 Hung Guei). Pinned originals, license and SHA are retained under
tdl2048-original; modified files retain MIT notices and adaptation notes.
New td_model.h uses binary goal11x3, clipped-linear value, target2/BONKTD01;
td_train.cpp reuses LOCAL Bonk moves/spawn/goal, not donor classic board rules.
Optional inference exports preserve existing API2/MC and default settings.
No pretrained classic2048 weights are installed. See ../TD.md and
../TD-MODEL-IMPLEMENTATION.md for exact adaptation, tests and experiment limits.
