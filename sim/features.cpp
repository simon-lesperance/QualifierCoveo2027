#include "features.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>

namespace sim {

namespace {

enum Channel {
    kElevation = 0,
    kImpassableChannel = 1,
    kMountainChannel = 2,
    kLavaChannel = 3,
    kCorpse = 4,
    kFirstSpecies = 5,  // One channel per species, holding the dinosaur's remaining value.
    kFirstMeteor = 9,   // One channel per turn until impact, 1 to 4.
    kVolcano = 13,
};

const auto kValue = [] {
    std::array<uint8_t, kMaxTicks + 2> table{};
    for (int age = 0; age < static_cast<int>(table.size()); age++) {
        double value = std::ceil(std::max(0.0, 500 / std::sqrt(age / 4.0 + 5) - 60));
        table[age] = static_cast<uint8_t>(std::max(1.0, value));  // Worthless dinosaurs still exist.
    }
    return table;
}();

}  // namespace

void encode_features(const Game& game, int tick, uint8_t* out) {
    std::memset(out, 0, kFeatureBytes);
    auto channel = [&](int index) { return out + index * kCells; };

    uint8_t* elevation = channel(kElevation);
    uint8_t* impassable = channel(kImpassableChannel);
    uint8_t* mountain = channel(kMountainChannel);
    uint8_t* lava = channel(kLavaChannel);
    uint8_t* corpse = channel(kCorpse);
    for (int cell = 0; cell < kCells; cell++) {
        uint8_t flags = game.flags(cell);
        elevation[cell] = static_cast<uint8_t>(game.elevation(cell) * 12);
        if (flags & kImpassable) impassable[cell] = 255;
        if (flags & kMountain) mountain[cell] = 255;
        if (flags & kLava) lava[cell] = 255;
        if (game.corpse(cell)) corpse[cell] = 255;
    }

    int newest_age = kMaxTicks;
    for (int i = 0; i < game.dino_count(); i++) {
        const Dino& dino = game.dino(i);
        int age = std::max(0, tick - 1 - dino.spawn_turn);
        newest_age = std::min(newest_age, age);
        channel(kFirstSpecies + static_cast<int>(dino.species))[dino.cell] = kValue[std::min(age, kMaxTicks + 1)];
    }

    constexpr int kSquared = kMeteorRadius * kMeteorRadius;
    for (int i = 0; i < game.meteor_count(); i++) {
        const Meteor& meteor = game.meteor(i);
        uint8_t* plane = channel(kFirstMeteor + std::clamp<int>(meteor.turns, 1, kMeteorDelay) - 1);
        int center_row = meteor.y - game.origin_y();
        for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
            for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
                int row = center_row + dy;
                int column = meteor.x + dx;
                if (dx * dx + dy * dy > kSquared || row < 0 || row >= kHeight || column < 0 || column >= kWidth) continue;
                plane[row * kWidth + column] = 255;
            }
        }
    }

    for (int i = 0; i < game.volcano_count(); i++) {
        int row = game.volcano(i).y - game.origin_y();
        if (row >= 0 && row < kHeight) channel(kVolcano)[row * kWidth + game.volcano(i).x] = 255;
    }

    uint8_t* globals = out + kChannels * kCells;
    globals[0] = static_cast<uint8_t>(std::min(tick, kMaxTicks) / 4);
    globals[1] = static_cast<uint8_t>(game.dino_count() * 12);
    globals[2] = static_cast<uint8_t>(game.meteor_count() * 80);
    globals[3] = game.volcano_count() > 0 ? 255 : 0;
    globals[4] = static_cast<uint8_t>(std::min(newest_age, 100) * 2);  // Hints at when the next wave comes.
}

}  // namespace sim
