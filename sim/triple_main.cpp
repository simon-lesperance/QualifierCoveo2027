// Can three chained meteors funnel a group into one blast? Dinosaurs flee toward the
// latest impact, so overlapping meteors on consecutive ticks may herd them.
//
// Waits on the first wave, then tries every triple of targets near the group.
//
//   sim_triple <biome> <seed> [threads]

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "game.hpp"

using sim::Action;
using sim::Game;

namespace {

Action meteor_at(const Game& game, int cell) {
    Action action;
    action.type = Action::Type::LaunchMeteor;
    action.x = cell % sim::kWidth;
    action.y = cell / sim::kWidth + game.origin_y();
    return action;
}

struct Best {
    int kills = 0;
    int points = 0;
    int total = 0;
    int a = -1, b = -1, c = -1;
};

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: sim_triple <biome> <seed> [threads]\n");
        return 2;
    }
    std::string seed = argv[2];
    int threads = argc > 3 ? std::atoi(argv[3]) : 11;
    sim::Terrain terrain(seed, argv[1]);
    Game game(terrain, seed);

    std::printf("wait  spread  candidates  best_tick_kills  points_that_tick  total_kills  total_points\n");
    int tick = 1;
    for (int wait = 5; wait <= 60; wait += 5) {
        while (tick < wait) game.step(tick++, Action{});

        int sum_row = 0, sum_column = 0;
        for (int i = 0; i < game.dino_count(); i++) {
            sum_row += game.dino(i).cell / sim::kWidth;
            sum_column += game.dino(i).cell % sim::kWidth;
        }
        int row = sum_row / std::max(1, game.dino_count());
        int column = sum_column / std::max(1, game.dino_count());
        int spread = 0;
        for (int i = 0; i < game.dino_count(); i++) {
            spread = std::max(spread, std::abs(game.dino(i).cell / sim::kWidth - row) + std::abs(game.dino(i).cell % sim::kWidth - column));
        }

        std::vector<int> cells;
        for (int cell = 0; cell < sim::kCells; cell++) {
            if (std::abs(cell / sim::kWidth - row) + std::abs(cell % sim::kWidth - column) <= 4) cells.push_back(cell);
        }

        std::atomic<int> next{0};
        std::mutex lock;
        Best best;
        int size = static_cast<int>(cells.size());
        auto work = [&] {
            Best local;
            for (int a = next.fetch_add(1); a < size; a = next.fetch_add(1)) {
                for (int b = 0; b < size; b++) {
                    for (int c = 0; c < size; c++) {
                        Game copy = game;
                        int start_score = copy.score();
                        int start_kills = copy.total_kills();
                        copy.step(tick, meteor_at(copy, cells[a]));
                        copy.step(tick + 1, meteor_at(copy, cells[b]));
                        copy.step(tick + 2, meteor_at(copy, cells[c]));
                        int peak = 0;
                        int peak_points = 0;
                        for (int t = tick + 3; t <= tick + 6; t++) {
                            int before = copy.score();
                            copy.step(t, Action{});
                            if (copy.score() - before > peak_points) {
                                peak_points = copy.score() - before;
                                peak = copy.kills_this_tick();
                            }
                        }
                        int total = copy.score() - start_score;
                        if (total > local.total) {
                            local = {peak, peak_points, total, cells[a], cells[b], cells[c]};
                            local.kills = peak;
                        }
                        (void)start_kills;
                    }
                }
            }
            std::lock_guard<std::mutex> guard(lock);
            if (local.total > best.total) best = local;
        };
        std::vector<std::thread> pool;
        for (int i = 0; i < threads; i++) pool.emplace_back(work);
        for (std::thread& thread : pool) thread.join();

        std::printf("%4d  %6d  %10d  %15d  %16d  %11s  %12d\n", wait, spread, size, best.kills, best.points, "", best.total);
        std::fflush(stdout);
    }
    return 0;
}
