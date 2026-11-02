#ifndef BONK_TD_MODEL_H
#define BONK_TD_MODEL_H

/* Adapted from moporgic/TDL2048 a99f620aec0d30a75943a4c9646743f1f53b0197,
2048.cpp isomorphic estimate/optimize 1510-1538 and state::instruct 1771-1776.
Changes: Bonk CAP11/GOAL3, four 4-tuples x D4, stages, clipped success value,
fixed-bias float32 updates, separate file format; no donor game engine.
MIT License

Copyright (c) 2021 Hung Guei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

*/
// load/clear/update outside search; const evaluate supports concurrent readers.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace bonk_td {

class Model {
public:
    Model() : ready_(false), bias_(3, 0.0f) {}

    bool loaded() const { return ready_; }
    const std::string &metadata_json() const { return metadata_; }

    void clear() {
        ready_ = false;
        std::vector<float>().swap(weights_);
        bias_.assign(3, 0.0f);
        metadata_.clear();
    }

    bool load(const std::string &path, std::string *error = NULL) {
        // Никакой смены действующих параметров до полной проверки нового файла.
        std::ifstream stream(path.c_str(), std::ios::binary | std::ios::ate);
        if (!stream) return fail(error, "Cannot open value model");
        const std::streamoff size = stream.tellg();
        if (size < static_cast<std::streamoff>(HEADER_SIZE) ||
                size > static_cast<std::streamoff>(HEADER_SIZE + MAX_METADATA + PAYLOAD_SIZE))
            return fail(error, "Invalid value model size");
        stream.seekg(0);
        std::vector<unsigned char> raw(static_cast<size_t>(size));
        if (!stream.read(reinterpret_cast<char *>(&raw[0]), size))
            return fail(error, "Cannot read value model");
        if (std::memcmp(&raw[0], "BONKTD01", 8) != 0 || read32(&raw[8]) != 1 ||
                read32(&raw[12]) != HEADER_SIZE || read32(&raw[20]) != 3 ||
                read32(&raw[24]) != 4 || read32(&raw[28]) != TABLE_SIZE ||
                read32(&raw[32]) != 11 || read32(&raw[36]) != 3 || read32(&raw[40]) != 2)
            return fail(error, "Incompatible value architecture, rules or target");
        const uint32_t metadata_size = read32(&raw[16]);
        if (metadata_size < 1 || metadata_size > MAX_METADATA ||
                static_cast<size_t>(size) != HEADER_SIZE + metadata_size + PAYLOAD_SIZE)
            return fail(error, "Invalid value payload size");
        if (crc32(&raw[HEADER_SIZE], raw.size() - HEADER_SIZE) != read32(&raw[44]))
            return fail(error, "Value CRC32 mismatch");
        if (sizeof(float) != 4 || !std::numeric_limits<float>::is_iec559)
            return fail(error, "IEEE754 float32 required");
        const unsigned char *payload = &raw[HEADER_SIZE + metadata_size];
        std::vector<float> new_bias(3), new_weights(WEIGHT_COUNT);
        for (size_t i = 0; i < 3 + WEIGHT_COUNT; ++i) {
            const uint32_t bits = read32(payload + 4 * i);
            float value;
            std::memcpy(&value, &bits, 4);
            if (!std::isfinite(value)) return fail(error, "Nonfinite value parameter");
            if (i < 3) new_bias[i] = value;
            else new_weights[i - 3] = value;
        }
        std::string new_metadata(reinterpret_cast<const char *>(&raw[HEADER_SIZE]), metadata_size);
        weights_.swap(new_weights);
        bias_.swap(new_bias);
        metadata_.swap(new_metadata);
        ready_ = true;
        if (error) error->clear();
        return true;
    }

    float evaluate(uint64_t packed) const {
        if (!ready_) return std::numeric_limits<float>::quiet_NaN();
        unsigned board[16];
        int caps = 0, occupied = 0;
        for (int i = 0; i < 16; ++i) {
            board[i] = static_cast<unsigned>((packed >> (4 * i)) & 15);
            caps += board[i] == 11;
            occupied += board[i] != 0;
        }
        if (caps >= 3) return 1.0f;
        if (occupied == 0) return 0.0f;
        if (occupied == 16) {
            bool legal = false;
            for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
                const unsigned value = board[4 * r + c];
                if (value < 11 && ((c < 3 && value == board[4 * r + c + 1]) ||
                                   (r < 3 && value == board[4 * (r + 1) + c]))) legal = true;
            }
            if (!legal) return 0.0f;
        }
        const int stage = caps < 2 ? caps : 2;
        // Предвычисленные D4-позиции: в каждом leaf только32 lookup.
        const unsigned (*cells)[8][4] = feature_cells();
        double total = 0;
        for (int pattern = 0; pattern < 4; ++pattern) {
            for (int symmetry = 0; symmetry < 8; ++symmetry) {
                const unsigned *cell = cells[pattern][symmetry];
                const unsigned index = board[cell[0]] | (board[cell[1]] << 4) |
                                       (board[cell[2]] << 8) | (board[cell[3]] << 12);
                total += weights_[(stage * 4 + pattern) * TABLE_SIZE + index];
            }
        }
        const float raw = static_cast<float>(static_cast<double>(bias_[stage]) + total / 8.0);
        return raw < 1e-6f ? 1e-6f : (raw > 1.0f - 1e-6f ? 1.0f - 1e-6f : raw);
    }

    static size_t parameter_count() { return 3 + WEIGHT_COUNT; }
    bool reset(float initial = 0.5f) {
        if (!std::isfinite(initial) || initial < 0 || initial > 1) return false;
        std::vector<float> fresh(WEIGHT_COUNT, 0.0f);
        weights_.swap(fresh); bias_.assign(3, initial); metadata_.clear(); ready_ = true;
        return true;
    }
    bool set_parameters(const float *values, size_t count) {
        if (!values || count != parameter_count()) return false;
        for (size_t i = 0; i < count; ++i) if (!std::isfinite(values[i])) return false;
        std::vector<float> new_bias(values, values + 3), new_weights(values + 3, values + count);
        bias_.swap(new_bias); weights_.swap(new_weights); metadata_.clear(); ready_ = true;
        return true;
    }
    bool get_parameters(float *values, size_t count) const {
        if (!ready_ || !values || count != parameter_count()) return false;
        std::copy(bias_.begin(), bias_.end(), values);
        std::copy(weights_.begin(), weights_.end(), values + 3);
        return true;
    }
    float update(uint64_t packed, float target, float alpha) {
        if (!ready_ || !std::isfinite(target) || target < 0 || target > 1 ||
                !std::isfinite(alpha) || alpha < 0 || alpha > 1)
            return std::numeric_limits<float>::quiet_NaN();
        if (terminal(packed)) return evaluate(packed);
        const float prediction = evaluate(packed);
        const float delta = target - prediction;
        const float error = alpha * delta;
        const float increment = error / 4.0f;
        unsigned board[16]; int caps = 0;
        for (int i = 0; i < 16; ++i) { board[i] = (packed >> (4 * i)) & 15; caps += board[i] == 11; }
        const int stage = caps < 2 ? caps : 2;
        const unsigned (*cells)[8][4] = feature_cells();
        // Donor isomorphic optimizer updates every occurrence, including aliases.
        for (int pattern = 0; pattern < 4; ++pattern) for (int symmetry = 0; symmetry < 8; ++symmetry) {
            const unsigned *c = cells[pattern][symmetry];
            const unsigned index = board[c[0]] | board[c[1]] << 4 | board[c[2]] << 8 | board[c[3]] << 12;
            weights_[(stage * 4 + pattern) * TABLE_SIZE + index] += increment;
        }
        return evaluate(packed);
    }

private:
    static bool terminal(uint64_t packed) {
        int caps = 0, occupied = 0; unsigned b[16];
        for (int i = 0; i < 16; ++i) { b[i] = (packed >> (4 * i)) & 15; caps += b[i] == 11; occupied += b[i] != 0; }
        if (caps >= 3 || occupied == 0) return true;
        if (occupied != 16) return false;
        for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
            const unsigned v = b[4*r+c];
            if (v < 11 && ((c < 3 && v == b[4*r+c+1]) || (r < 3 && v == b[4*(r+1)+c]))) return false;
        }
        return true;
    }
    static const unsigned (*feature_cells())[8][4] {
        static const unsigned value[4][8][4] = {
            {{0, 1, 2, 3}, {12, 8, 4, 0}, {15, 14, 13, 12}, {3, 7, 11, 15}, {3, 2, 1, 0}, {15, 11, 7, 3}, {12, 13, 14, 15}, {0, 4, 8, 12}},
            {{0, 1, 4, 5}, {12, 8, 13, 9}, {15, 14, 11, 10}, {3, 7, 2, 6}, {3, 2, 7, 6}, {15, 11, 14, 10}, {12, 13, 8, 9}, {0, 4, 1, 5}},
            {{0, 1, 2, 5}, {12, 8, 4, 9}, {15, 14, 13, 10}, {3, 7, 11, 6}, {3, 2, 1, 6}, {15, 11, 7, 10}, {12, 13, 14, 9}, {0, 4, 8, 5}},
            {{0, 1, 5, 6}, {12, 8, 9, 5}, {15, 14, 10, 9}, {3, 7, 6, 10}, {3, 2, 6, 5}, {15, 11, 10, 6}, {12, 13, 9, 10}, {0, 4, 5, 9}}
        };
        return value;
    }
    static const size_t TABLE_SIZE = 65536;
    static const size_t WEIGHT_COUNT = 3 * 4 * TABLE_SIZE;
    static const size_t HEADER_SIZE = 48;
    static const size_t MAX_METADATA = 1024 * 1024;
    static const size_t PAYLOAD_SIZE = 4 * (3 + WEIGHT_COUNT);
    bool ready_;
    std::vector<float> weights_, bias_;
    std::string metadata_;

    static bool fail(std::string *error, const char *message) {
        if (error) *error = message;
        return false;
    }

    static uint32_t read32(const unsigned char *p) {
        return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
               (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
    }

    static uint32_t crc32(const unsigned char *data, size_t length) {
        uint32_t result = 0xffffffffU;
        for (size_t i = 0; i < length; ++i) {
            result ^= data[i];
            for (int bit = 0; bit < 8; ++bit)
                result = (result >> 1) ^ (0xedb88320U & (0U - (result & 1U)));
        }
        return result ^ 0xffffffffU;
    }
};

}  // namespace bonk_td
#endif
