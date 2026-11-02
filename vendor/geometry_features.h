/* Structural leaf-score experiments for the local Bonk adaptation of 2048-ai.
 * Row monotonicity calculation is derived from nneonneo/2048-ai; packed-board
 * conventions and move tables belong to that project. New segmentation,
 * non-CAP adjacency and one-spawn risk helpers are local experimental additions.
 * Include after 2048.h; configuration/spawn probabilities stay fixed per search.
 *
 * MIT License
 * Copyright (c) 2014-2019 Robert Xiao <nneonneo@gmail.com> and contributors.
 * Copyright (c) 2026 Bonk local adaptation contributors.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
#ifndef BONK_GEOMETRY_FEATURES_H
#define BONK_GEOMETRY_FEATURES_H

#include <algorithm>
#include <cmath>
#include <stdint.h>

namespace bonk_geometry {

static const unsigned barrier_rank = 11;

// CAP ranks retain their values everywhere. Only the monotonicity chain is cut.
static inline float segmented_penalty(const unsigned line[4], float power,
                                      float original_left, float original_right,
                                      float strength) {
    const float original = std::min(original_left, original_right);
    bool barrier = false;
    for (int i = 0; i < 4; ++i) barrier = barrier || line[i] >= barrier_rank;
    if (!barrier) return original; // Preserve every old non-CAP row bit-for-bit.
    float total = 0.0f, left = 0.0f, right = 0.0f;
    for (int i = 0; i < 4; ++i) {
        if (line[i] >= barrier_rank) {
            total += std::min(left, right);
            left = right = 0.0f;
        } else if (i > 0 && line[i-1] < barrier_rank) {
            if (line[i-1] > line[i])
                left += std::pow(line[i-1], power) - std::pow(line[i], power);
            else
                right += std::pow(line[i], power) - std::pow(line[i-1], power);
        }
    }
    total += std::min(left, right);
    return (1.0f - strength) * original + strength * total;
}

static unsigned char free_edges_table[65536];
static unsigned char max_edges_table[17];
static bool geometry_ready = false;

// Initialized before search threads, as are the engine's move/heuristic tables.
static inline void init_geometry_tables() {
    if (geometry_ready) return;
    for (unsigned mask = 0; mask < 65536; ++mask) {
        unsigned edges = 0;
        for (unsigned row = 0; row < 4; ++row) {
            for (unsigned col = 0; col < 4; ++col) {
                const unsigned bit = 1u << (row * 4 + col);
                if (mask & bit) continue;
                if (col < 3 && !(mask & (bit << 1))) ++edges;
                if (row < 3 && !(mask & (bit << 4))) ++edges;
            }
        }
        const unsigned caps = __builtin_popcount(mask);
        free_edges_table[mask] = static_cast<unsigned char>(edges);
        max_edges_table[caps] = std::max(max_edges_table[caps], static_cast<unsigned char>(edges));
    }
    geometry_ready = true;
}

static inline unsigned cap_mask(board_t board) {
    // v>=11 iff v3 && (v2 || (v1 && v0)); isolate one low bit per nibble.
    board_t wide = (board >> 3) & ((board >> 2) | ((board >> 1) & board)) & 0x1111111111111111ULL;
    unsigned mask = 0;
    while (wide) {
        mask |= 1u << (__builtin_ctzll(wide) / 4);
        wide &= wide - 1;
    }
    return mask;
}

static inline int geometry_penalty(board_t board) {
    const unsigned mask = cap_mask(board);
    return static_cast<int>(free_edges_table[mask]) - max_edges_table[__builtin_popcount(mask)];
}

static inline int exact_cap_count(board_t board) {
    int count = 0;
    for (int cell = 0; cell < 16; ++cell, board >>= 4) count += (board & 15) == 11;
    return count;
}

typedef board_t (*move_function)(int, board_t);

// Exact one-spawn loss probability under the supplied current-stage distribution.
// No legal spawn cell => no spawn event => risk 0; no new terminal rule is added.
// With >=2 empty cells, one remains after spawning. The connected grid has a
// zero/nonzero boundary, so a movable tile can slide into a zero: loss risk 0.
static inline float immediate_loss_risk(board_t board, const float probabilities[15]) {
    if (exact_cap_count(board) != 2) return 0.0f;
    int empty = 0, free_cell = -1;
    for (int cell = 0; cell < 16; ++cell) {
        if (((board >> (4 * cell)) & 15) == 0) { ++empty; free_cell = cell; }
    }
    if (empty != 1) return 0.0f;
    // A full child moves iff an adjacent equal pair with rank<11 can merge.
    // An existing occupied pair survives every spawn, so every child is legal.
    for (int cell = 0; cell < 16; ++cell) {
        const unsigned rank = (board >> (4 * cell)) & 15;
        if (rank == 0 || rank >= barrier_rank) continue;
        if (cell % 4 < 3 && ((board >> (4 * (cell+1))) & 15) == rank) return 0.0f;
        if (cell < 12 && ((board >> (4 * (cell+4))) & 15) == rank) return 0.0f;
    }
    // Otherwise a merge can only be created next to the unique spawn cell.
    unsigned safe = 1u << 11; // The third exact11 is a goal, never a loss.
    const int neighbors[4] = {free_cell % 4 ? free_cell-1 : -1, free_cell % 4 < 3 ? free_cell+1 : -1,
                              free_cell >= 4 ? free_cell-4 : -1, free_cell < 12 ? free_cell+4 : -1};
    for (int i = 0; i < 4; ++i) {
        if (neighbors[i] < 0) continue;
        const unsigned rank = (board >> (4 * neighbors[i])) & 15;
        if (rank > 0 && rank < barrier_rank) safe |= 1u << rank;
    }
    double probability = 0.0;
    for (int rank = 1; rank <= 15; ++rank)
        if (!(safe & (1u << rank)) && probabilities[rank-1] > 0.0f) probability += probabilities[rank-1];
    // The source is normalized float probabilities: clamp only their roundoff.
    return static_cast<float>(std::max(0.0, std::min(1.0, probability)));
}

static inline float risk_adjusted(board_t board, float raw, float strength,
                                 const float probabilities[15]) {
    if (raw < 0.0f) return raw; // Preserve the pre-existing signed-max behavior.
    return raw * (1.0f - strength * immediate_loss_risk(board, probabilities));
}

} // namespace bonk_geometry
#endif
