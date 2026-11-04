// Plays games with the live agent and records where its search spent its visits,
// to teach a policy network. Inputs are the position as the bot saw it.
//
//   sim_policygen <output folder> <games> [--biome name] [--budget n] [--every n]
//                 [--first n] [--threads n] [--net file] [--value-weight x]
//
// Writes, per game, one file named <biome>-<seed>.bin:
//   int32 score after each tick 0..1000, int32 sample count, and for each sample
//   int32 tick, the feature bytes, int32 action count, then count pairs of
//   int32 action code and int32 visits.
// Action codes: 0 is nothing, 1 + cell a meteor, 401 + cell a volcano.

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

using sim::kCells;
using sim::kFeatureBytes;
using sim::kMaxTicks;
using sim::kWidth;

int action_code(const sim::Action& action, int origin) {
    if (action.type == sim::Action::Type::None) return 0;
    int cell = (action.y - origin) * kWidth + action.x;
    return (action.type == sim::Action::Type::LaunchMeteor ? 1 : 1 + kCells) + cell;
}

void play(const std::string& biome, const std::string& seed, const std::string& path, const sim::AgentConfig& config, int every) {
    sim::Terrain terrain(seed, biome);
    sim::Game game(terrain, seed);
    sim::Agent agent(config);

    std::vector<int32_t> score(kMaxTicks + 1, 0);
    std::vector<char> samples;
    int32_t count = 0;
    auto put = [&](const void* data, size_t size) {
        const char* bytes = static_cast<const char*>(data);
        samples.insert(samples.end(), bytes, bytes + size);
    };

    for (int tick = 1; tick <= kMaxTicks; tick++) {
        sim::Observation observation = game.observe(tick);
        sim::Action action = agent.act(observation);
        if (tick % every == 0 && !agent.root_visits().empty()) {
            uint8_t features[kFeatureBytes];
            sim::encode_features(game, tick, features);
            int32_t size = static_cast<int32_t>(agent.root_visits().size());
            put(&tick, sizeof(tick));
            put(features, kFeatureBytes);
            put(&size, sizeof(size));
            for (const auto& visit : agent.root_visits()) {
                int32_t pair[2] = {action_code(visit.action, game.origin_y()), visit.visits};
                put(pair, sizeof(pair));
            }
            count++;
        }
        game.step(tick, action);
        score[tick] = game.score();
    }

    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(score.data()), score.size() * sizeof(int32_t));
    file.write(reinterpret_cast<const char*>(&count), sizeof(count));
    file.write(samples.data(), samples.size());
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: sim_policygen <output folder> <games> [options]\n");
        return 2;
    }
    std::string folder = argv[1];
    int games = std::atoi(argv[2]);
    std::string biome = "deep_crevasse";
    int every = 3;
    int first = 0;
    int threads = 11;

    sim::AgentConfig config;
    sim::ValueNet net;
    config.mcts = true;
    config.radius = 2;
    config.mcts_depth = 5;
    config.sim_budget = 3000;
    config.value_weight = 0.3;
    for (int i = 3; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        int value = std::atoi(argv[i + 1]);
        if (name == "--biome") biome = argv[i + 1];
        else if (name == "--budget") config.sim_budget = value;
        else if (name == "--every") every = value;
        else if (name == "--value-weight") config.value_weight = std::atof(argv[i + 1]);
        else if (name == "--first") first = value;
        else if (name == "--threads") threads = value;
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
            std::string seed = "policy-" + std::to_string(first + job);
            play(biome, seed, folder + "/" + biome + "-" + seed + ".bin", config, every);
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();
    std::printf("wrote %d games\n", games);
    return 0;
}
