// How often could a volcano shrink the spawn area, whatever the bot is doing?
// Plays live games, and every 10 ticks tries every mountain with lava alone.
//
//   sim_probe <biome> <first seed> <games> [--budget n]

#include <cstdio>
#include <cstdlib>
#include <string>

#include "agent.hpp"

int main(int argc, char** argv) {
    std::string biome = argv[1];
    int first = std::atoi(argv[2]);
    int games = std::atoi(argv[3]);
    sim::AgentConfig config;
    config.mcts = true;
    config.radius = 2;
    config.mcts_depth = 5;
    config.plan_waves = true;
    config.sim_budget = 1500;

    for (int g = 0; g < games; g++) {
        std::string seed = "live-" + std::to_string(first + g);
        sim::Terrain terrain(seed, biome);
        sim::Game game(terrain, seed);
        sim::Agent agent(config);
        int windows[3] = {};
        int busy[3] = {};
        for (int tick = 1; tick <= sim::kMaxTicks; tick++) {
            if (tick % 10 == 0) {
                sim::Game bare = game;
                bool active = game.volcano_count() > 0;
                bare.clear_dinosaurs();
                bare.clear_volcanoes();
                bare.hide(true, false, false, 0);
                double best = 0;
                for (int cell = 0; cell < sim::kCells; cell++) {
                    if (!(bare.flags(cell) & sim::kMountain)) continue;
                    sim::Action candidate;
                    candidate.type = sim::Action::Type::TriggerVolcano;
                    candidate.x = cell % sim::kWidth;
                    candidate.y = cell / sim::kWidth + bare.origin_y();
                    best = std::max(best, sim::volcano_outlook(bare, tick, candidate, 40, 60));
                }
                for (int level = 0; level < 3; level++) {
                    double bar = level == 0 ? 300 : level == 1 ? 1000 : 3000;
                    if (best >= bar) {
                        windows[level]++;
                        busy[level] += active;
                    }
                }
            }
            game.step(tick, agent.act(game.observe(tick)));
        }
        std::printf("%s score %d, of 100 checks: outlook>=300 %d (volcano busy %d), >=1000 %d (%d), >=3000 %d (%d)\n",
                    seed.c_str(), game.score(), windows[0], busy[0], windows[1], busy[1], windows[2], busy[2]);
        std::fflush(stdout);
    }
    return 0;
}
