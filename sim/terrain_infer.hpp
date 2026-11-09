#pragma once

#include <array>
#include <cstdint>
#include <string_view>
#include <vector>

#include "terrain.hpp"

namespace sim {

// Infers upcoming terrain rows from the rows a live bot has seen, with no seed.
//
// The terrain is layered Perlin noise. A row depends only on the gradient indices at the
// lattice points around it, and both octaves of a layer read the same lattice, so rows
// already seen pin down most of what the next rows need. This keeps every assignment of
// gradient indices consistent with the rows seen so far, and predicts each tile of a future
// row by majority over them. Unknown gradients count as zero, their expected value.
//
// Only volcanic_plains is supported: two layers of two octaves at frequency 0.1.
class TerrainInference {
public:
    explicit TerrainInference(std::string_view biome);

    bool supported() const { return supported_; }
    // True once the rows seen contradicted every hypothesis, or there were too many.
    bool failed() const { return failed_; }
    int hypothesis_count() const { return static_cast<int>(hypotheses_.size()); }
    int assigned() const { return assigned_; }
    // Debug: how many hypotheses match the true terrain's gradients on assigned lattice rows.
    int matching(const Terrain& truth) const;

    // Rows must arrive in order of world y, each exactly as first seen (before any impact).
    // This only stores and checks them; work() does the solving.
    void observe(int y, const uint8_t* heights);
    // Solves for newly needed lattice rows, for up to budget_ms (0: until done). It can be
    // spread over ticks; until it finishes, predictions use what was known before.
    void work(double budget_ms);
    int last_row() const { return first_y_ + static_cast<int>(rows_.size()) - 1; }

    // Predicted heights and flags of world row y. False if there is no usable inference.
    bool predict(int y, uint8_t* heights, uint8_t* flags);

private:
    static constexpr int kWidth = 20;
    static constexpr int kCols = 6;
    static constexpr int kRows = 48;
    static constexpr int kLayers = 2;
    static constexpr int kLayerSize = kCols * kRows;
    static constexpr int kUsedCols = 5;  // Lattice columns 0-4 cover x 0-19 at frequency 0.2.
    using Grid = std::array<uint8_t, kLayers * kLayerSize>;
    using Assignment = std::array<uint8_t, kUsedCols * 2>;  // Per column, up to two lattice rows.

    static int needed_lattice_row(int y) { return (y * 2) / 10 + 1; }  // floor(0.2 y) + 1
    const uint8_t* row(int y) const { return rows_[y - first_y_].data(); }
    void use(const Grid& grid);
    bool consistent(const Grid& grid, int y);
    bool extend_one(Grid& base, int first, int last, const std::vector<int>& check_rows, std::vector<Grid>& next);
    void filter(std::vector<Grid>& grids, int y);
    void layer_search(Grid& grid, int layer, int first, int last, const std::vector<int>& check_rows, int column,
                      Assignment& current, std::vector<Assignment>& out, size_t cap);
    bool layer_tile_ok(int layer, int x, int y);

    bool supported_ = false;
    bool failed_ = false;
    Terrain model_;
    std::vector<Grid> hypotheses_;
    std::vector<std::array<uint8_t, kWidth>> rows_;
    int first_y_ = 0;
    int assigned_ = -1;  // Highest lattice row assigned in every hypothesis.
    std::vector<double> levels_[kLayers];  // Values each layer can take after its filters.
    struct Cached {
        int y = -1;
        uint64_t version = ~0ull;
        std::array<uint8_t, kWidth> heights{};
    };
    const Cached* find_cached(int y) const;
    std::array<Cached, 8> cache_{};
    size_t cache_next_ = 0;
    uint64_t version_ = 0;

    bool pending_ = false;  // An extension in progress, spread over calls to work().
    int pending_first_ = 0;
    int pending_last_ = 0;
    size_t pending_index_ = 0;
    int pending_seen_ = 0;
    std::vector<int> pending_rows_;
    std::vector<Grid> pending_next_;
    int deferred_rows_ = -1;  // Rows seen when an extension was last found too ambiguous.
};

}  // namespace sim
