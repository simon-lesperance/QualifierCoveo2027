#include "terrain_infer.hpp"

#include <algorithm>
#include <chrono>

namespace sim {

namespace {

constexpr size_t kLayerCap = 1500;    // Solutions per layer before an extension is put off.
constexpr size_t kPairCap = 3000000;  // Layer pairs checked in one extension.
constexpr size_t kHypothesisCap = 4096;
constexpr int kTrigger = 2;           // Rows of a band seen before its lower lattice row is solved.
constexpr size_t kPredictHypotheses = 8;

}  // namespace

TerrainInference::TerrainInference(std::string_view biome) : model_("inference", biome) {
    supported_ = biome == "volcanic_plains" && model_.layer_count() == kLayers;
    // Layer values after the filters, computed as the engine does: terrace, invert, offset.
    for (int n = -9; n <= 9; n++) levels_[0].push_back(-(static_cast<double>(n) / 3));
    for (int n = -4; n <= 12; n++) levels_[1].push_back(-(static_cast<double>(n) / 4) + -0.005);
    if (!supported_) failed_ = true;
}

void TerrainInference::use(const Grid& grid) {
    for (int layer = 0; layer < kLayers; layer++) model_.set_gradient_grid(layer, grid.data() + layer * kLayerSize, kRows);
}

bool TerrainInference::consistent(const Grid& grid, int y) {
    use(grid);
    const uint8_t* seen = row(y);
    for (int x = 0; x < kWidth; x++) {
        double total = 0;
        for (int layer = 0; layer < kLayers; layer++) total += model_.layer_value(layer, x, y);
        if (model_.height_from_total(total) != seen[x]) return false;
    }
    return true;
}

void TerrainInference::filter(std::vector<Grid>& grids, int y) {
    size_t kept = 0;
    for (size_t i = 0; i < grids.size(); i++) {
        if (consistent(grids[i], y)) grids[kept++] = grids[i];
    }
    grids.resize(kept);
}

bool TerrainInference::layer_tile_ok(int layer, int x, int y) {
    double value = model_.layer_value(layer, x, y);
    int height = row(y)[x];
    for (double other : levels_[1 - layer]) {
        double total = layer == 0 ? value + other : other + value;
        if (model_.height_from_total(total) == height) return true;
    }
    return false;
}

namespace {

double steady_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

}  // namespace

bool TerrainInference::out_of_time() { return deadline_ > 0 && steady_ms() >= deadline_; }

bool TerrainInference::layer_step(int layer, int first, int last, const std::vector<int>& check_rows) {
    Extension& e = ext_;
    int count = last - first + 1;
    int options = count == 1 ? 8 : 64;
    std::vector<Assignment>& out = e.solutions[layer];
    use(e.grid);
    while (e.column >= 0) {
        if (e.column == kUsedCols) {
            out.push_back(e.current);
            if (out.size() > kLayerCap) return true;
            e.column--;
            continue;
        }
        int column = e.column;
        uint8_t* cells = e.grid.data() + layer * kLayerSize + column * kRows;
        if (e.option[column] >= options) {
            for (int i = 0; i < count; i++) cells[first + i] = 8;
            e.option[column] = 0;
            e.column--;
            continue;
        }
        if ((++nodes_ & 15) == 0 && out_of_time()) return false;
        int option = e.option[column]++;
        for (int i = 0; i < count; i++) {
            uint8_t value = static_cast<uint8_t>((option >> (3 * i)) & 7);
            cells[first + i] = value;
            e.current[column * 2 + i] = value;
        }
        bool ok = true;
        if (column >= 1) {  // Tiles whose lattice columns are now all assigned.
            for (int y : check_rows) {
                for (int x = 5 * (column - 1); x < 5 * column && ok; x++) ok = layer_tile_ok(layer, x, y);
                if (!ok) break;
            }
        }
        if (ok) e.column++;
    }
    return true;
}

TerrainInference::Step TerrainInference::extend_step(const Grid& base, int first, int last,
                                                     const std::vector<int>& check_rows, std::vector<Grid>& next) {
    Extension& e = ext_;
    if (!e.active) {
        e.active = true;
        e.stage = 0;
        e.grid = base;
        e.column = 0;
        e.option.fill(0);
        for (int layer = 0; layer < kLayers; layer++) {
            e.solutions[layer].clear();
            e.values[layer].clear();
        }
        e.join_a = 0;
    }
    auto fail = [&] {
        e.active = false;
        return Step::Failed;
    };
    while (e.stage < kLayers) {
        if (!layer_step(e.stage, first, last, check_rows)) return Step::Paused;
        if (e.solutions[e.stage].size() > kLayerCap) return fail();
        e.stage++;
        e.grid = base;
        e.column = 0;
        e.option.fill(0);
    }
    auto apply = [&](Grid& grid, int layer, const Assignment& a) {
        for (int c = 0; c < kUsedCols; c++)
            for (int k = first; k <= last; k++) grid[layer * kLayerSize + c * kRows + k] = a[c * 2 + (k - first)];
    };
    size_t tiles = check_rows.size() * kWidth;
    if (e.stage == kLayers) {
        // Each solution's layer values on the rows checked, for the exact join.
        if (e.solutions[0].size() * e.solutions[1].size() > kPairCap) return fail();
        for (int layer = 0; layer < kLayers; layer++) {
            e.values[layer].resize(e.solutions[layer].size() * tiles);
            Grid grid = base;
            for (size_t s = 0; s < e.solutions[layer].size(); s++) {
                apply(grid, layer, e.solutions[layer][s]);
                use(grid);
                size_t index = s * tiles;
                for (int y : check_rows)
                    for (int x = 0; x < kWidth; x++) e.values[layer][index++] = model_.layer_value(layer, x, y);
            }
        }
        e.stage++;
        e.join_a = 0;
    }
    const std::vector<double>* values = e.values;
    for (; e.join_a < e.solutions[0].size(); e.join_a++) {
        if (out_of_time()) return Step::Paused;
        size_t a = e.join_a;
        for (size_t b = 0; b < e.solutions[1].size(); b++) {
            bool ok = true;
            size_t tile = 0;
            for (int y : check_rows) {
                const uint8_t* seen = row(y);
                for (int x = 0; x < kWidth && ok; x++, tile++) {
                    double total = values[0][a * tiles + tile] + values[1][b * tiles + tile];
                    ok = model_.height_from_total(total) == seen[x];
                }
                if (!ok) break;
            }
            if (!ok) continue;
            Grid grid = base;
            apply(grid, 0, e.solutions[0][a]);
            apply(grid, 1, e.solutions[1][b]);
            next.push_back(grid);
            if (next.size() > kHypothesisCap) return fail();
        }
    }
    e.active = false;
    return Step::Done;
}

void TerrainInference::observe(int y, const uint8_t* heights) {
    if (failed_) return;
    if (rows_.empty()) {
        first_y_ = y;
        Grid unknown;
        unknown.fill(8);
        hypotheses_.assign(1, unknown);
    } else if (y != last_row() + 1) {
        failed_ = true;  // A gap: this inference can't use it.
        return;
    }
    if (needed_lattice_row(y) + 1 >= kRows) {
        failed_ = true;
        return;
    }
    std::array<uint8_t, kWidth> copy;
    std::copy(heights, heights + kWidth, copy.begin());
    rows_.push_back(copy);

    // A row whose lattice is already assigned rules hypotheses out directly.
    if (needed_lattice_row(y) <= assigned_) {
        size_t before = hypotheses_.size();
        filter(hypotheses_, y);
        if (hypotheses_.size() != before) version_++;
        if (hypotheses_.empty()) failed_ = true;
    }
}

void TerrainInference::work(double budget_ms) {
    deadline_ = budget_ms > 0 ? steady_ms() + budget_ms : 0;
    auto expired = [&] { return out_of_time(); };
    while (!failed_) {
        if (!pending_) {
            int first = assigned_ + 1;
            int last = std::max(first, 1);  // The first extension assigns rows 0 and 1 together.
            if (last_row() < 5 * (last - 1) + kTrigger) return;
            if (static_cast<int>(rows_.size()) <= deferred_rows_) return;  // Nothing new since it was too ambiguous.
            if (deferred_rows_ >= 0 && last == first && last_row() >= 5 * last + kTrigger) {
                last = first + 1;  // Still ambiguous alone: solve it with the next lattice row.
            }
            pending_ = true;
            pending_first_ = first;
            pending_last_ = last;
            pending_index_ = 0;
            pending_next_.clear();
            pending_rows_.clear();
            for (int y = first_y_; y <= last_row(); y++) {
                int needed = needed_lattice_row(y);
                if (needed > assigned_ && needed <= last) pending_rows_.push_back(y);
            }
            pending_seen_ = last_row();
        }
        while (pending_index_ < hypotheses_.size()) {
            if (expired()) return;
            Step step = extend_step(hypotheses_[pending_index_], pending_first_, pending_last_, pending_rows_, pending_next_);
            if (step == Step::Paused) return;
            pending_index_++;
            if (step == Step::Failed) {
                pending_ = false;
                pending_next_.clear();
                if (pending_last_ > pending_first_ && pending_first_ > 0) {
                    failed_ = true;  // Two lattice rows at once and still too ambiguous.
                } else {
                    deferred_rows_ = static_cast<int>(rows_.size());
                }
                return;
            }
        }
        // Rows seen while this ran, which depend on the lattice rows just assigned.
        for (int y = pending_seen_ + 1; y <= last_row(); y++) {
            if (needed_lattice_row(y) <= pending_last_) filter(pending_next_, y);
        }
        hypotheses_ = std::move(pending_next_);
        pending_next_.clear();
        assigned_ = pending_last_;
        pending_ = false;
        deferred_rows_ = -1;
        version_++;
        if (hypotheses_.empty()) failed_ = true;
    }
}

const TerrainInference::Cached* TerrainInference::find_cached(int y) const {
    for (const Cached& slot : cache_)
        if (slot.y == y && slot.version == version_) return &slot;
    return nullptr;
}

bool TerrainInference::predict(int y, uint8_t* heights, uint8_t* flags) {
    if (failed_ || hypotheses_.empty() || y < first_y_) return false;
    if (y <= last_row()) {
        std::copy(row(y), row(y) + kWidth, heights);
    } else if (const Cached* hit = find_cached(y)) {
        std::copy(hit->heights.begin(), hit->heights.end(), heights);
    } else {
        if (needed_lattice_row(y) + 1 >= kRows) return false;
        std::vector<std::array<double, 21>> votes(kWidth);
        for (auto& vote : votes) vote.fill(0);
        int unknown = assigned_ + 1;
        // Rows seen that depend on the next unassigned lattice row: they narrow its gradients.
        std::vector<int> band;
        for (int seen = std::max(first_y_, 5 * (unknown - 1)); seen <= last_row(); seen++) {
            if (needed_lattice_row(seen) == unknown) band.push_back(seen);
        }
        bool marginal = needed_lattice_row(y) >= unknown && unknown + 1 < kRows;
        size_t used = std::min<size_t>(hypotheses_.size(), kPredictHypotheses);
        for (size_t h = 0; h < used; h++) {
            Grid grid = hypotheses_[h * hypotheses_.size() / used];  // An even spread, not the first few.
            use(grid);
            if (!marginal) {
                for (int x = 0; x < kWidth; x++) {
                    double total = 0;
                    for (int layer = 0; layer < kLayers; layer++) total += model_.layer_value(layer, x, y);
                    votes[x][model_.height_from_total(total)] += 1;
                }
                continue;
            }
            // Per tile, average over the two unknown gradients per layer that it reads, keeping
            // the combinations that reproduce the band rows already seen at this x.
            int count = static_cast<int>(band.size()) + 1;
            std::vector<double> values[kLayers];
            for (int x = 0; x < kWidth; x++) {
                int column = (x * 2) / 10;  // floor(0.2 x)
                for (int layer = 0; layer < kLayers; layer++) {
                    values[layer].assign(64 * count, 0);
                    uint8_t* left = grid.data() + layer * kLayerSize + column * kRows + unknown;
                    uint8_t* right = left + kRows;
                    for (int combo = 0; combo < 64; combo++) {
                        *left = static_cast<uint8_t>(combo & 7);
                        *right = static_cast<uint8_t>(combo >> 3);
                        for (int i = 0; i < count; i++) {
                            int row_y = i < count - 1 ? band[i] : y;
                            values[layer][combo * count + i] = model_.layer_value(layer, x, row_y);
                        }
                    }
                    *left = 8;
                    *right = 8;
                }
                std::array<double, 21> tile{};
                double weight = 0;
                for (int a = 0; a < 64; a++) {
                    for (int b = 0; b < 64; b++) {
                        bool ok = true;
                        for (int i = 0; i < count - 1 && ok; i++) {
                            double total = values[0][a * count + i] + values[1][b * count + i];
                            ok = model_.height_from_total(total) == row(band[i])[x];
                        }
                        if (!ok) continue;
                        double total = values[0][a * count + count - 1] + values[1][b * count + count - 1];
                        tile[model_.height_from_total(total)] += 1;
                        weight += 1;
                    }
                }
                if (weight > 0)
                    for (int v = 0; v < 21; v++) votes[x][v] += tile[v] / weight;
            }
        }
        for (int x = 0; x < kWidth; x++) {
            heights[x] = static_cast<uint8_t>(std::max_element(votes[x].begin(), votes[x].end()) - votes[x].begin());
        }
        Cached& slot = cache_[cache_next_++ % cache_.size()];
        slot.y = y;
        slot.version = version_;
        std::copy(heights, heights + kWidth, slot.heights.begin());
    }
    for (int x = 0; x < kWidth; x++) flags[x] = model_.flags_for_height(heights[x]);
    return true;
}

bool TerrainInference::sample_rows(int first_y, int count, uint32_t seed, uint8_t* heights, uint8_t* flags) {
    if (failed_ || hypotheses_.empty() || first_y <= last_row()) return false;
    int deepest = needed_lattice_row(first_y + count - 1);
    if (deepest + 1 >= kRows) return false;
    uint32_t state = seed * 2654435761u + static_cast<uint32_t>(version_) * 40503u + 1;
    auto next = [&] {
        state = state * 1664525u + 1013904223u;
        return state >> 8;
    };
    Grid grid = hypotheses_[next() % hypotheses_.size()];
    use(grid);
    int unknown = assigned_ + 1;
    std::vector<int> band;
    for (int seen = std::max(first_y_, 5 * (unknown - 1)); seen <= last_row(); seen++) {
        if (needed_lattice_row(seen) == unknown) band.push_back(seen);
    }
    if (unknown <= deepest) {
        // Rejection: redraw the unknown row's gradients until the band rows seen agree.
        for (int attempt = 0; attempt < 200; attempt++) {
            for (int layer = 0; layer < kLayers; layer++)
                for (int c = 0; c < kUsedCols; c++) grid[layer * kLayerSize + c * kRows + unknown] = next() % 8;
            bool ok = true;
            for (int y : band) {
                if (!consistent(grid, y)) {
                    ok = false;
                    break;
                }
            }
            if (ok) break;
        }
        for (int k = unknown + 1; k <= deepest + 1 && k < kRows; k++)
            for (int layer = 0; layer < kLayers; layer++)
                for (int c = 0; c < kUsedCols; c++) grid[layer * kLayerSize + c * kRows + k] = next() % 8;
    }
    use(grid);
    for (int i = 0; i < count; i++) {
        for (int x = 0; x < kWidth; x++) {
            double total = 0;
            for (int layer = 0; layer < kLayers; layer++) total += model_.layer_value(layer, x, first_y + i);
            int height = model_.height_from_total(total);
            heights[i * kWidth + x] = static_cast<uint8_t>(height);
            flags[i * kWidth + x] = model_.flags_for_height(height);
        }
    }
    return true;
}

int TerrainInference::matching(const Terrain& truth) const {
    int count = 0;
    for (const Grid& grid : hypotheses_) {
        bool same = true;
        for (int layer = 0; layer < kLayers && same; layer++)
            for (int c = 0; c < kUsedCols && same; c++)
                for (int k = 0; k <= assigned_ && same; k++)
                    same = grid[layer * kLayerSize + c * kRows + k] == truth.true_gradient(layer, c, k);
        count += same;
    }
    return count;
}

}  // namespace sim
