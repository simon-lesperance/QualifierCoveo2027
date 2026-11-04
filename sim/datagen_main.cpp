// Plays games with the live agent and writes training data for the value network.
//
//   sim_datagen <output folder> <games> [--biome name] [--budget n] [--every n] [--wait percent]
//               [--first n] [--threads n] [--radius n] [--depth n] [--net file]
//
// Samples look like MCTS leaves: the state after in-flight meteors land, with no new
// launches. Otherwise the net is evaluated on positions it never trained on, since
// this bot almost always has meteors in flight. --wait makes it skip that percentage
// of its moves, so the data also shows what keeping a meteor slot free is worth.
//
// Writes, per game, one file named <biome>-<seed>.bin:
//   int32 score after each tick 0..1000, int32 kills in each tick 0..1000,
//   then int32 sample count, and for each sample int32 tick + the feature bytes.
// Targets are computed at training time, so they can change without new games.

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "agent.hpp"
#include "features.hpp"

namespace {

using sim::kFeatureBytes;
using sim::kMaxTicks;

constexpr int kDefaultBudget = 3000;

void play(const std::string& biome, const std::string& seed, const std::string& path, const sim::AgentConfig& config,
          int every, int wait_percent, uint32_t noise_seed) {
    sim::Terrain terrain(seed, biome);
    sim::Game game(terrain, seed);
    sim::Agent agent(config);

    std::vector<int32_t> score(kMaxTicks + 1, 0);
    std::vector<int32_t> kills(kMaxTicks + 1, 0);
    std::vector<int32_t> ticks;
    std::vector<uint8_t> features;
    uint32_t noise = noise_seed;

    for (int tick = 1; tick <= kMaxTicks; tick++) {
        sim::Observation observation = game.observe(tick);
        if (tick % every == 0 && tick + config.horizon <= kMaxTicks) {
            sim::Game leaf = sim::Game::from_observation(observation, static_cast<uint32_t>(tick));
            for (int future = tick; future < tick + config.horizon; future++) leaf.step(future, sim::Action{});
            ticks.push_back(tick + config.horizon);
            features.resize(features.size() + kFeatureBytes);
            sim::encode_features(leaf, tick + config.horizon, features.data() + features.size() - kFeatureBytes);
        }
        sim::Action action = agent.act(observation);
        noise = noise * 1664525u + 1013904223u;
        if (static_cast<int>((noise >> 16) % 100) < wait_percent) action = sim::Action{};
        game.step(tick, action);
        score[tick] = game.score();
        kills[tick] = game.kills_this_tick();
    }

    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(score.data()), score.size() * sizeof(int32_t));
    file.write(reinterpret_cast<const char*>(kills.data()), kills.size() * sizeof(int32_t));
    int32_t count = static_cast<int32_t>(ticks.size());
    file.write(reinterpret_cast<const char*>(&count), sizeof(count));
    for (int i = 0; i < count; i++) {
        file.write(reinterpret_cast<const char*>(&ticks[i]), sizeof(int32_t));
        file.write(reinterpret_cast<const char*>(features.data() + i * kFeatureBytes), kFeatureBytes);
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: sim_datagen <output folder> <games> [options]\n");
        return 2;
    }
    std::string folder = argv[1];
    int games = std::atoi(argv[2]);
    std::string biome = "deep_crevasse";
    int every = 10;
    int first = 0;
    int threads = 11;
    int wait_percent = 0;

    sim::AgentConfig config;
    sim::ValueNet net;
    config.mcts = true;
    config.radius = 2;
    config.mcts_depth = 5;
    config.sim_budget = kDefaultBudget;
    for (int i = 3; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        int value = std::atoi(argv[i + 1]);
        if (name == "--biome") biome = argv[i + 1];
        else if (name == "--budget") config.sim_budget = value;
        else if (name == "--every") every = value;
        else if (name == "--wait") wait_percent = value;
        else if (name == "--first") first = value;
        else if (name == "--threads") threads = value;
        else if (name == "--radius") config.radius = value;
        else if (name == "--depth") config.mcts_depth = value;
        else if (name == "--net") {
            if (!net.load(argv[i + 1])) {
                std::fprintf(stderr, "could not load %s\n", argv[i + 1]);
                return 1;
            }
            config.net = &net;
        }
    }

    std::atomic<int> next{0};
    auto work = [&] {
        for (int job = next.fetch_add(1); job < games; job = next.fetch_add(1)) {
            std::string seed = "data-" + std::to_string(first + job);
            play(biome, seed, folder + "/" + biome + "-" + seed + ".bin", config, every, wait_percent,
                 static_cast<uint32_t>(first + job) * 2654435761u + 1);
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();
    std::printf("wrote %d games\n", games);
    return 0;
}
