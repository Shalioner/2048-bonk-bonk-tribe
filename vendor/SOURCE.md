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
