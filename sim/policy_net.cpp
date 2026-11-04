#include "policy_net.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace sim {

namespace {

constexpr int kBlocks = 6;
constexpr int kDilations[kBlocks] = {1, 1, 2, 2, 4, 1};
constexpr int kStegoRow = 13;
constexpr int kStegoCycle = 38;

#if defined(__x86_64__)
#define KERNEL __attribute__((target("avx2,fma")))
#else
#define KERNEL
#endif

// out[y, x] += sum over the 9 taps of weights[tap] * in[y + dy, x + dx], for one
// pair of channels. Padding is zero, so taps off the map are skipped.
KERNEL void convolve_pair(const float* in, const float* weights, int dilation, float* out) {
    for (int tap = 0; tap < 9; tap++) {
        int dy = (tap / 3 - 1) * dilation;
        int dx = (tap % 3 - 1) * dilation;
        float weight = weights[tap];
        int first_x = std::max(0, -dx);
        int last_x = std::min(kWidth, kWidth - dx);
        for (int y = std::max(0, -dy); y < std::min(kHeight, kHeight - dy); y++) {
            const float* source = in + (y + dy) * kWidth + dx;
            float* target = out + y * kWidth;
            for (int x = first_x; x < last_x; x++) target[x] += weight * source[x];
        }
    }
}

// weights is (out channels, in channels, 3, 3), then one bias per out channel.
void convolve(const float* in, int in_channels, const float* weights, int out_channels, int dilation, float* out) {
    std::memset(out, 0, sizeof(float) * out_channels * kCells);
    for (int o = 0; o < out_channels; o++) {
        for (int i = 0; i < in_channels; i++) {
            convolve_pair(in + i * kCells, weights + (o * in_channels + i) * 9, dilation, out + o * kCells);
        }
        float bias = weights[out_channels * in_channels * 9 + o];
        for (int cell = 0; cell < kCells; cell++) out[o * kCells + cell] += bias;
    }
}

}  // namespace

bool PolicyNet::load(const char* path) {
    std::FILE* file = std::fopen(path, "rb");
    if (file == nullptr) return false;
    int32_t header[2];
    if (std::fread(header, sizeof(int32_t), 2, file) != 2) {
        std::fclose(file);
        return false;
    }
    channels_ = header[0];
    hidden_ = header[1];
    size_t count = (channels_ * kPlanes * 9 + channels_) + kBlocks * (channels_ * channels_ * 9 + channels_) +
                   (2 * channels_ + 2) + (2 * channels_ * hidden_ + hidden_) + (hidden_ + 1);
    weights_.resize(count);
    bool complete = std::fread(weights_.data(), sizeof(float), count, file) == count;
    std::fclose(file);
    a_.resize(std::max(kPlanes, channels_) * kCells);
    b_.resize(std::max(kPlanes, channels_) * kCells);
    return complete;
}

void PolicyNet::evaluate(const uint8_t* features, int tick, float* logits) {
    float* x = a_.data();
    std::memset(x, 0, kPlanes * kCells * sizeof(float));

    for (int i = 0; i < kChannels * kCells; i++) x[i] = features[i] / 255.0f;
    int phase = tick % kStegoCycle;
    int stego_column = phase < kWidth ? phase : kStegoCycle - phase;
    x[kChannels * kCells + kStegoRow * kWidth + std::min(stego_column, kWidth - 1)] = 1;

    float flat[kGlobals + 4];
    for (int i = 0; i < kGlobals; i++) flat[i] = features[kChannels * kCells + i] / 255.0f;
    double angle = 2 * 3.14159265358979323846 * tick;
    flat[kGlobals] = static_cast<float>(std::sin(angle / 5));
    flat[kGlobals + 1] = static_cast<float>(std::cos(angle / 5));
    flat[kGlobals + 2] = static_cast<float>(std::sin(angle / kStegoCycle));
    flat[kGlobals + 3] = static_cast<float>(std::cos(angle / kStegoCycle));
    for (int row = 0; row < kHeight; row++) {
        for (int column = 0; column < kWidth; column++) {
            int cell = row * kWidth + column;
            x[(kChannels + 1) * kCells + cell] = -1 + 2 * row / static_cast<float>(kHeight - 1);
            x[(kChannels + 2) * kCells + cell] = -1 + 2 * column / static_cast<float>(kWidth - 1);
            for (int i = 0; i < kGlobals + 4; i++) x[(kChannels + 3 + i) * kCells + cell] = flat[i];
        }
    }

    const float* w = weights_.data();
    float* y = b_.data();
    convolve(x, kPlanes, w, channels_, 1, y);
    w += channels_ * kPlanes * 9 + channels_;
    int size = channels_ * kCells;
    for (int i = 0; i < size; i++) y[i] = std::max(0.0f, y[i]);
    std::swap(x, y);  // x holds the current activations from here on.

    for (int block = 0; block < kBlocks; block++) {
        convolve(x, channels_, w, channels_, kDilations[block], y);
        w += channels_ * channels_ * 9 + channels_;
        for (int i = 0; i < size; i++) x[i] += std::max(0.0f, y[i]);
    }

    const float* tile_weights = w;
    const float* tile_bias = w + 2 * channels_;
    w += 2 * channels_ + 2;
    std::memset(logits, 0, sizeof(float) * kActions);
    for (int head = 0; head < 2; head++) {
        float* out = logits + 1 + head * kCells;
        for (int c = 0; c < channels_; c++) {
            float weight = tile_weights[head * channels_ + c];
            const float* in = x + c * kCells;
            for (int cell = 0; cell < kCells; cell++) out[cell] += weight * in[cell];
        }
        for (int cell = 0; cell < kCells; cell++) out[cell] += tile_bias[head];
    }

    std::vector<float> pooled(2 * channels_);
    for (int c = 0; c < channels_; c++) {
        const float* in = x + c * kCells;
        float sum = 0;
        float top = in[0];
        for (int cell = 0; cell < kCells; cell++) {
            sum += in[cell];
            top = std::max(top, in[cell]);
        }
        pooled[c] = sum / kCells;
        pooled[channels_ + c] = top;
    }
    std::vector<float> hidden(hidden_);
    for (int h = 0; h < hidden_; h++) {
        float sum = w[2 * channels_ * hidden_ + h];
        for (int p = 0; p < 2 * channels_; p++) sum += w[h * 2 * channels_ + p] * pooled[p];
        hidden[h] = std::max(0.0f, sum);
    }
    w += 2 * channels_ * hidden_ + hidden_;
    float none = w[hidden_];
    for (int h = 0; h < hidden_; h++) none += w[h] * hidden[h];
    logits[0] = none;
}

}  // namespace sim
