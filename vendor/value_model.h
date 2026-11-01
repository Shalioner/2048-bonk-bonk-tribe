#ifndef BONK_VALUE_MODEL_H
#define BONK_VALUE_MODEL_H

// Приватный экспериментальный формат BONKVAL1: только CAP11/GOAL3 и MC-afterstate.
// load/clear вызываются координатором вне поиска. evaluate const не меняет память,
// поэтому один полностью загруженный экземпляр допускает параллельное чтение.
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace bonk_value {

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
        if (std::memcmp(&raw[0], "BONKVAL1", 8) != 0 || read32(&raw[8]) != 1 ||
                read32(&raw[12]) != HEADER_SIZE || read32(&raw[20]) != 3 ||
                read32(&raw[24]) != 4 || read32(&raw[28]) != TABLE_SIZE ||
                read32(&raw[32]) != 11 || read32(&raw[36]) != 3 || read32(&raw[40]) != 1)
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
        static const unsigned cells[4][8][4] = {
            {{0, 1, 2, 3}, {12, 8, 4, 0}, {15, 14, 13, 12}, {3, 7, 11, 15}, {3, 2, 1, 0}, {15, 11, 7, 3}, {12, 13, 14, 15}, {0, 4, 8, 12}},
            {{0, 1, 4, 5}, {12, 8, 13, 9}, {15, 14, 11, 10}, {3, 7, 2, 6}, {3, 2, 7, 6}, {15, 11, 14, 10}, {12, 13, 8, 9}, {0, 4, 1, 5}},
            {{0, 1, 2, 5}, {12, 8, 4, 9}, {15, 14, 13, 10}, {3, 7, 11, 6}, {3, 2, 1, 6}, {15, 11, 7, 10}, {12, 13, 14, 9}, {0, 4, 8, 5}},
            {{0, 1, 5, 6}, {12, 8, 9, 5}, {15, 14, 10, 9}, {3, 7, 6, 10}, {3, 2, 6, 5}, {15, 11, 10, 6}, {12, 13, 9, 10}, {0, 4, 5, 9}}
        };
        double total = 0;
        for (int pattern = 0; pattern < 4; ++pattern) {
            for (int symmetry = 0; symmetry < 8; ++symmetry) {
                const unsigned *cell = cells[pattern][symmetry];
                const unsigned index = board[cell[0]] | (board[cell[1]] << 4) |
                                       (board[cell[2]] << 8) | (board[cell[3]] << 12);
                total += weights_[(stage * 4 + pattern) * TABLE_SIZE + index];
            }
        }
        const double z = static_cast<double>(bias_[stage]) + total / 8.0;
        if (z >= 0) return static_cast<float>(1.0 / (1.0 + std::exp(-z)));
        const double e = std::exp(z);
        return static_cast<float>(e / (1.0 + e));
    }

private:
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

}  // namespace bonk_value
#endif
