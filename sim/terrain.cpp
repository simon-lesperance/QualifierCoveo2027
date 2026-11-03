#include "terrain.hpp"

#include <cmath>
#include <stdexcept>
#include <string>

#include "rng.hpp"

namespace sim {

namespace {

constexpr int kGradients[8][2] = {{1, 1}, {-1, 1}, {1, -1}, {-1, -1}, {1, 0}, {-1, 0}, {0, 1}, {0, -1}};

double lerp(double from, double to, double weight) {
    return from + weight * (to - from);
}

double gradient(int index, double x, double y) {
    return kGradients[index][0] * x + kGradients[index][1] * y;
}

// JS Math.round: halves round up, unlike std::round.
double js_round(double value) {
    double lower = std::floor(value);
    return value - lower >= 0.5 ? lower + 1 : lower;
}

double js_sign(double value) {
    return value > 0 ? 1 : (value < 0 ? -1 : value);
}

}  // namespace

Terrain::Terrain(std::string_view seed, std::string_view biome) {
    using I = Interpolation;
    using F = FilterType;
    auto layer = [](double amplitude, double frequency, int octaves, double persistence, double lacunarity,
                    I interpolation, std::vector<Filter> filters) {
        return Layer{amplitude, frequency, octaves, persistence, lacunarity, interpolation, std::move(filters), {}};
    };
    Filter abs{F::Abs};
    Filter abs_normalized{F::Abs, 0, 0, true};
    Filter invert{F::Invert};
    auto power = [](double exponent) { return Filter{F::Power, exponent}; };
    auto terrace = [](double steps) { return Filter{F::Terrace, steps}; };
    auto offset = [](double value) { return Filter{F::Offset, value}; };

    if (biome == "deep_crevasse") {
        layers_ = {
            layer(1, 0.1, 1, 0.5, 2, I::Smoothstep, {abs_normalized, power(0.9), terrace(5)}),
            layer(0.4, 0.3, 2, 0.5, 2, I::Smoothstep, {}),
            layer(0.3, 0.2, 1, 0.5, 2, I::Smoothstep, {abs, invert, offset(0.12), power(0.8)}),
        };
        mountain_min_ = 13;
        impassable_max_ = 1;
    } else if (biome == "mountain_range") {
        layers_ = {
            layer(1, 0.15, 6, 0.6, 2, I::Quintic, {Filter{F::Clamp, -0.5, 1}, power(0.7)}),
            layer(0.3, 0.2, 3, 0.5, 2, I::Smoothstep, {}),
            layer(0.3, 0.2, 1, 0.5, 2, I::Smoothstep, {abs, power(0.9)}),
        };
        mountain_min_ = 16;
        impassable_max_ = 2;
    } else if (biome == "volcanic_plains") {
        layers_ = {
            layer(1, 0.1, 2, 0.5, 2, I::Smoothstep, {abs_normalized, power(0.9), terrace(3), invert}),
            layer(1, 0.1, 2, 0.5, 2, I::Smoothstep, {abs, power(0.9), terrace(4), invert, offset(-0.005)}),
        };
        mountain_min_ = 18;
        impassable_max_ = 1;
    } else {
        throw std::invalid_argument("unknown biome: " + std::string(biome));
    }

    // One RNG, drawn in layer order, separate from the game's RNG.
    Arc4 rng(seed);
    for (Layer& item : layers_) {
        auto& table = item.permutation;
        for (int i = 0; i < 256; i++) table[i] = static_cast<uint8_t>(i);
        for (int i = 255; i > 0; i--) {
            uint32_t swap = rng.uint32() % static_cast<uint32_t>(i + 1);
            std::swap(table[i], table[swap]);
        }
        for (int i = 0; i < 256; i++) table[i + 256] = table[i];
    }
}

double Terrain::noise(const Layer& layer, double x, double y) {
    double floor_x = std::floor(x);
    double floor_y = std::floor(y);
    int cell_x = static_cast<int>(floor_x) & 255;
    int cell_y = static_cast<int>(floor_y) & 255;
    double fraction_x = x - floor_x;
    double fraction_y = y - floor_y;

    const auto& table = layer.permutation;
    int top_left = table[table[cell_x] + cell_y] & 7;
    int bottom_left = table[table[cell_x] + cell_y + 1] & 7;
    int top_right = table[table[cell_x + 1] + cell_y] & 7;
    int bottom_right = table[table[cell_x + 1] + cell_y + 1] & 7;

    double a = gradient(top_left, fraction_x, fraction_y);
    double b = gradient(top_right, fraction_x - 1, fraction_y);
    double c = gradient(bottom_left, fraction_x, fraction_y - 1);
    double d = gradient(bottom_right, fraction_x - 1, fraction_y - 1);

    auto ease = [&](double t) {
        if (layer.interpolation == Interpolation::Quintic) return t * t * t * (t * (t * 6 - 15) + 10);
        return t * t * (3 - 2 * t);
    };
    double weight_x = ease(fraction_x);
    double weight_y = ease(fraction_y);
    return lerp(lerp(a, b, weight_x), lerp(c, d, weight_x), weight_y);
}

double Terrain::sample(const Layer& layer, double x, double y) {
    x *= layer.frequency;
    y *= layer.frequency;
    double value = 0;
    double weight = 1;
    double scale = 1;
    for (int i = 0; i < layer.octaves; i++) {
        value += noise(layer, x * scale, y * scale) * weight;
        weight *= layer.persistence;
        scale *= layer.lacunarity;
    }
    value *= layer.amplitude;

    for (const Filter& filter : layer.filters) {
        switch (filter.type) {
            case FilterType::Clamp:
                value = std::fmax(filter.a, std::fmin(filter.b, value));
                break;
            case FilterType::Abs:
                value = std::fabs(value);
                if (filter.normalize) value = value * 2 - 1;
                break;
            case FilterType::Invert:
                value = -value;
                break;
            case FilterType::Offset:
                value += filter.a;
                break;
            case FilterType::Power:
                value = js_sign(value) * std::pow(std::fabs(value), filter.a);
                break;
            case FilterType::Terrace:
                value = js_round(value * filter.a) / filter.a;
                break;
        }
    }
    return value;
}

void Terrain::row(int y, int width, uint8_t* elevation, uint8_t* flags) const {
    for (int x = 0; x < width; x++) {
        double total = 0;
        for (const Layer& layer : layers_) total += sample(layer, x, y);

        double level = js_round((total + 1) / 2 * max_elevation_);
        int height = level < 0 ? 0 : (level > max_elevation_ ? max_elevation_ : static_cast<int>(level));

        uint8_t tile = 0;
        if (height >= mountain_min_) tile = kImpassable | kMountain;
        if (height <= impassable_max_) tile = kImpassable;
        elevation[x] = static_cast<uint8_t>(height);
        flags[x] = tile;
    }
}

}  // namespace sim
