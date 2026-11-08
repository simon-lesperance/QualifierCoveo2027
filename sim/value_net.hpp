#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace sim {

// Hand-written inference for tools/train_value.py's network, so the bot needs no ML library.
class ValueNet {
public:
    // Reads the exported file: three int32 sizes, then float32 parameters.
    bool load(const char* path);

    // Takes bytes from encode_features. Returns thousands of points.
    float evaluate(const uint8_t* features) const;

private:
    template <int kFirst, int kSecond, int kHidden>
    float evaluate_sized(const uint8_t* features) const;

    int first_ = 0;
    int second_ = 0;
    int hidden_ = 0;
    int flat_ = 0;

    std::vector<float> conv1_weights_;  // [input channel * 9 + kernel position][first]
    std::vector<float> conv1_bias_;
    std::vector<float> conv2_weights_;  // [kernel position][first][second]
    std::vector<float> conv2_bias_;
    std::vector<float> fc1_weights_;    // [flat input][hidden]
    std::vector<float> fc1_bias_;
    std::vector<float> fc2_weights_;
    float fc2_bias_ = 0;
};

}  // namespace sim
