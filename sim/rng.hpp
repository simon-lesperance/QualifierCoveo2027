#pragma once

#include <cstdint>
#include <string_view>

namespace sim {

// The ARC4 generator from seedrandom, bit-exact with the engine's RNG.
class Arc4 {
public:
    explicit Arc4(std::string_view seed);
    explicit Arc4(uint32_t numeric_seed);

    double random();
    uint32_t uint32() { return static_cast<uint32_t>(take(4)); }

    // Always consumes one random(), as the engine's nextInt and pick do, even for n == 1.
    int next_int(double n);

private:
    void init(const uint8_t* key, int length);
    uint64_t take(int count);

    uint8_t state_[256];
    uint8_t i_ = 0;
    uint8_t j_ = 0;
};

}  // namespace sim
