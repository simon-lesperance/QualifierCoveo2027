// Is search wasting spawn pockets? Plays live games, and whenever the spawn area gets
// small, continues a copy of the true game 100 ticks each way and compares the points.
//
//   sim_pocket <games per biome> [--first n] [--threads n] [--budget n] [--limit n] [--net file]
//
// A hand-written rule here is only a measuring stick, not something to ship.

#include <atomic>
#include <cmath>
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

constexpr const char* kBiomes[2] = {"deep_crevasse", "volcanic_plains"};
constexpr int kHorizon = 100;

enum Rule { kSearchOnly, kAlways, kFewLeft, kInside, kRules };
constexpr const char* kRuleNames[kRules] = {"search only", "always fire at pocket", "fire when 2 or fewer alive", "fire when all alive are in the blast"};

struct Event {
    int biome;
    int points[kRules];
    int big_ticks[kRules];  // Ticks worth at least 3,000 points.
};

bool in_blast(const Game& game, int cell) {
    int row = cell / sim::kWidth;
    int column = cell % sim::kWidth;
    for (int i = 0; i < game.dino_count(); i++) {
        int dy = game.dino(i).cell / sim::kWidth - row;
        int dx = game.dino(i).cell % sim::kWidth - column;
        if (dx * dx + dy * dy > sim::kMeteorRadius * sim::kMeteorRadius) return false;
    }
    return true;
}

Action choose(Rule rule, sim::Agent& agent, const Game& game, int tick, int limit) {
    if (rule != kSearchOnly && game.meteor_count() < sim::kMaxMeteors) {
        int size = 0;
        int covered = 0;
        int cell = sim::best_pocket_tile(game, size, covered);
        if (size <= limit && covered * 2 >= size) {
            bool fire = rule == kAlways || (rule == kFewLeft && game.dino_count() <= 2) || (rule == kInside && in_blast(game, cell));
            if (fire) {
                Action shot;
                shot.type = Action::Type::LaunchMeteor;
                shot.x = cell % sim::kWidth;
                shot.y = cell / sim::kWidth + game.origin_y();
                return shot;
            }
        }
    }
    return agent.act(game.observe(tick));
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sim_pocket <games per biome> [options]\n");
        return 2;
    }
    int games = std::atoi(argv[1]);
    int first = 1000;
    int threads = 11;
    int limit = 30;
    sim::AgentConfig config;
    sim::ValueNet net;
    config.mcts = true;
    config.radius = 2;
    config.mcts_depth = 5;
    config.sim_budget = 7000;
    config.plan_waves = true;
    config.volcano_rank = true;
    config.volcanoes = 3;
    config.value_weight = 0.3;
    for (int i = 2; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        int value = std::atoi(argv[i + 1]);
        if (name == "--first") first = value;
        else if (name == "--threads") threads = value;
        else if (name == "--budget") config.sim_budget = value;
        else if (name == "--limit") limit = value;
        else if (name == "--net") {
            if (!net.load(argv[i + 1])) {
                std::fprintf(stderr, "could not load %s\n", argv[i + 1]);
                return 1;
            }
            config.net = &net;
        }
    }

    std::vector<Event> events;
    std::mutex events_lock;
    std::atomic<int> next{0};
    auto work = [&] {
        for (int job = next.fetch_add(1); job < games * 2; job = next.fetch_add(1)) {
            int biome = job % 2;
            std::string seed = "live-" + std::to_string(first + job / 2);
            sim::Terrain terrain(seed, kBiomes[biome]);
            Game game(terrain, seed);
            sim::Agent agent(config);
            int next_event = 1;
            for (int tick = 1; tick <= sim::kMaxTicks; tick++) {
                if (tick >= next_event && tick + kHorizon <= sim::kMaxTicks && game.spawn_tiles() <= limit) {
                    Event event{biome, {}, {}};
                    for (int rule = 0; rule < kRules; rule++) {
                        Game branch = game;
                        sim::Agent branch_agent = agent;
                        for (int future = tick; future < tick + kHorizon; future++) {
                            int before = branch.score();
                            branch.step(future, choose(static_cast<Rule>(rule), branch_agent, branch, future, limit));
                            if (branch.score() - before >= 3000) event.big_ticks[rule]++;
                        }
                        event.points[rule] = branch.score() - game.score();
                    }
                    std::lock_guard<std::mutex> guard(events_lock);
                    events.push_back(event);
                    next_event = tick + kHorizon;
                }
                game.step(tick, agent.act(game.observe(tick)));
            }
        }
    };
    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(work);
    for (std::thread& thread : pool) thread.join();

    for (int biome = 0; biome < 2; biome++) {
        std::printf("%s\n", kBiomes[biome]);
        for (int rule = 0; rule < kRules; rule++) {
            double sum = 0;
            double difference = 0;
            double squares = 0;
            int big = 0;
            int count = 0;
            for (const Event& event : events) {
                if (event.biome != biome) continue;
                double gap = event.points[rule] - event.points[kSearchOnly];
                sum += event.points[rule];
                difference += gap;
                squares += gap * gap;
                big += event.big_ticks[rule];
                count++;
            }
            if (count == 0) continue;
            double mean = difference / count;
            double error = count > 1 ? std::sqrt(std::max(0.0, squares / count - mean * mean) / (count - 1)) : 0;
            std::printf("  %-38s events %3d  points in 100 ticks %7.0f  vs search %+7.0f +- %5.0f  big ticks %.2f\n",
                        kRuleNames[rule], count, sum / count, mean, error, static_cast<double>(big) / count);
        }
    }
    return 0;
}
