// Can meteors steer lava? From positions in real games, with lava alone, finds the best
// volcano, then greedily adds meteors that raise the pocket value of the resulting flood.
// Uses true future rows and ignores dinosaurs, so this is an upper limit, and it doesn't
// count what those meteors could have earned instead.
//
//   sim_carve <games per biome> [--first n] [--threads n] [--lookahead n] [--volcanoes n]

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "agent.hpp"

namespace {

using sim::Action;
using sim::Game;

constexpr const char* kBiomes[3] = {"deep_crevasse", "volcanic_plains", "mountain_range"};
constexpr int kMeteorOffsets[6] = {1, 2, 3, 6, 7, 8};  // The fastest launch pattern with 3 in flight.
constexpr int kSteps = 4;

const char* const kStepNames[kSteps] = {"no volcano", "best volcano", "volcano + 3 meteors", "volcano + 6 meteors"};

struct Sample {
    int biome;
    double value[kSteps];
};

using Schedule = std::vector<std::pair<int, Action>>;

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sim_carve <games per biome> [options]\n");
        return 2;
    }
    int games = std::atoi(argv[1]);
    int first = 1000;
    int threads = 11;
    int lookahead = 40;
    int top_volcanoes = 3;
    for (int i = 2; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        int value = std::atoi(argv[i + 1]);
        if (name == "--first") first = value;
        else if (name == "--threads") threads = value;
        else if (name == "--lookahead") lookahead = value;
        else if (name == "--volcanoes") top_volcanoes = value;
    }

    sim::AgentConfig config;
    config.mcts = true;
    config.radius = 2;
    config.mcts_depth = 5;
    config.plan_waves = true;
    config.sim_budget = 1500;

    std::vector<Sample> samples;
    std::mutex samples_lock;
    std::atomic<int> next{0};
    auto work = [&] {
        for (int job = next.fetch_add(1); job < games * 3; job = next.fetch_add(1)) {
            int biome = job % 3;
            std::string seed = "live-" + std::to_string(first + job / 3);
            sim::Terrain terrain(seed, kBiomes[biome]);
            Game game(terrain, seed);
            sim::Agent agent(config);
            for (int tick = 1; tick <= sim::kMaxTicks; tick++) {
                if (tick % 100 == 50 && tick + lookahead <= sim::kMaxTicks) {
                    Game bare = game;
                    bare.clear_dinosaurs();
                    bare.clear_volcanoes();
                    constexpr double kSlope = 1;
                    auto score = [&](const Schedule& plan) { return sim::flood_outlook(bare, tick, plan, lookahead, 60); };
                    auto guide = [&](const Schedule& plan) { return sim::flood_outlook(bare, tick, plan, lookahead, 60, kSlope); };

                    Sample sample{biome, {}};
                    sample.value[0] = score({});
                    std::vector<std::pair<double, Action>> volcanoes;
                    for (int cell = 0; cell < sim::kCells; cell++) {
                        if (!(bare.flags(cell) & sim::kMountain)) continue;
                        Action action;
                        action.type = Action::Type::TriggerVolcano;
                        action.x = cell % sim::kWidth;
                        action.y = cell / sim::kWidth + bare.origin_y();
                        volcanoes.push_back({guide({{0, action}}), action});
                        sample.value[1] = std::max(sample.value[1], score({{0, action}}));
                    }
                    std::stable_sort(volcanoes.begin(), volcanoes.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
                    if (volcanoes.empty()) continue;
                    sample.value[2] = sample.value[1];
                    sample.value[3] = sample.value[1];

                    for (int v = 0; v < std::min<int>(top_volcanoes, static_cast<int>(volcanoes.size())); v++) {
                        Schedule plan = {{0, volcanoes[v].second}};
                        double current = volcanoes[v].first;
                        for (int slot = 0; slot < 6; slot++) {
                            double best = current;
                            Action best_shot;
                            for (int cell = 0; cell < sim::kCells; cell++) {
                                if (bare.flags(cell) & sim::kImpassable) continue;
                                Action shot;
                                shot.type = Action::Type::LaunchMeteor;
                                shot.x = cell % sim::kWidth;
                                shot.y = cell / sim::kWidth + bare.origin_y();
                                Schedule trial = plan;
                                trial.push_back({kMeteorOffsets[slot], shot});
                                double value = guide(trial);
                                if (value > best) {
                                    best = value;
                                    best_shot = shot;
                                }
                            }
                            if (best_shot.type == Action::Type::None) break;
                            plan.push_back({kMeteorOffsets[slot], best_shot});
                            current = best;
                            double reached = score(plan);
                            if (slot < 3) sample.value[2] = std::max(sample.value[2], reached);
                            sample.value[3] = std::max(sample.value[3], reached);
                        }
                    }
                    std::lock_guard<std::mutex> guard(samples_lock);
                    samples.push_back(sample);
                }
                game.step(tick, agent.act(game.observe(tick)));
            }
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();

    for (int biome = 0; biome < 3; biome++) {
        std::printf("%s\n", kBiomes[biome]);
        for (int step = 0; step < kSteps; step++) {
            double sum = 0;
            int count = 0;
            int good = 0;
            int great = 0;
            for (const Sample& sample : samples) {
                if (sample.biome != biome) continue;
                sum += sample.value[step];
                good += sample.value[step] >= 1000;
                great += sample.value[step] >= 3000;
                count++;
            }
            if (count == 0) continue;
            std::printf("  %-22s positions %4d  mean outlook %6.0f  over 1000: %4.1f%%  over 3000: %4.1f%%\n",
                        kStepNames[step], count, sum / count, 100.0 * good / count, 100.0 * great / count);
        }
    }
    return 0;
}
