#pragma once

#include <array>
#include <cstdint>
#include <string_view>
#include <vector>

namespace sim {

enum TileFlag : uint8_t {
    kImpassable = 1,
    kMountain = 2,
    kLava = 4,
};

// The engine's seeded noise terrain: one generator per game, immutable once built.
class Terrain {
public:
    Terrain(std::string_view seed, std::string_view biome);

    // Fills width tiles of world row y, for x from 0.
    void row(int y, int width, uint8_t* elevation, uint8_t* flags) const;

private:
    enum class Interpolation { Smoothstep, Quintic };
    enum class FilterType { Clamp, Abs, Invert, Offset, Power, Terrace };

    struct Filter {
        FilterType type;
        double a = 0;
        double b = 0;
        bool normalize = false;
    };

    struct Layer {
        double amplitude;
        double frequency;
        int octaves;
        double persistence;
        double lacunarity;
        Interpolation interpolation;
        std::vector<Filter> filters;
        std::array<uint8_t, 512> permutation;
    };

    static double noise(const Layer& layer, double x, double y);
    static double sample(const Layer& layer, double x, double y);

    std::vector<Layer> layers_;
    int max_elevation_ = 20;
    int mountain_min_ = 0;
    int impassable_max_ = 0;
};

}  // namespace sim
