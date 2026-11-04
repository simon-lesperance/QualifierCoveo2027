// How much could one meteor kill if we simply waited? Plays with no actions, and
// every few ticks tries every target on a copy, reporting the best single blast.
//
//   sim_patience <biome> <seed> [every] [species number]
//
// With a species (2 is Triceratops), the bot plays normally until a wave made mostly
// of that species appears, and the test starts there.

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "agent.hpp"
#include "game.hpp"

using sim::Action;
using sim::Game;

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: sim_patience <biome> <seed> [every] [species number]\n");
        return 2;
    }
    std::string seed = argv[2];
    int every = argc > 3 ? std::atoi(argv[3]) : 5;
    sim::Terrain terrain(seed, argv[1]);
    Game game(terrain, seed);

    int start = 1;
    if (argc > 4) {
        int wanted = std::atoi(argv[4]);
        sim::AgentConfig config;
        config.mcts = true;
        config.radius = 2;
        config.mcts_depth = 5;
        config.sim_budget = 3000;
        sim::Agent agent(config);
        for (; start <= sim::kMaxTicks - 100; start++) {
            int count = 0;
            for (int i = 0; i < game.dino_count(); i++) count += static_cast<int>(game.dino(i).species) == wanted;
            if (count >= 6 && count * 2 >= game.dino_count()) break;
            game.step(start, agent.act(game.observe(start)));
        }
        std::printf("reached the wave at tick %d\n", start);
    }

    std::printf("tick alive  S  V  T  R   best_kills  best_points  target\n");
    int overall_kills = 0;
    int overall_points = 0;
    int last_tick = sim::kMaxTicks - sim::kMeteorDelay;
    if (argc > 4) last_tick = std::min(start + 90, last_tick);
    for (int tick = start; tick <= last_tick; tick++) {
        if ((tick - start) % every == 0) {
            int best_kills = 0;
            int best_points = 0;
            int best_cell = -1;
            for (int cell = 0; cell < sim::kCells; cell++) {
                Game copy = game;
                Action action;
                action.type = Action::Type::LaunchMeteor;
                action.x = cell % sim::kWidth;
                action.y = cell / sim::kWidth + game.origin_y();
                if (!copy.step(tick, action)) continue;
                for (int t = tick + 1; t < tick + sim::kMeteorDelay; t++) copy.step(t, Action{});
                int before = copy.score();
                copy.step(tick + sim::kMeteorDelay, Action{});
                int points = copy.score() - before;
                if (points > best_points) {
                    best_points = points;
                    best_kills = copy.kills_this_tick();
                    best_cell = cell;
                }
            }
            int counts[4] = {};
            for (int i = 0; i < game.dino_count(); i++) counts[static_cast<int>(game.dino(i).species)]++;
            overall_kills = std::max(overall_kills, best_kills);
            overall_points = std::max(overall_points, best_points);
            std::printf("%4d %5d %2d %2d %2d %2d   %10d  %11d  (%d,%d)\n", tick, game.dino_count(), counts[0], counts[1],
                        counts[2], counts[3], best_kills, best_points, best_cell % sim::kWidth,
                        best_cell / sim::kWidth);
        }
        game.step(tick, Action{});
    }
    std::printf("best blast anywhere: %d kills, %d points\n", overall_kills, overall_points);
    return 0;
}
