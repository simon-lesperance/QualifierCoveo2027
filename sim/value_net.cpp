#include "value_net.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>

#include "features.hpp"

namespace sim {

namespace {

constexpr int kSize1 = 10;  // Grid side after the first convolution.
constexpr int kSize2 = 5;   // After the second.

bool read_floats(std::FILE* file, float* out, size_t count) {
    return std::fread(out, sizeof(float), count, file) == count;
}

}  // namespace

bool ValueNet::load(const char* path) {
    std::FILE* file = std::fopen(path, "rb");
    if (file == nullptr) return false;

    int32_t sizes[3];
    bool ok = std::fread(sizes, sizeof(int32_t), 3, file) == 3;
    if (ok) {
        first_ = sizes[0];
        second_ = sizes[1];
        hidden_ = sizes[2];
        flat_ = second_ * kSize2 * kSize2 + kGlobals;

        // PyTorch stores convolutions as [out][in][kernel row][kernel column]. Both are
        // reordered so the out index is innermost, which makes each update one vector.
        std::vector<float> raw(first_ * kChannels * 9);
        ok = read_floats(file, raw.data(), raw.size());
        conv1_weights_.resize(raw.size());
        for (int out = 0; ok && out < first_; out++) {
            for (int in = 0; in < kChannels; in++) {
                for (int kernel = 0; kernel < 9; kernel++) {
                    conv1_weights_[(in * 9 + kernel) * first_ + out] = raw[(out * kChannels + in) * 9 + kernel];
                }
            }
        }
        conv1_bias_.resize(first_);
        ok = ok && read_floats(file, conv1_bias_.data(), first_);

        raw.resize(second_ * first_ * 9);
        ok = ok && read_floats(file, raw.data(), raw.size());
        conv2_weights_.resize(raw.size());
        for (int out = 0; ok && out < second_; out++) {
            for (int in = 0; in < first_; in++) {
                for (int kernel = 0; kernel < 9; kernel++) {
                    conv2_weights_[(kernel * first_ + in) * second_ + out] = raw[(out * first_ + in) * 9 + kernel];
                }
            }
        }
        conv2_bias_.resize(second_);
        ok = ok && read_floats(file, conv2_bias_.data(), second_);

        raw.resize(hidden_ * flat_);
        ok = ok && read_floats(file, raw.data(), raw.size());
        fc1_weights_.resize(raw.size());
        for (int out = 0; ok && out < hidden_; out++) {
            for (int in = 0; in < flat_; in++) fc1_weights_[in * hidden_ + out] = raw[out * flat_ + in];
        }
        fc1_bias_.resize(hidden_);
        ok = ok && read_floats(file, fc1_bias_.data(), hidden_);

        fc2_weights_.resize(hidden_);
        ok = ok && read_floats(file, fc2_weights_.data(), hidden_);
        ok = ok && read_floats(file, &fc2_bias_, 1);
    }
    std::fclose(file);
    return ok;
}

float ValueNet::evaluate(const uint8_t* features) const {
    // At most 64 of each, set by what the training script can export.
    float layer1[kSize1 * kSize1 * 64];
    float layer2[kSize2 * kSize2 * 64];
    float hidden[256];

    for (int i = 0; i < kSize1 * kSize1; i++) std::memcpy(layer1 + i * first_, conv1_bias_.data(), first_ * sizeof(float));

    // The input is mostly zeros, so only non-zero bytes do any work.
    for (int channel = 0; channel < kChannels; channel++) {
        const uint8_t* plane = features + channel * kCells;
        const float* channel_weights = conv1_weights_.data() + channel * 9 * first_;
        for (int cell = 0; cell < kCells; cell++) {
            if (plane[cell] == 0) continue;
            float value = plane[cell] / 255.0f;
            int row = cell / kWidth;
            int column = cell % kWidth;
            // An output at index i reads inputs 2i - 1 to 2i + 1.
            for (int out_row = (row + 1) / 2 - (row & 1 ? 1 : 0); out_row <= (row + 1) / 2; out_row++) {
                if (out_row >= kSize1) continue;
                int kernel_row = row - 2 * out_row + 1;
                for (int out_column = (column + 1) / 2 - (column & 1 ? 1 : 0); out_column <= (column + 1) / 2; out_column++) {
                    if (out_column >= kSize1) continue;
                    int kernel = kernel_row * 3 + column - 2 * out_column + 1;
                    const float* weights = channel_weights + kernel * first_;
                    float* target = layer1 + (out_row * kSize1 + out_column) * first_;
                    for (int i = 0; i < first_; i++) target[i] += weights[i] * value;
                }
            }
        }
    }
    for (int i = 0; i < kSize1 * kSize1 * first_; i++) layer1[i] = std::max(layer1[i], 0.0f);

    for (int row = 0; row < kSize2; row++) {
        for (int column = 0; column < kSize2; column++) {
            float* target = layer2 + (row * kSize2 + column) * second_;
            std::memcpy(target, conv2_bias_.data(), second_ * sizeof(float));
            for (int kernel_row = 0; kernel_row < 3; kernel_row++) {
                int in_row = 2 * row - 1 + kernel_row;
                if (in_row < 0 || in_row >= kSize1) continue;
                for (int kernel_column = 0; kernel_column < 3; kernel_column++) {
                    int in_column = 2 * column - 1 + kernel_column;
                    if (in_column < 0 || in_column >= kSize1) continue;
                    const float* source = layer1 + (in_row * kSize1 + in_column) * first_;
                    const float* weights = conv2_weights_.data() + (kernel_row * 3 + kernel_column) * first_ * second_;
                    for (int in = 0; in < first_; in++) {
                        float value = source[in];
                        if (value == 0.0f) continue;
                        const float* column_weights = weights + in * second_;
                        for (int i = 0; i < second_; i++) target[i] += column_weights[i] * value;
                    }
                }
            }
        }
    }

    std::memcpy(hidden, fc1_bias_.data(), hidden_ * sizeof(float));
    // PyTorch flattens as channel, row, column, while layer2 keeps channels innermost.
    for (int channel = 0; channel < second_; channel++) {
        for (int cell = 0; cell < kSize2 * kSize2; cell++) {
            float value = std::max(layer2[cell * second_ + channel], 0.0f);
            if (value == 0.0f) continue;
            const float* weights = fc1_weights_.data() + (channel * kSize2 * kSize2 + cell) * hidden_;
            for (int i = 0; i < hidden_; i++) hidden[i] += weights[i] * value;
        }
    }
    const uint8_t* globals = features + kChannels * kCells;
    const float* global_weights = fc1_weights_.data() + (second_ * kSize2 * kSize2) * hidden_;
    for (int g = 0; g < kGlobals; g++) {
        float value = globals[g] / 255.0f;
        for (int i = 0; i < hidden_; i++) hidden[i] += global_weights[g * hidden_ + i] * value;
    }

    float output = fc2_bias_;
    for (int i = 0; i < hidden_; i++) output += fc2_weights_[i] * std::max(hidden[i], 0.0f);
    return output;
}

}  // namespace sim
