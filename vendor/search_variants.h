/* D4 bitboards for isolated Bonk search experiments.
 * Transpose derives from nneonneo/2048-ai; reflections/canonical choice are local.
 * Include after 2048.h. These helpers neither change moves nor the leaf score.
 *
 * MIT License
 * Copyright (c) 2014-2019 Robert Xiao <nneonneo@gmail.com> and contributors.
 * Copyright (c) 2026 Bonk local adaptation contributors.
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
#ifndef BONK_SEARCH_VARIANTS_H
#define BONK_SEARCH_VARIANTS_H

namespace bonk_search {

static inline board_t transpose(board_t x) {
    const board_t a1 = x & 0xF0F00F0FF0F00F0FULL;
    const board_t a2 = x & 0x0000F0F00000F0F0ULL;
    const board_t a3 = x & 0x0F0F00000F0F0000ULL;
    const board_t a = a1 | (a2 << 12) | (a3 >> 12);
    const board_t b1 = a & 0xFF00FF0000FF00FFULL;
    const board_t b2 = a & 0x00FF00FF00000000ULL;
    const board_t b3 = a & 0x00000000FF00FF00ULL;
    return b1 | (b2 >> 24) | (b3 << 24);
}

static inline board_t horizontal(board_t x) {
    return ((x & 0x000F000F000F000FULL) << 12) | ((x & 0x00F000F000F000F0ULL) << 4) |
           ((x & 0x0F000F000F000F00ULL) >> 4) | ((x & 0xF000F000F000F000ULL) >> 12);
}

static inline board_t vertical(board_t x) {
    return (x << 48) | ((x & 0x00000000FFFF0000ULL) << 16) |
           ((x & 0x0000FFFF00000000ULL) >> 16) | (x >> 48);
}

static inline board_t canonical(board_t x) {
    const board_t t = transpose(x);
    const board_t candidates[8] = {x, horizontal(x), vertical(x), horizontal(vertical(x)),
                                  t, horizontal(t), vertical(t), horizontal(vertical(t))};
    board_t best = x;
    for (int i = 1; i < 8; ++i) if (candidates[i] < best) best = candidates[i];
    return best;
}

} // namespace bonk_search
#endif
