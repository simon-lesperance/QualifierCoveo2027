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

    // For inferring terrain from what a live bot sees. Layer `index` then reads gradient
    // indices from grid[cell_x * rows + cell_y] instead of its permutation table; index 8
    // means unknown and counts as a zero gradient. Null restores the table.
    void set_gradient_grid(int index, const uint8_t* grid, int rows);
    int layer_count() const { return static_cast<int>(layers_.size()); }
    int true_gradient(int index, int cell_x, int cell_y) const {
        const auto& table = layers_[index].permutation;
        return table[table[cell_x & 255] + (cell_y & 255)] & 7;
    }
    double layer_value(int index, int x, int y) const { return sample(layers_[index], x, y); }
    int height_from_total(double total) const;
    uint8_t flags_for_height(int height) const;

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
        const uint8_t* grid = nullptr;
        int grid_rows = 0;
    };

    static double noise(const Layer& layer, double x, double y);
    static double sample(const Layer& layer, double x, double y);

    std::vector<Layer> layers_;
    int max_elevation_ = 20;
    int mountain_min_ = 0;
    int impassable_max_ = 0;
};

}  // namespace sim
