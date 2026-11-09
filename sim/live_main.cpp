// Tests the live agent on full games, seeing only what the server would send.
//
//   sim_live <games per biome> [--budget n] [--beam n] [--radius n] [--horizon n]
//                              [--volcanoes n] [--samples n] [--threads n] [--first n]
//                              [--reveal-rows 1] [--reveal-rng 1] [--reveal-waves 1]
//                              [--shield 1] [--grid 1]
//                              [--mcts 1] [--exploration x] [--depth n] [--widening x]
//                              [--net file] [--value-weight x] [--net-cost n] [--biome name]
//                              [--policy file] [--tree-policy file] [--puct x] [--log folder]

#include <algorithm>
#include <atomic>
#include <fstream>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

#include "agent.hpp"
#include "features.hpp"

namespace {

constexpr const char* kBiomes[3] = {"deep_crevasse", "mountain_range", "volcanic_plains"};

struct Result {
    int score = 0;
    int kills = 0;
    int spawned = 0;
};

// log_folder, if set, gets one csv per game: tick, score, kills, alive, lava tiles, spawn tiles.
std::string dump_folder;  // If set, gets one file of search records per game.
// If set, a second agent with this budget and oracle_arms halving actions searches every
// oracle_every ticks, and <log>/<game>-oracle.csv compares its choice with the bot's.
int oracle_budget = 0;
std::string oracle_dump;  // If set, gets one file of the oracle's search records per game.
int oracle_every = 10;
int oracle_arms = 48;
std::vector<std::string> seed_list;  // --seed-list: play these seeds (for example server ones) in place of live-N.
std::string rng_tag;  // Keeps a seed's map but changes its dice, to separate the two.
int frame_every = 0;
int frame_first = 0;
int frame_last = 0;

Result play(const char* biome, const std::string& seed, const sim::AgentConfig& config, const std::string& log_folder) {
    sim::Terrain terrain(seed, biome);
    sim::Game game(terrain, seed + rng_tag);
    sim::Agent agent(config);
    sim::AgentConfig oracle_config = config;
    oracle_config.sim_budget = oracle_budget;
    oracle_config.root_halving = oracle_arms;
    sim::Agent oracle(oracle_config);
    bool oracle_warm = false;
    std::FILE* oracle_records = nullptr;
    if (oracle_budget > 0 && !oracle_dump.empty()) {
        oracle_records = std::fopen((oracle_dump + "/" + biome + "-" + seed + ".bin").c_str(), "wb");
    }
    std::FILE* oracle_log = nullptr;
    if (oracle_budget > 0 && !log_folder.empty()) {
        oracle_log = std::fopen((log_folder + "/" + biome + "-" + seed + "-oracle.csv").c_str(), "w");
    }
    bool experiment = config.reveal_rows || config.reveal_rng || config.reveal_waves;
    int last_id = 0;
    std::FILE* log = nullptr;
    if (!log_folder.empty()) log = std::fopen((log_folder + "/" + biome + "-" + seed + ".csv").c_str(), "w");
    std::FILE* dump = nullptr;
    if (!dump_folder.empty()) dump = std::fopen((dump_folder + "/" + biome + "-" + seed + ".bin").c_str(), "wb");
    for (int tick = 1; tick <= sim::kMaxTicks; tick++) {
        sim::Observation observation = game.observe(tick);
        game.step(tick, agent.act(observation, experiment ? &game : nullptr));
        const sim::Agent::SearchRecord& record = agent.last_record();
        if ((oracle_log != nullptr || oracle_records != nullptr) && record.tick == tick && tick % oracle_every == 0) {
            if (!oracle_warm) oracle.act(observation, nullptr);  // Sizes its halving rounds.
            oracle_warm = true;
            oracle.act(observation, nullptr);
            const sim::Agent::SearchRecord& big = oracle.last_record();
            // tick, bot's choice, oracle's choice, the oracle choice's rank in the policy's order,
            // and the visits the oracle gave its choice and the bot's choice.
            int rank = -1, big_visits = 0, mine_visits = 0;
            for (size_t i = 0; i < big.arms.size(); i++) {
                if (big.arms[i].first == big.chosen) { rank = static_cast<int>(i); big_visits = big.arms[i].second; }
                if (big.arms[i].first == record.chosen) mine_visits = big.arms[i].second;
            }
            if (big.tick == tick && oracle_records != nullptr && big.features.size() == sim::kPolicyBytes) {
                // Features, then int16 tick, chosen code, arm count, and 48 (code, visits) pairs.
                constexpr int kArms = 48;
                int16_t header[3] = {static_cast<int16_t>(tick), static_cast<int16_t>(big.chosen),
                                     static_cast<int16_t>(std::min<size_t>(kArms, big.arms.size()))};
                int16_t arms[2 * kArms] = {};
                for (size_t i = 0; i < big.arms.size() && i < kArms; i++) {
                    arms[2 * i] = static_cast<int16_t>(big.arms[i].first);
                    arms[2 * i + 1] = static_cast<int16_t>(std::min(30000, big.arms[i].second));
                }
                std::fwrite(big.features.data(), 1, big.features.size(), oracle_records);
                std::fwrite(header, sizeof(header), 1, oracle_records);
                std::fwrite(arms, sizeof(arms), 1, oracle_records);
            }
            if (big.tick == tick && oracle_log != nullptr) {
                std::fprintf(oracle_log, "%d,%d,%d,%d,%d,%d\n", tick, record.chosen, big.chosen, rank, big_visits, mine_visits);
            }
        }
        if (dump != nullptr && record.tick == tick && record.features.size() == sim::kPolicyBytes) {
            // Features, then int16 tick, chosen code, arm count, and 16 (code, visits) pairs.
            int16_t header[3] = {static_cast<int16_t>(tick), static_cast<int16_t>(record.chosen),
                                 static_cast<int16_t>(std::min<size_t>(16, record.arms.size()))};
            int16_t arms[32] = {};
            for (size_t i = 0; i < record.arms.size() && i < 16; i++) {
                arms[2 * i] = static_cast<int16_t>(record.arms[i].first);
                arms[2 * i + 1] = static_cast<int16_t>(std::min(30000, record.arms[i].second));
            }
            std::fwrite(record.features.data(), 1, record.features.size(), dump);
            std::fwrite(header, sizeof(header), 1, dump);
            std::fwrite(arms, sizeof(arms), 1, dump);
        }
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
            std::fprintf(log, "%d,%d,%d,%d,%d,%d,%d\n", tick, game.score(), game.total_kills(), game.dino_count(), lava,
                         game.spawn_tiles(), agent.last_iterations());
        }
    }
    if (log != nullptr) std::fclose(log);
    if (dump != nullptr) std::fclose(dump);
    if (oracle_log != nullptr) std::fclose(oracle_log);
    if (oracle_records != nullptr) std::fclose(oracle_records);
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
    std::string tree_policy_path;
    std::string log_folder;
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
        else if (name == "--tree-policy") tree_policy_path = argv[i + 1];
        else if (name == "--root-halving") config.root_halving = value;
        else if (name == "--halving-prior") config.halving_prior = std::atof(argv[i + 1]);
        else if (name == "--halving-crn") config.halving_crn = value != 0;
        else if (name == "--tree-reuse") config.tree_reuse = value;
        else if (name == "--halving-skew") config.halving_skew = std::atof(argv[i + 1]);
        else if (name == "--dump") {
            dump_folder = argv[i + 1];
            config.record_search = true;
        }
        else if (name == "--halving-max") config.halving_max = std::atof(argv[i + 1]);
        else if (name == "--tree-puct") config.tree_puct = std::atof(argv[i + 1]);
        else if (name == "--policy-only") config.policy_only = value != 0;
        else if (name == "--policy-depth") config.policy_depth = value;
        else if (name == "--policy-tree-cost") config.policy_tree_cost = value;
        else if (name == "--policy-tree-visits") config.policy_tree_visits = value;
        else if (name == "--policy-deep-visits") config.policy_deep_visits = value;
        else if (name == "--oracle-budget") {
            oracle_budget = value;
            config.record_search = true;
        }
        else if (name == "--oracle-every") oracle_every = value;
        else if (name == "--oracle-dump") oracle_dump = argv[i + 1];
        else if (name == "--oracle-arms") oracle_arms = value;
        else if (name == "--log") log_folder = argv[i + 1];
        else if (name == "--rng-tag") rng_tag = argv[i + 1];
        else if (name == "--seed-list") {
            std::ifstream list(argv[i + 1]);
            for (std::string line; std::getline(list, line);) {
                while (!line.empty() && (line.back() == 13 || line.back() == ' ')) line.pop_back();
                if (!line.empty()) seed_list.push_back(line);
            }
        }
        else if (name == "--dice") config.dice = value;
        else if (name == "--rave-k") config.rave_k = std::atof(argv[i + 1]);
        else if (name == "--rave-order") config.rave_order = value != 0;
        else if (name == "--frame-every") frame_every = value;
        else if (name == "--frame-first") frame_first = value;
        else if (name == "--frame-last") frame_last = value;
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

    sim::PolicyNet policy;
    sim::PolicyNet tree_policy;
    if (!tree_policy_path.empty() && !tree_policy.load(tree_policy_path.c_str())) {
        std::fprintf(stderr, "could not load %s\n", tree_policy_path.c_str());
        return 1;
    }
    if (!policy_path.empty() && !policy.load(policy_path.c_str())) {
        std::fprintf(stderr, "could not load %s\n", policy_path.c_str());
        return 1;
    }

    std::vector<Result> results(games * 3);
    std::atomic<int> next{0};
    auto work = [&] {
        sim::PolicyNet own_policy = policy;  // It keeps scratch space, so each thread has its own.
        sim::AgentConfig own_config = config;
        if (!policy_path.empty()) own_config.policy = &own_policy;
        sim::PolicyNet own_tree_policy = tree_policy;
        if (!tree_policy_path.empty()) own_config.tree_policy = &own_tree_policy;
        for (int job = next.fetch_add(1); job < games * 3; job = next.fetch_add(1)) {
            if (!biome_filter.empty() && biome_filter != kBiomes[job % 3]) continue;
            const int index = first + job / 3;
            if (!seed_list.empty() && index >= static_cast<int>(seed_list.size())) continue;
            const std::string seed = seed_list.empty() ? "live-" + std::to_string(index) : seed_list[index];
            results[job] = play(kBiomes[job % 3], seed, own_config, log_folder);
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();

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
