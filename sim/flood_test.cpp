// Include the implementation to test its internal flood without exporting a test API.
#include "game.cpp"

#include <cstdio>
#include <random>
#include <vector>

namespace {

using Flags = std::array<uint8_t, sim::kCells>;
using Distances = std::array<int16_t, sim::kCells>;
constexpr int16_t kUnvisited = 32767;
int cases = 0;

Distances reference(const Flags& flags, Distances distances, std::vector<int> queue) {
    for (size_t head = 0; head < queue.size(); head++) {
        int cell = queue[head];
        int row = cell / sim::kWidth;
        int column = cell % sim::kWidth;
        for (auto [dx, dy] : std::array<std::pair<int, int>, 4>{{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}}) {
            int r = row + dy;
            int c = column + dx;
            if (r < 0 || r >= sim::kHeight || c < 0 || c >= sim::kWidth) continue;
            int neighbor = r * sim::kWidth + c;
            if (distances[neighbor] != kUnvisited) continue;
            if (flags[neighbor] & (sim::kImpassable | sim::kLava)) continue;
            distances[neighbor] = static_cast<int16_t>(distances[cell] + 1);
            queue.push_back(neighbor);
        }
    }
    return distances;
}

void check(const char* name, const Flags& flags, const std::vector<int>& sources,
           int offset = 0, int previsited = -1) {
    Distances actual;
    actual.fill(kUnvisited);
    for (int source : sources) actual[source] = static_cast<int16_t>(offset);
    if (previsited >= 0) actual[previsited] = 123;
    Distances expected = reference(flags, actual, sources);
    struct GuardedQueue {
        int16_t before = 12345;
        int16_t cells[sim::kCells]{};
        int16_t after = 23456;
    } queue;
    std::copy(sources.begin(), sources.end(), queue.cells);
    sim::flood(flags.data(), actual.data(), queue.cells, static_cast<int>(sources.size()));
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (actual[cell] != expected[cell]) {
            std::fprintf(stderr, "%s case %d: cell (%d,%d): expected %d, got %d\n",
                         name, cases, cell % sim::kWidth, cell / sim::kWidth, expected[cell], actual[cell]);
            std::exit(1);
        }
    }
    if (queue.before != 12345 || queue.after != 23456) {
        std::fprintf(stderr, "%s: flood wrote outside its queue\n", name);
        std::exit(1);
    }
    cases++;
}

}  // namespace

int main() {
    Flags flags{};
    check("no sources", flags, {});
    for (int source = 0; source < sim::kCells; source++) {
        check("open grid, every source", flags, {source});
    }
    check("multiple sources", flags, {0, 19, 380, 399, 210});
    check("nonzero initial distance", flags, {0}, 7);
    check("preserve previsited cell", flags, {0}, 0, 1);
    std::vector<int> all;
    for (int cell = 0; cell < sim::kCells; cell++) all.push_back(cell);
    check("all cells seeded", flags, all);

    flags.fill(sim::kImpassable);
    check("blocked grid", flags, {210});
    // A blocked seed may expand outward: corpse sources are not filtered by flags.
    flags.fill(0);
    for (uint8_t blocked : std::array<uint8_t, 3>{sim::kImpassable, sim::kLava,
                                                sim::kImpassable | sim::kLava}) {
        flags[210] = blocked;
        check("blocked source expands", flags, {210});
    }
    flags.fill(sim::kMountain);
    check("mountain flag alone is not a blocker", flags, {0});
    flags.fill(0);
    for (int row = 0; row < sim::kHeight; row++) flags[row * sim::kWidth + 10] = sim::kImpassable;
    check("disconnected regions", flags, {0});
    check("sources on both sides of wall", flags, {0, 19});
    flags[10 * sim::kWidth + 10] = 0;
    check("wall with detour", flags, {0});
    flags.fill(sim::kLava);
    for (int row = 0; row < sim::kHeight; row++) {
        for (int column = 0; column < sim::kWidth; column++) {
            if (row % 2 == 0 || column == (row % 4 == 1 ? 19 : 0)) flags[row * sim::kWidth + column] = 0;
        }
    }
    check("long winding corridor", flags, {0});

    std::mt19937 random(0xF100D);
    for (int trial = 0; trial < 2000; trial++) {
        for (auto& flag : flags) {
            flag = static_cast<uint8_t>(random() % 8);
            if (trial % 4 == 0) flag &= ~sim::kImpassable;
        }
        std::vector<int> sources;
        int count = trial % 21;
        while (static_cast<int>(sources.size()) < count) {
            int cell = static_cast<int>(random() % sim::kCells);
            if (std::find(sources.begin(), sources.end(), cell) == sources.end()) sources.push_back(cell);
        }
        check("seeded random terrain", flags, sources);
    }
    std::printf("flood regression: %d cases, every distance checked\n", cases);
}
