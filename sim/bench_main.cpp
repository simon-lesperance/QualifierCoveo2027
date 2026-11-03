// Measures simulator speed on random play.
//
//   sim_bench [games]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "game.hpp"

namespace {

using sim::Action;
using sim::Game;

constexpr const char* kBiomes[3] = {"deep_crevasse", "mountain_range", "volcanic_plains"};

struct FastRandom {
    uint64_t state;

    uint32_t next(uint32_t bound) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        return static_cast<uint32_t>((state >> 16) % bound);
    }
};

Action random_action(const Game& game, FastRandom& random) {
    Action action;
    if (game.dino_count() == 0 || random.next(10) < 3) return action;
    const sim::Dino& dino = game.dino(static_cast<int>(random.next(game.dino_count())));
    action.type = Action::Type::LaunchMeteor;
    action.x = dino.cell % sim::kWidth;
    action.y = dino.cell / sim::kWidth + game.origin_y();
    return action;
}

double seconds_since(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

}  // namespace

int main(int argc, char** argv) {
    int games = argc > 1 ? std::atoi(argv[1]) : 30;
    std::printf("sizeof(Game) = %zu bytes\n", sizeof(Game));

    for (int biome = 0; biome < 3; biome++) {
        double simulate_seconds = 0;
        long long ticks = 0;
        long long total_score = 0;
        volatile int sink = 0;
        for (int i = 0; i < games; i++) {
            std::string seed = "bench-" + std::to_string(i);
            sim::Terrain terrain(seed, kBiomes[biome]);
            Game game(terrain, seed);
            FastRandom random{0x9E3779B97F4A7C15ull + static_cast<uint64_t>(i)};
            auto start = std::chrono::steady_clock::now();
            for (int turn = 1; turn <= sim::kMaxTicks; turn++) {
                game.step(turn, random_action(game, random));
            }
            simulate_seconds += seconds_since(start);
            ticks += sim::kMaxTicks;
            total_score += game.score();
            sink = game.total_kills();
        }
        (void)sink;
        double ns_per_tick = simulate_seconds / static_cast<double>(ticks) * 1e9;
        std::printf("%-16s %8.0f ns/tick  %8.0f ticks/s  %6.1f games/s  mean score %lld\n", kBiomes[biome], ns_per_tick,
                    1e9 / ns_per_tick, 1e9 / ns_per_tick / sim::kMaxTicks, total_score / games);
    }

    // Branching cost: a plain copy of the whole state.
    sim::Terrain terrain("bench-copy", kBiomes[0]);
    Game game(terrain, "bench-copy");
    Game copy = game;
    auto start = std::chrono::steady_clock::now();
    constexpr int kCopies = 20000000;
    for (int i = 0; i < kCopies; i++) {
        copy = game;
        asm volatile("" : : "r"(&copy) : "memory");  // Keeps the optimizer from removing the copy.
    }
    std::printf("copy: %.1f ns\n", seconds_since(start) / kCopies * 1e9);
    return 0;
}
