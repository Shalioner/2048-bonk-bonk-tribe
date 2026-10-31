#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <thread>
#include <vector>

#include "2048.h"

#include "config.h"
#if defined(HAVE_UNORDERED_MAP)
    #include <unordered_map>
    typedef std::unordered_map<board_t, trans_table_entry_t> trans_table_t;
#elif defined(HAVE_TR1_UNORDERED_MAP)
    #include <tr1/unordered_map>
    typedef std::tr1::unordered_map<board_t, trans_table_entry_t> trans_table_t;
#else
    #include <map>
    typedef std::map<board_t, trans_table_entry_t> trans_table_t;
#endif

/* MSVC compatibility: undefine max and min macros */
#if defined(max)
#undef max
#endif

#if defined(min)
#undef min
#endif

// Transpose rows/columns in a board:
//   0123       048c
//   4567  -->  159d
//   89ab       26ae
//   cdef       37bf
static inline board_t transpose(board_t x)
{
    board_t a1 = x & 0xF0F00F0FF0F00F0FULL;
    board_t a2 = x & 0x0000F0F00000F0F0ULL;
    board_t a3 = x & 0x0F0F00000F0F0000ULL;
    board_t a = a1 | (a2 << 12) | (a3 >> 12);
    board_t b1 = a & 0xFF00FF0000FF00FFULL;
    board_t b2 = a & 0x00FF00FF00000000ULL;
    board_t b3 = a & 0x00000000FF00FF00ULL;
    return b1 | (b2 >> 24) | (b3 << 24);
}

// Count the number of empty positions (= zero nibbles) in a board.
// Precondition: the board cannot be fully empty.
static int count_empty(board_t x)
{
    x |= (x >> 2) & 0x3333333333333333ULL;
    x |= (x >> 1);
    x = ~x & 0x1111111111111111ULL;
    // At this point each nibble is:
    //  0 if the original nibble was non-zero
    //  1 if the original nibble was zero
    // Next sum them all
    x += x >> 32;
    x += x >> 16;
    x += x >>  8;
    x += x >>  4; // this can overflow to the next nibble if there were 16 empty positions
    return x & 0xf;
}

/* We can perform state lookups one row at a time by using arrays with 65536 entries. */

/* Move tables. Each row or compressed column is mapped to (oldrow^newrow) assuming row/col 0.
 *
 * Thus, the value is 0 if there is no move, and otherwise equals a value that can easily be
 * xor'ed into the current board state to update the board. */
static row_t row_left_table [65536];
static row_t row_right_table[65536];
static board_t col_up_table[65536];
static board_t col_down_table[65536];
static float heur_score_table[65536];
static float score_table[65536];

// Rules of the "Звёздное провидение" merge mode: tiles of CAP_RANK (2048 in classic
// terms) never merge with each other; the goal is GOAL_COUNT such tiles at once.
static const int CAP_RANK = 11;
static const int GOAL_COUNT = 3;

// Spawn model: spawn_table[max rank on the board after the move][rank-1] = probability.
// Filled from Python (set_spawn_table); classic 90/10 until then.
static float spawn_table[16][15];
static bool spawn_table_set = false;

// Heuristic scoring settings
static const float SCORE_LOST_PENALTY = 200000.0f;
static const float SCORE_MONOTONICITY_POWER = 4.0f;
static const float SCORE_MONOTONICITY_WEIGHT = 47.0f;
static const float SCORE_SUM_POWER = 3.5f;
static const float SCORE_SUM_WEIGHT = 11.0f;
static const float SCORE_MERGES_WEIGHT = 700.0f;
static const float SCORE_EMPTY_WEIGHT = 270.0f;

void init_tables() {
    if (!spawn_table_set) {
        for (int s = 0; s < 16; ++s) {
            for (int r = 0; r < 15; ++r) spawn_table[s][r] = 0.0f;
            spawn_table[s][0] = 0.9f;
            spawn_table[s][1] = 0.1f;
        }
    }
    for (unsigned row = 0; row < 65536; ++row) {
        unsigned line[4] = {
                (row >>  0) & 0xf,
                (row >>  4) & 0xf,
                (row >>  8) & 0xf,
                (row >> 12) & 0xf
        };

        // Score
        float score = 0.0f;
        for (int i = 0; i < 4; ++i) {
            int rank = line[i];
            if (rank >= 2) {
                // the score is the total sum of the tile and all intermediate merged tiles
                score += (rank - 1) * (1 << rank);
            }
        }
        score_table[row] = score;


        // Heuristic score
        float sum = 0;
        int empty = 0;
        int merges = 0;

        int prev = 0;
        int counter = 0;
        for (int i = 0; i < 4; ++i) {
            int rank = line[i];
            sum += pow(rank, SCORE_SUM_POWER);
            if (rank == 0) {
                empty++;
            } else {
                if (prev == rank && rank != CAP_RANK) { // CAP_RANK pairs never merge
                    counter++;
                } else if (counter > 0) {
                    merges += 1 + counter;
                    counter = 0;
                }
                prev = rank;
            }
        }
        if (counter > 0) {
            merges += 1 + counter;
        }

        float monotonicity_left = 0;
        float monotonicity_right = 0;
        for (int i = 1; i < 4; ++i) {
            if (line[i-1] > line[i]) {
                monotonicity_left += pow(line[i-1], SCORE_MONOTONICITY_POWER) - pow(line[i], SCORE_MONOTONICITY_POWER);
            } else {
                monotonicity_right += pow(line[i], SCORE_MONOTONICITY_POWER) - pow(line[i-1], SCORE_MONOTONICITY_POWER);
            }
        }

        heur_score_table[row] = SCORE_LOST_PENALTY +
            SCORE_EMPTY_WEIGHT * empty +
            SCORE_MERGES_WEIGHT * merges -
            SCORE_MONOTONICITY_WEIGHT * std::min(monotonicity_left, monotonicity_right) -
            SCORE_SUM_WEIGHT * sum;

        // execute a move to the left
        for (int i = 0; i < 3; ++i) {
            int j;
            for (j = i + 1; j < 4; ++j) {
                if (line[j] != 0) break;
            }
            if (j == 4) break; // no more tiles to the right

            if (line[i] == 0) {
                line[i] = line[j];
                line[j] = 0;
                i--; // retry this entry
            } else if (line[i] == line[j] && line[i] != (unsigned)CAP_RANK) {
                if(line[i] != 0xf) {
                    /* Pretend that 32768 + 32768 = 32768 (representational limit). */
                    line[i]++;
                }
                line[j] = 0;
            }
        }

        row_t result = (line[0] <<  0) |
                       (line[1] <<  4) |
                       (line[2] <<  8) |
                       (line[3] << 12);
        row_t rev_result = reverse_row(result);
        unsigned rev_row = reverse_row(row);

        row_left_table [    row] =                row  ^                result;
        row_right_table[rev_row] =            rev_row  ^            rev_result;
        col_up_table   [    row] = unpack_col(    row) ^ unpack_col(    result);
        col_down_table [rev_row] = unpack_col(rev_row) ^ unpack_col(rev_result);
    }
}

static inline board_t execute_move_0(board_t board) {
    board_t ret = board;
    board_t t = transpose(board);
    ret ^= col_up_table[(t >>  0) & ROW_MASK] <<  0;
    ret ^= col_up_table[(t >> 16) & ROW_MASK] <<  4;
    ret ^= col_up_table[(t >> 32) & ROW_MASK] <<  8;
    ret ^= col_up_table[(t >> 48) & ROW_MASK] << 12;
    return ret;
}

static inline board_t execute_move_1(board_t board) {
    board_t ret = board;
    board_t t = transpose(board);
    ret ^= col_down_table[(t >>  0) & ROW_MASK] <<  0;
    ret ^= col_down_table[(t >> 16) & ROW_MASK] <<  4;
    ret ^= col_down_table[(t >> 32) & ROW_MASK] <<  8;
    ret ^= col_down_table[(t >> 48) & ROW_MASK] << 12;
    return ret;
}

static inline board_t execute_move_2(board_t board) {
    board_t ret = board;
    ret ^= board_t(row_left_table[(board >>  0) & ROW_MASK]) <<  0;
    ret ^= board_t(row_left_table[(board >> 16) & ROW_MASK]) << 16;
    ret ^= board_t(row_left_table[(board >> 32) & ROW_MASK]) << 32;
    ret ^= board_t(row_left_table[(board >> 48) & ROW_MASK]) << 48;
    return ret;
}

static inline board_t execute_move_3(board_t board) {
    board_t ret = board;
    ret ^= board_t(row_right_table[(board >>  0) & ROW_MASK]) <<  0;
    ret ^= board_t(row_right_table[(board >> 16) & ROW_MASK]) << 16;
    ret ^= board_t(row_right_table[(board >> 32) & ROW_MASK]) << 32;
    ret ^= board_t(row_right_table[(board >> 48) & ROW_MASK]) << 48;
    return ret;
}

/* Execute a move. */
board_t execute_move(int move, board_t board) {
    switch(move) {
    case 0: // up
        return execute_move_0(board);
    case 1: // down
        return execute_move_1(board);
    case 2: // left
        return execute_move_2(board);
    case 3: // right
        return execute_move_3(board);
    default:
        return ~0ULL;
    }
}

static inline int get_max_rank(board_t board) {
    int maxrank = 0;
    while (board) {
        maxrank = std::max(maxrank, int(board & 0xf));
        board >>= 4;
    }
    return maxrank;
}

static inline int count_distinct_tiles(board_t board) {
    uint16_t bitset = 0;
    while (board) {
        bitset |= 1<<(board & 0xf);
        board >>= 4;
    }

    // Don't count empty tiles.
    bitset >>= 1;

    int count = 0;
    while (bitset) {
        bitset &= bitset - 1;
        count++;
    }
    return count;
}

/* Optimizing the game */

struct eval_state {
    trans_table_t trans_table; // transposition table, to cache previously-seen moves
    int maxdepth;
    int curdepth;
    int cachehits;
    unsigned long moves_evaled;
    int depth_limit;
    float cprob_thresh;              // branches with cumulative probability below this are cut
    bool has_deadline;               // abort the search once deadline has passed
    std::chrono::steady_clock::time_point deadline;
    std::atomic<bool> *stop;         // shared between the threads of one pass
    bool aborted;
    unsigned long checks;

    eval_state() : maxdepth(0), curdepth(0), cachehits(0), moves_evaled(0), depth_limit(0),
                   cprob_thresh(0.0001f), has_deadline(false), stop(NULL), aborted(false), checks(0) {
    }
};

// score a single board heuristically
static float score_heur_board(board_t board);
// score a single board actually (adding in the score from spawned 4 tiles)
static float score_board(board_t board);
// score over all possible moves
static float score_move_node(eval_state &state, board_t board, float cprob);
// score over all possible tile choices and placements
static float score_tilechoose_node(eval_state &state, board_t board, float cprob);


static float score_helper(board_t board, const float* table) {
    return table[(board >>  0) & ROW_MASK] +
           table[(board >> 16) & ROW_MASK] +
           table[(board >> 32) & ROW_MASK] +
           table[(board >> 48) & ROW_MASK];
}

static int count_cap(board_t b) { int n=0; for(int i=0;i<16;i++,b>>=4) n+=(int)(b&15)==CAP_RANK; return n; }
static bool goal(board_t b) { return count_cap(b)>=GOAL_COUNT; }
static float score_heur_board(board_t board) {
    if(goal(board)) return 1e9f;
    return score_helper(          board , heur_score_table) +
           score_helper(transpose(board), heur_score_table);
}

static float score_board(board_t board) {
    return score_helper(board, score_table);
}

// Statistics and controls
// cprob: cumulative probability
// don't recurse into a node with a cprob less than state.cprob_thresh (default 1e-4)
static const int CACHE_DEPTH_LIMIT  = 15;

static float score_tilechoose_node(eval_state &state, board_t board, float cprob) {
    if(goal(board)) return 1e9f;
    if (state.has_deadline && !state.aborted && (++state.checks & 0xfff) == 0) {
        if ((state.stop && state.stop->load()) || std::chrono::steady_clock::now() >= state.deadline) {
            state.aborted = true;
            if (state.stop) state.stop->store(true);
        }
    }
    if (state.aborted) return 0.0f; // result of an aborted pass is discarded
    if (cprob < state.cprob_thresh || state.curdepth >= state.depth_limit) {
        state.maxdepth = std::max(state.curdepth, state.maxdepth);
        return score_heur_board(board);
    }
    if (state.curdepth < CACHE_DEPTH_LIMIT) {
        const trans_table_t::iterator &i = state.trans_table.find(board);
        if (i != state.trans_table.end()) {
            trans_table_entry_t entry = i->second;
            /*
            return heuristic from transposition table only if it means that
            the node will have been evaluated to a minimum depth of state.depth_limit.
            This will result in slightly fewer cache hits, but should not impact the
            strength of the ai negatively.
            */
            if(entry.depth <= state.curdepth)
            {
                state.cachehits++;
                return entry.heuristic;
            }
        }
    }

    int num_open = count_empty(board);
    cprob /= num_open;

    float res = 0.0f;
    board_t tmp = board;
    board_t tile_2 = 1;
    const float *spawn_probs = spawn_table[get_max_rank(board)];
    while (tile_2) {
        if ((tmp & 0xf) == 0) {
            for(int rank=1;rank<=15;rank++) {
                float p=spawn_probs[rank-1];
                if(p>0) res += score_move_node(state, board | (tile_2*rank), cprob*p)*p;
            }
        }
        tmp >>= 4;
        tile_2 <<= 4;
    }
    res = res / num_open;

    if (state.curdepth < CACHE_DEPTH_LIMIT) {
        trans_table_entry_t entry = {static_cast<uint8_t>(state.curdepth), res};
        state.trans_table[board] = entry;
    }

    return res;
}

static float score_move_node(eval_state &state, board_t board, float cprob) {
    float best = 0.0f;
    state.curdepth++;
    for (int move = 0; move < 4; ++move) {
        board_t newboard = execute_move(move, board);
        state.moves_evaled++;

        if (board != newboard) {
            best = std::max(best, score_tilechoose_node(state, newboard, cprob));
        }
    }
    state.curdepth--;

    return best;
}

static float _score_toplevel_move(eval_state &state, board_t board, int move) {
    //int maxrank = get_max_rank(board);
    board_t newboard = execute_move(move, board);

    if(board == newboard)
        return 0;

    return score_tilechoose_node(state, newboard, 1.0f) + 1e-6;
}

float score_toplevel_move(board_t board, int move) {
    float res;
    struct timeval start, finish;
    double elapsed;
    eval_state state;
    state.depth_limit = std::max(3, count_distinct_tiles(board) - 2);

    gettimeofday(&start, NULL);
    res = _score_toplevel_move(state, board, move);
    gettimeofday(&finish, NULL);

    elapsed = (finish.tv_sec - start.tv_sec);
    elapsed += (finish.tv_usec - start.tv_usec) / 1000000.0;

    printf("Move %d: result %f: eval'd %ld moves (%d cache hits, %d cache size) in %.2f seconds (maxdepth=%d)\n", move, res,
        state.moves_evaled, state.cachehits, (int)state.trans_table.size(), elapsed, state.maxdepth);

    return res;
}

/* Find the best move for a given board. */
int find_best_move(board_t board) {
    int move;
    float best = 0;
    int bestmove = -1;

    print_board(board);
    printf("Current scores: heur %.0f, actual %.0f\n", score_heur_board(board), score_board(board));

    for(move=0; move<4; move++) {
        float res = score_toplevel_move(board, move);

        if(res > best) {
            best = res;
            bestmove = move;
        }
    }

    return bestmove;
}

int ask_for_move(board_t board) {
    int move;
    char validstr[5];
    char *validpos = validstr;

    print_board(board);

    for(move=0; move<4; move++) {
        if(execute_move(move, board) != board)
            *validpos++ = "UDLR"[move];
    }
    *validpos = 0;
    if(validpos == validstr)
        return -1;

    while(1) {
        char movestr[64];
        const char *allmoves = "UDLR";

        printf("Move [%s]? ", validstr);

        if(!fgets(movestr, sizeof(movestr)-1, stdin))
            return -1;

        if(!strchr(validstr, toupper(movestr[0]))) {
            printf("Invalid move.\n");
            continue;
        }

        return strchr(allmoves, toupper(movestr[0])) - allmoves;
    }
}

/* Playing the game */
static board_t draw_tile() {
    return (unif_random(10) < 9) ? 1 : 2;
}

static board_t insert_tile_rand(board_t board, board_t tile) {
    int index = unif_random(count_empty(board));
    board_t tmp = board;
    while (true) {
        while ((tmp & 0xf) != 0) {
            tmp >>= 4;
            tile <<= 4;
        }
        if (index == 0) break;
        --index;
        tmp >>= 4;
        tile <<= 4;
    }
    return board | tile;
}

static board_t initial_board() {
    board_t board = draw_tile() << (4 * unif_random(16));
    return insert_tile_rand(board, draw_tile());
}

void play_game(get_move_func_t get_move) {
    board_t board = initial_board();
    int moveno = 0;
    int scorepenalty = 0; // "penalty" for obtaining free 4 tiles

    while(1) {
        int move;
        board_t newboard;

        for(move = 0; move < 4; move++) {
            if(execute_move(move, board) != board)
                break;
        }
        if(move == 4)
            break; // no legal moves

        printf("\nMove #%d, current score=%.0f\n", ++moveno, score_board(board) - scorepenalty);

        move = get_move(board);
        if(move < 0)
            break;

        newboard = execute_move(move, board);
        if(newboard == board) {
            printf("Illegal move!\n");
            moveno--;
            continue;
        }

        board_t tile = draw_tile();
        if (tile == 2) scorepenalty += 4;
        board = insert_tile_rand(newboard, tile);
    }

    print_board(board);
    printf("\nGame over. Your score is %.0f. The highest rank you achieved was %d.\n", score_board(board) - scorepenalty, get_max_rank(board));
}

int upstream_main() {
    init_tables();
    play_game(find_best_move);
    return 0;
}

// Bumped whenever the Python side needs new exports or changed semantics.
extern "C" int engine_api_version() { return 2; }
extern "C" int get_cap_rank() { return CAP_RANK; }
extern "C" int get_goal_count() { return GOAL_COUNT; }
extern "C" int goal_levels(board_t board) { return goal(board); }

// Fixed-depth search with the default pruning threshold (single thread).
extern "C" int solve_levels(board_t board, int allowed, int depth) {
    float best=-1; int result=-1;
    for(int d=0;d<4;d++) if(allowed&(1<<d)) {
        eval_state state; state.depth_limit=depth;
        float score=_score_toplevel_move(state,board,d);
        if(score>best) {best=score;result=d;}
    }
    return result;
}

extern "C" float score_levels(board_t board,int move,int depth) {
    eval_state state;state.depth_limit=depth;
    return _score_toplevel_move(state,board,move);
}

extern "C" float score_levels_thresh(board_t board,int move,int depth,float thresh) {
    eval_state state;state.depth_limit=depth;state.cprob_thresh=thresh;
    return _score_toplevel_move(state,board,move);
}

/* Time-budgeted search with a hard deadline.
 * 1. Fallback: depth 1 with thresholds[0], sequential, no deadline (microseconds). It only
 *    guarantees that a move exists if no full pass finishes in time.
 * 2. Full passes with decreasing pruning thresholds (thresholds[0] > thresholds[1] > ...),
 *    the allowed first moves evaluated in parallel threads. Every full pass, including the
 *    first, is aborted at start+budget_s-margin and then discarded. A pass after the first
 *    is not started when previous_pass_time*growth would not fit before the deadline.
 * 3. The move of the last completed full pass is returned (the fallback move if none).
 * margin = min(1 s, 10% of the budget) leaves time to free the transposition tables of an
 * aborted pass (measured up to ~0.6 s for 10-second passes).
 * info[0] completed full passes, [1] threshold of the last completed full pass (0 if none),
 * [2] positions evaluated (all passes), [3] total seconds, [4] 1 if a pass was cut by the
 * deadline, [5] 1 if a pass was skipped by the estimate, [6] seconds of the last completed
 * full pass, [7] 1 if the returned move comes from the fallback. */
extern "C" int solve_budget(board_t board, int allowed, int depth, const float *thresholds,
                            int n_thresholds, double budget_s, double growth, double *info) {
    typedef std::chrono::steady_clock clock_t_;
    clock_t_::time_point start = clock_t_::now();
    budget_s = std::max(0.0, std::min(budget_s, 1e6));
    double margin = std::min(1.0, 0.1 * budget_s);
    clock_t_::time_point deadline = start + std::chrono::duration_cast<clock_t_::duration>(
        std::chrono::duration<double>(budget_s - margin));
    int best_move = -1;
    double positions = 0, last_pass = 0, last_thresh = 0;
    int completed = 0, cut = 0, skipped = 0;

    float fallback_best = -1;
    for (int d = 0; d < 4; ++d) {
        if (!(allowed & (1 << d))) continue;
        eval_state st;
        st.depth_limit = 1;
        st.cprob_thresh = n_thresholds > 0 ? thresholds[0] : 0.0001f;
        float score = _score_toplevel_move(st, board, d);
        positions += st.moves_evaled;
        if (score > fallback_best) { fallback_best = score; best_move = d; }
    }

    for (int k = 0; k < n_thresholds; ++k) {
        clock_t_::time_point pass_start = clock_t_::now();
        double elapsed = std::chrono::duration<double>(pass_start - start).count();
        if (pass_start >= deadline || (k > 0 && elapsed + last_pass * growth > budget_s - margin)) {
            skipped = 1;
            break;
        }
        std::atomic<bool> stop(false);
        float scores[4] = {0, 0, 0, 0};
        unsigned long evaled[4] = {0, 0, 0, 0};
        bool aborted[4] = {false, false, false, false};
        std::vector<std::thread> workers;
        for (int d = 0; d < 4; ++d) {
            if (!(allowed & (1 << d))) continue;
            workers.push_back(std::thread([&, d, k]() {
                eval_state st;
                st.depth_limit = depth;
                st.cprob_thresh = thresholds[k];
                st.has_deadline = true;
                st.deadline = deadline;
                st.stop = &stop;
                scores[d] = _score_toplevel_move(st, board, d);
                evaled[d] = st.moves_evaled;
                aborted[d] = st.aborted;
            }));
        }
        for (size_t w = 0; w < workers.size(); ++w) workers[w].join();
        bool any_abort = false;
        for (int d = 0; d < 4; ++d) { positions += evaled[d]; any_abort = any_abort || aborted[d]; }
        if (any_abort) { cut = 1; break; }
        float best = -1;
        int move = -1;
        for (int d = 0; d < 4; ++d)
            if ((allowed & (1 << d)) && scores[d] > best) { best = scores[d]; move = d; }
        best_move = move;
        completed++;
        last_thresh = thresholds[k];
        last_pass = std::chrono::duration<double>(clock_t_::now() - pass_start).count();
    }
    if (info) {
        info[0] = completed; info[1] = last_thresh; info[2] = positions;
        info[3] = std::chrono::duration<double>(clock_t_::now() - start).count();
        info[4] = cut; info[5] = skipped; info[6] = last_pass; info[7] = completed == 0;
    }
    return best_move;
}

// probs: stages x levels row-major; row s is used when the maximum rank after the move is s.
// Rows are normalized. Returns 0 on success, -1 on invalid input (table unchanged).
extern "C" int set_spawn_table(const float *probs, int stages, int levels) {
    if (stages != 16 || levels < 1 || levels > 15) return -1;
    float tmp[16][15];
    for (int s = 0; s < 16; ++s) {
        float sum = 0;
        for (int r = 0; r < levels; ++r) { if (probs[s*levels+r] < 0) return -1; sum += probs[s*levels+r]; }
        if (sum <= 0) return -1;
        for (int r = 0; r < 15; ++r) tmp[s][r] = r < levels ? probs[s*levels+r] / sum : 0.0f;
    }
    memcpy(spawn_table, tmp, sizeof(tmp));
    spawn_table_set = true;
    return 0;
}

extern "C" float get_spawn_prob(int stage, int level) {
    if (stage < 0 || stage > 15 || level < 1 || level > 15) return -1;
    return spawn_table[stage][level-1];
}

// Same vector for every stage (kept for tests and experiments).
extern "C" void set_spawn_vector(const float *weights,int count) {
    if(count<1 || count>15)return;
    float table[16*15];
    for(int s=0;s<16;s++) for(int r=0;r<15;r++) table[s*15+r] = r<count?weights[r]:0;
    set_spawn_table(table,16,15);
}

extern "C" void set_spawn_probs(float p1,float p2,float p3) {
    float w[3]={p1,p2,p3};
    set_spawn_vector(w,3);
}
