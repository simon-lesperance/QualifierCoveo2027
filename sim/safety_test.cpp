// Include the implementation to test its internal safety distance without exporting a test API.
#include "game.cpp"

#include <cstdio>
#include <random>
#include <vector>

namespace {

using Flags = std::array<uint8_t, sim::kCells>;
using Danger = std::array<bool, sim::kCells>;
constexpr int kUnvisited = 32767;
int cases = 0;
int queries = 0;

// Plain coordinate BFS from every safe passable cell, through passable cells.
std::array<int, sim::kCells> reference(const Flags& flags, const Danger& danger) {
    std::array<int, sim::kCells> distance;
    distance.fill(kUnvisited);
    std::vector<int> queue;
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (!danger[cell] && !(flags[cell] & sim::kImpassable)) {
            distance[cell] = 0;
            queue.push_back(cell);
        }
    }
    for (size_t head = 0; head < queue.size(); head++) {
        int row = queue[head] / sim::kWidth;
        int column = queue[head] % sim::kWidth;
        for (auto [dx, dy] : std::array<std::pair<int, int>, 4>{{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}}) {
            int r = row + dy;
            int c = column + dx;
            if (r < 0 || r >= sim::kHeight || c < 0 || c >= sim::kWidth) continue;
            int next = r * sim::kWidth + c;
            if (distance[next] != kUnvisited || (flags[next] & sim::kImpassable)) continue;
            distance[next] = distance[queue[head]] + 1;
            queue.push_back(next);
        }
    }
    return distance;
}

void fail(const char* name, int cell, int expected, int actual) {
    std::fprintf(stderr, "%s case %d: cell (%d,%d): expected %d, got %d\n", name, cases,
                 cell % sim::kWidth, cell / sim::kWidth, expected, actual);
    std::exit(1);
}

void check(const char* name, Flags flags, Danger danger) {
    // As in the game, impassable tiles are always dangerous.
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (flags[cell] & sim::kImpassable) danger[cell] = true;
    }
    auto expected = reference(flags, danger);

    for (int cell = 0; cell < sim::kCells; cell++) {
        if (!danger[cell]) continue;
        int search = sim::nearest_safe(flags.data(), danger.data(), cell);
        if (search != expected[cell]) fail(name, cell, expected[cell], search);
        queries++;
    }
    cases++;
}

Danger all(bool value) {
    Danger danger;
    danger.fill(value);
    return danger;
}

}  // namespace

int main() {
    Flags flags{};
    check("nothing dangerous", flags, all(false));
    check("everything dangerous", flags, all(true));
    flags.fill(sim::kImpassable);
    check("everything impassable", flags, all(false));
    flags.fill(sim::kLava);
    check("paths may cross lava", flags, all(true));
    flags.fill(0);

    for (int cell = 0; cell < sim::kCells; cell++) {
        Danger danger = all(true);
        danger[cell] = false;
        check("single safe cell", flags, danger);
    }

    // A row's last column must not touch the next row's first column.
    Danger danger = all(true);
    danger[5 * sim::kWidth] = false;
    check("no wrap from column 19 to column 0", flags, danger);
    danger = all(true);
    danger[5 * sim::kWidth + sim::kWidth - 1] = false;
    check("no wrap from column 0 to column 19", flags, danger);

    flags.fill(0);
    for (int row = 0; row < sim::kHeight; row++) flags[row * sim::kWidth + 10] = sim::kImpassable;
    danger = all(true);
    danger[0] = false;
    check("wall encloses an unsafe region", flags, danger);
    flags[19 * sim::kWidth + 10] = sim::kLava;
    check("lava gap in a wall", flags, danger);

    flags.fill(sim::kImpassable);
    for (int row = 0; row < sim::kHeight; row++) {
        for (int column = 0; column < sim::kWidth; column++) {
            if (row % 2 == 0 || column == (row % 4 == 1 ? 19 : 0)) flags[row * sim::kWidth + column] = 0;
        }
    }
    danger = all(true);
    danger[19 * sim::kWidth + 19] = false;
    check("long winding corridor", flags, danger);

    // Like the game: lava and mountains, meteor disks and danger beside lava.
    std::mt19937 random(0x5AFE);
    for (int trial = 0; trial < 3000; trial++) {
        int impassable = 1 + static_cast<int>(random() % 30);
        int lava = static_cast<int>(random() % 25);
        for (auto& flag : flags) {
            int roll = static_cast<int>(random() % 100);
            flag = roll < impassable ? sim::kImpassable : (roll < impassable + lava ? sim::kLava : 0);
            if (random() % 10 == 0) flag |= sim::kMountain;
        }
        danger.fill(false);
        for (int cell = 0; cell < sim::kCells; cell++) {
            if (flags[cell] & sim::kLava) danger[cell] = true;
        }
        int disks = static_cast<int>(random() % 12);
        for (int i = 0; i < disks; i++) {
            int center_row = static_cast<int>(random() % sim::kHeight);
            int center_column = static_cast<int>(random() % sim::kWidth);
            int radius = 1 + static_cast<int>(random() % 4);
            for (int row = 0; row < sim::kHeight; row++) {
                for (int column = 0; column < sim::kWidth; column++) {
                    int dy = row - center_row;
                    int dx = column - center_column;
                    if (dx * dx + dy * dy <= radius * radius) danger[row * sim::kWidth + column] = true;
                }
            }
        }
        if (trial % 5 == 0) {
            int dense = 50 + static_cast<int>(random() % 50);
            for (bool& cell : danger) cell = cell || static_cast<int>(random() % 100) < dense;
        }
        check("seeded random terrain", flags, danger);
    }
    std::printf("safety regression: %d cases, %d dangerous-cell queries checked\n", cases, queries);
}
