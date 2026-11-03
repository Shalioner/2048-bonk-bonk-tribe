# Second improvement round, preregistered before measurements
Baseline08992d5, candidatea8d0441. Exact fixed scores/decisions required. No heuristic/threshold/budget changes.
Screen: four existing boards indices0,3,7,16 seed2048; default progressive budget10s, one paired call each. Inspect actual pass count and positions as well as latency. Not acceptance data.
Control: corpus seed810114 (15 new snapshots plus6 known edge cases), fixed3repeats; first10legal nonterminal positions2paired repeats at10s. Threshold/depth/cut/skipped recorded; same completed result requires same move.
Acceptance: lower redundant pass/position count where certified; no fixed-score drift; investigate latency regression; report general and saturation-specific effects separately. Do not promise universal reduction if only certain positions benefit.
Games if candidate passes:20predeclared seeds2026110320..39 per arm at10ms, max6000moves; all40games regardless outcomes. Exploratory only, no default10s or phone win-rate claim.
Fullunit including7new certification tests, randomized800scores/200masks, exact fixed-depth parity, native/recorded-frame smoke; read-only independent review.
