#pragma once

#include <vector>

#include "features.hpp"

namespace sim {

// Inference for the policy network from tools/train_ppo.py, written out by hand
// because the server has no ML libraries.
class PolicyNet {
public:
    static constexpr int kPlanes = kChannels + 1 + 2 + kGlobals + 4 + 4;
    static constexpr int kActions = 1 + 2 * kCells;

    bool load(const char* path);

    // Takes bytes from encode_policy_features. Fills logits: nothing, then a meteor
    // per tile, then a volcano per tile.
    // Legality isn't applied. Not safe to call from two threads at once.
    void evaluate(const uint8_t* features, int tick, float* logits);

private:
    int channels_ = 0;
    int hidden_ = 0;
    bool depthwise_ = false;
    std::vector<float> weights_;
    std::vector<float> a_;
    std::vector<float> b_;
};

}  // namespace sim
