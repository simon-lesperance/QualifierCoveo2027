// Tests the live agent on full games, seeing only what the server would send.
//
//   sim_live <games per biome> [--budget n] [--beam n] [--radius n] [--horizon n]
//                              [--volcanoes n] [--samples n] [--threads n] [--first n]
//                              [--reveal-rows 1] [--reveal-rng 1] [--reveal-waves 1]
//                              [--shield 1] [--grid 1]
//                              [--mcts 1] [--exploration x] [--depth n] [--widening x]
//                              [--net file] [--value-weight x] [--net-cost n] [--biome name]
//                              [--policy file] [--puct x] [--log folder] [--csv file]
//                              [--optimism x]

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "agent.hpp"

namespace {

constexpr const char* kBiomes[3] = {"deep_crevasse", "mountain_range", "volcanic_plains"};

struct Result {
    int score = 0;
    int kills = 0;
    int spawned = 0;
};

// log_folder, if set, gets one csv per game: tick, score, kills, alive, lava tiles, spawn tiles.
std::string rng_tag;  // Keeps a seed's map but changes its dice, to separate the two.
int frame_every = 0;
int frame_first = 0;
int frame_last = 0;

Result play(const char* biome, const std::string& seed, const sim::AgentConfig& config, const std::string& log_folder) {
    sim::Terrain terrain(seed, biome);
    sim::Game game(terrain, seed + rng_tag);
    sim::Agent agent(config);
    bool experiment = config.reveal_rows || config.reveal_rng || config.reveal_waves;
    int last_id = 0;
    std::FILE* log = nullptr;
    if (!log_folder.empty()) log = std::fopen((log_folder + "/" + biome + "-" + seed + ".csv").c_str(), "w");
    for (int tick = 1; tick <= sim::kMaxTicks; tick++) {
        sim::Observation observation = game.observe(tick);
        game.step(tick, agent.act(observation, experiment ? &game : nullptr));
        for (int i = 0; i < game.dino_count(); i++) last_id = std::max(last_id, game.dino(i).id);
        if (frame_every > 0 && tick >= frame_first && tick <= frame_last && tick % frame_every == 0) {
            std::printf("tick %d score %d spawn %d origin %d", tick, game.score(), game.spawn_tiles(), game.origin_y());
            for (int i = 0; i < game.volcano_count(); i++) std::printf(" volcano (%d,%d)", game.volcano(i).x, game.volcano(i).y - game.origin_y());
            std::printf("\n");
            uint8_t mask[sim::kCells];
            game.spawn_mask(mask);
            for (int row = 0; row < sim::kHeight; row++) {
                for (int column = 0; column < sim::kWidth; column++) {
                    int cell = row * sim::kWidth + column;
                    char symbol = static_cast<char>('a' + game.elevation(cell));
                    if (game.flags(cell) & sim::kImpassable) symbol = '#';
                    else if (game.flags(cell) & sim::kLava) symbol = '~';
                    else if (game.flags(cell) & sim::kMountain) symbol = '^';
                    else if (mask[cell]) symbol = static_cast<char>('A' + game.elevation(cell));
                    for (int i = 0; i < game.dino_count(); i++) {
                        if (game.dino(i).cell == cell) symbol = '@';
                    }
                    std::putchar(symbol);
                }
                std::putchar('\n');
            }
        }
        if (log != nullptr) {
            int lava = 0;
            for (int cell = 0; cell < sim::kCells; cell++) lava += (game.flags(cell) & sim::kLava) != 0;
            std::fprintf(log, "%d,%d,%d,%d,%d,%d\n", tick, game.score(), game.total_kills(), game.dino_count(), lava, game.spawn_tiles());
        }
    }
    if (log != nullptr) std::fclose(log);
    return {game.score(), game.total_kills(), last_id};
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sim_live <games per biome> [options]\n");
        return 2;
    }
    int games = std::atoi(argv[1]);
    int threads = 11;
    int first = 0;
    std::string biome_filter;
    sim::AgentConfig config;
    sim::ValueNet net;
    std::string policy_path;
    std::string log_folder;
    std::string csv_path;
    for (int i = 2; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        int value = std::atoi(argv[i + 1]);
        if (name == "--budget") config.sim_budget = value;
        else if (name == "--beam") config.beam = value;
        else if (name == "--radius") config.radius = value;
        else if (name == "--horizon") config.horizon = value;
        else if (name == "--volcanoes") config.volcanoes = value;
        else if (name == "--samples") config.samples = value;
        else if (name == "--threads") threads = value;
        else if (name == "--first") first = value;
        else if (name == "--value-weight") config.value_weight = std::atof(argv[i + 1]);
        else if (name == "--optimism") config.optimism = std::atof(argv[i + 1]);
        else if (name == "--net-cost") config.net_cost = value;
        else if (name == "--puct") config.puct = std::atof(argv[i + 1]);
        else if (name == "--plan-waves") config.plan_waves = value != 0;
        else if (name == "--pocket-limit") config.pocket_limit = value;
        else if (name == "--volcano-plan") config.volcano_plan = value != 0;
        else if (name == "--volcano-rank") config.volcano_rank = value != 0;
        else if (name == "--volcano-rank-ticks") config.volcano_rank_ticks = value;
        else if (name == "--flood-weight") config.flood_weight = std::atof(argv[i + 1]);
        else if (name == "--volcano-lookahead") config.volcano_lookahead = value;
        else if (name == "--volcano-threshold") config.volcano_threshold = std::atof(argv[i + 1]);
        else if (name == "--pocket-weight") config.pocket_weight = std::atof(argv[i + 1]);
        else if (name == "--pocket-targets") config.pocket_targets = value;
        else if (name == "--policy-cost") config.policy_cost = value;
        else if (name == "--policy-targets") config.policy_targets = value;
        else if (name == "--prior-floor") config.prior_floor = std::atof(argv[i + 1]);
        else if (name == "--policy") policy_path = argv[i + 1];
        else if (name == "--log") log_folder = argv[i + 1];
        else if (name == "--csv") csv_path = argv[i + 1];
        else if (name == "--rng-tag") rng_tag = argv[i + 1];
        else if (name == "--dice") config.dice = value;
        else if (name == "--rave-k") config.rave_k = std::atof(argv[i + 1]);
        else if (name == "--rave-order") config.rave_order = value != 0;
        else if (name == "--frame-every") frame_every = value;
        else if (name == "--frame-first") frame_first = value;
        else if (name == "--frame-last") frame_last = value;
        else if (name == "--aim-current") config.aim_current = value != 0;
        else if (name == "--aim-predicted") config.aim_predicted = value != 0;
        else if (name == "--volcano-near") config.volcano_near_dinos = value != 0;
        else if (name == "--biome") biome_filter = argv[i + 1];
        else if (name == "--net") {
            if (!net.load(argv[i + 1])) {
                std::fprintf(stderr, "could not load %s\n", argv[i + 1]);
                return 1;
            }
            config.net = &net;
        }
        else if (name == "--mcts") config.mcts = value != 0;
        else if (name == "--exploration") config.exploration = std::atof(argv[i + 1]);
        else if (name == "--depth") config.mcts_depth = value;
        else if (name == "--widening") config.widening = std::atof(argv[i + 1]);
        else if (name == "--reveal-rows") config.reveal_rows = value != 0;
        else if (name == "--reveal-rng") config.reveal_rng = value != 0;
        else if (name == "--reveal-waves") config.reveal_waves = value != 0;
        else if (name == "--shield") config.shield = value != 0;
        else if (name == "--grid") config.grid = value != 0;
    }

    if (!(config.optimism >= 0 && config.optimism <= 1)) {
        std::fprintf(stderr, "--optimism must be between 0 and 1\n");
        return 2;
    }

    sim::PolicyNet policy;
    if (!policy_path.empty() && !policy.load(policy_path.c_str())) {
        std::fprintf(stderr, "could not load %s\n", policy_path.c_str());
        return 1;
    }

    std::ofstream csv;
    if (!csv_path.empty()) {
        csv.open(csv_path);
        if (!csv) {
            std::fprintf(stderr, "could not open %s\n", csv_path.c_str());
            return 1;
        }
        csv << "biome,seed,score,kills,spawned\n";
    }

    std::vector<Result> results(games * 3);
    std::atomic<int> next{0};
    auto work = [&] {
        sim::PolicyNet own_policy = policy;  // It keeps scratch space, so each thread has its own.
        sim::AgentConfig own_config = config;
        if (!policy_path.empty()) own_config.policy = &own_policy;
        for (int job = next.fetch_add(1); job < games * 3; job = next.fetch_add(1)) {
            if (!biome_filter.empty() && biome_filter != kBiomes[job % 3]) continue;
            results[job] = play(kBiomes[job % 3], "live-" + std::to_string(first + job / 3), own_config, log_folder);
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();

    if (csv) {
        for (int job = 0; job < games * 3; job++) {
            const char* biome = kBiomes[job % 3];
            if (!biome_filter.empty() && biome_filter != biome) continue;
            csv << biome << ",live-" << first + job / 3 << ',' << results[job].score << ','
                << results[job].kills << ',' << results[job].spawned << '\n';
        }
    }

    long long overall = 0;
    for (int biome = 0; biome < 3; biome++) {
        long long score = 0, kills = 0, spawned = 0;
        int best = 0;
        for (int i = biome; i < games * 3; i += 3) {
            score += results[i].score;
            kills += results[i].kills;
            spawned += results[i].spawned;
            best = std::max(best, results[i].score);
        }
        overall += score;
        std::printf("%-16s mean %7lld  best %7d  kills %5lld  spawned %5lld\n", kBiomes[biome], score / games, best,
                    kills / games, spawned / games);
    }
    std::printf("overall mean %lld\n", overall / (games * 3));
    return 0;
}
