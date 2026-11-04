#pragma once

#include <cstdint>

#include "game.hpp"

namespace sim {

// Inputs to the value network, stored as bytes so training data stays small.
// Spatial channels use the frame's rows and columns. Everything here is visible
// to a live bot.
constexpr int kChannels = 14;
constexpr int kGlobals = 5;
constexpr int kFeatureBytes = kChannels * kCells + kGlobals;

// `tick` is the tick the state is about to play: the next action would be for this tick.
void encode_features(const Game& game, int tick, uint8_t* out);

}  // namespace sim
