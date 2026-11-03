#include "rng.hpp"

#include <charconv>
#include <cmath>

namespace sim {

namespace {

constexpr double kStartDenominator = 281474976710656.0;  // 256^6
constexpr double kSignificance = 4503599627370496.0;     // 2^52
constexpr double kOverflow = 9007199254740992.0;         // 2^53

// seedrandom's mixkey. The smear term stays 0 unless the seed wraps past 256 characters.
int mix_key(std::string_view seed, uint8_t* key) {
    int length = 0;
    int smear = 0;
    for (size_t i = 0; i < seed.size(); i++) {
        int slot = static_cast<int>(i) & 255;
        bool existing = slot < length;
        smear ^= existing ? key[slot] * 19 : 0;
        key[slot] = static_cast<uint8_t>(smear + static_cast<unsigned char>(seed[i]));
        if (!existing) length = slot + 1;
    }
    return length;
}

}  // namespace

Arc4::Arc4(std::string_view seed) {
    uint8_t key[256];
    int length = mix_key(seed, key);
    init(key, length);
}

Arc4::Arc4(uint32_t numeric_seed) {
    char text[16];
    auto end = std::to_chars(text, text + sizeof(text), numeric_seed).ptr;
    uint8_t key[256];
    int length = mix_key(std::string_view(text, end - text), key);
    init(key, length);
}

void Arc4::init(const uint8_t* key, int length) {
    static const uint8_t kEmptyKey[1] = {0};
    if (length == 0) {
        key = kEmptyKey;
        length = 1;
    }
    for (int i = 0; i < 256; i++) state_[i] = static_cast<uint8_t>(i);
    uint8_t j = 0;
    for (int i = 0; i < 256; i++) {
        uint8_t held = state_[i];
        j = static_cast<uint8_t>(j + key[i % length] + held);
        state_[i] = state_[j];
        state_[j] = held;
    }
    i_ = 0;
    j_ = 0;
    take(256);  // RC4-drop[256]
}

uint64_t Arc4::take(int count) {
    uint64_t result = 0;
    uint8_t i = i_;
    uint8_t j = j_;
    while (count--) {
        i = static_cast<uint8_t>(i + 1);
        uint8_t held = state_[i];
        j = static_cast<uint8_t>(j + held);
        state_[i] = state_[j];
        state_[j] = held;
        result = result * 256 + state_[static_cast<uint8_t>(state_[i] + held)];
    }
    i_ = i;
    j_ = j;
    return result;
}

double Arc4::random() {
    double value = static_cast<double>(take(6));
    double denominator = kStartDenominator;
    uint64_t extra = 0;
    while (value < kSignificance) {
        value = (value + static_cast<double>(extra)) * 256;
        denominator *= 256;
        extra = take(1);
    }
    while (value >= kOverflow) {
        value /= 2;
        denominator /= 2;
        extra >>= 1;
    }
    return (value + static_cast<double>(extra)) / denominator;
}

int Arc4::next_int(double n) {
    return static_cast<int>(std::floor(random() * n));
}

}  // namespace sim
