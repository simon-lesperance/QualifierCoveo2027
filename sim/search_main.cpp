// Beam search with full knowledge of the seed, to estimate how much a map can score.
// The best plan found is a lower bound on the map's maximum, and a live bot, which
// can't see the seed, won't match it.
//
//   sim_search <biome> <seed> <width> <plan file> [--radius n] [--cluster x] [--spread x]
//                                                 [--volcanoes n] [--threads n] [--shield 1]
//                                                 [--grid-step n] [--net file] [--net-weight x]
//                                                 [--pocket-value x] [--flood x]
//                                                 [--data file] [--samples n] [--every n]
//                                                 [--label-width n] [--label-step n]
//
// The plan file uses the same format as sim_replay's actions.
//
// --data also writes training data for the value net. Every --every ticks it takes
// --samples candidates, half from the states the beam would keep and half from all of
// them, each after its meteors land. A narrow beam with the same foresight then plays on
// from each one, so good and bad positions are judged by the same player. Each record
// is int32 tick, int32 points over the next 100 ticks, then the feature bytes.

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

#include "agent.hpp"
#include "features.hpp"
#include "game.hpp"
#include "value_net.hpp"

namespace {

using sim::Action;
using sim::Game;

constexpr int kRolloutTicks = sim::kMeteorDelay;

struct Options {
    int radius = 3;
    int volcanoes = 6;
    int threads = 12;
    double cluster = 30;  // Reward for dinosaurs a single blast could reach.
    double spread = 0;    // Penalty per living dinosaur, to favour clearing the map.
    bool shield = false;  // Rule out spawn-tick kills, which need to foresee spawns.
    double pocket = 0;    // Reward per tile removed from where waves can spawn.
    int grid_step = 3;    // Spacing of the empty-ground targets. 1 offers every tile.
    double pocket_value = 0;  // Share of a meteor's expected points on the next wave.
    double flood = 0;     // Share of the same value, averaged over 40 ticks of lava alone.
    const sim::ValueNet* net = nullptr;
    double net_weight = 0;
    std::string data;     // Where to write training data, if anywhere.
    int samples = 10;
    int every = 10;
    int label_width = 10;
    int label_step = 3;
};

constexpr int kPocketLimit = 60;
constexpr int kFloodLookahead = 40;
constexpr int kLabelWindow = 100;

struct Candidate {
    double value;
    uint64_t hash;
    int parent;
    Action action;
};

struct Link {
    int parent;
    Action action;
};

void list_actions(const Game& game, const Options& options, std::vector<Action>& actions) {
    actions.clear();
    actions.push_back(Action{});
    int origin = game.origin_y();

    if (game.meteor_count() < sim::kMaxMeteors) {
        bool wanted[sim::kCells] = {};
        for (int i = 0; i < game.dino_count(); i++) {
            int row = game.dino(i).cell / sim::kWidth;
            int column = game.dino(i).cell % sim::kWidth;
            for (int dy = -options.radius; dy <= options.radius; dy++) {
                int reach = options.radius - std::abs(dy);
                for (int dx = -reach; dx <= reach; dx++) {
                    if (row + dy >= 0 && row + dy < sim::kHeight && column + dx >= 0 && column + dx < sim::kWidth) {
                        wanted[(row + dy) * sim::kWidth + column + dx] = true;
                    }
                }
            }
        }
        // A sparse grid lets it aim at empty ground, such as where a wave will spawn.
        for (int row = 1 % options.grid_step; row < sim::kHeight; row += options.grid_step) {
            for (int column = 1 % options.grid_step; column < sim::kWidth; column += options.grid_step) {
                wanted[row * sim::kWidth + column] = true;
            }
        }
        for (int cell = 0; cell < sim::kCells; cell++) {
            if (!wanted[cell]) continue;
            Action action;
            action.type = Action::Type::LaunchMeteor;
            action.x = cell % sim::kWidth;
            action.y = cell / sim::kWidth + origin;
            actions.push_back(action);
        }
    }

    if (game.volcano_count() < sim::kMaxVolcanoes && options.volcanoes > 0) {
        int mountains[sim::kCells];
        int count = 0;
        for (int cell = 0; cell < sim::kCells; cell++) {
            if (game.flags(cell) & sim::kMountain) mountains[count++] = cell;
        }
        int stride = std::max(1, count / options.volcanoes);
        for (int i = 0; i < count; i += stride) {
            Action action;
            action.type = Action::Type::TriggerVolcano;
            action.x = mountains[i] % sim::kWidth;
            action.y = mountains[i] / sim::kWidth + origin;
            actions.push_back(action);
        }
    }
}

// The most dinosaurs any one blast could cover right now.
int best_blast(const Game& game) {
    int best = 0;
    constexpr int kSquared = sim::kMeteorRadius * sim::kMeteorRadius;
    for (int row = 0; row < sim::kHeight; row++) {
        for (int column = 0; column < sim::kWidth; column++) {
            int count = 0;
            for (int i = 0; i < game.dino_count(); i++) {
                int dy = game.dino(i).cell / sim::kWidth - row;
                int dx = game.dino(i).cell % sim::kWidth - column;
                if (dx * dx + dy * dy <= kSquared) count++;
            }
            best = std::max(best, count);
        }
    }
    return best;
}

// Points a meteor timed for a new wave's first tick could expect, given the spawn area.
double next_pocket(const Game& game) {
    int size = 0;
    int covered = 0;
    sim::best_pocket_tile(game, size, covered);
    if (size == 0 || size > kPocketLimit) return 0;
    double kills = std::min<double>(covered, static_cast<double>(sim::kDinosPerWave) * covered / std::max(size, sim::kDinosPerWave));
    constexpr double kFreshValue = 164;
    return kFreshValue * kills * (1 + 0.5 * (kills - 1));
}

double evaluate(const Game& rolled, int next_turn, const Options& options) {
    int blast = options.cluster > 0 ? best_blast(rolled) : 0;
    double value = rolled.score() + options.cluster * blast * blast - options.spread * rolled.dino_count();
    if (options.pocket > 0) value += options.pocket * (sim::kCells - rolled.spawn_tiles());
    if (options.pocket_value > 0) value += options.pocket_value * next_pocket(rolled);
    if (options.net != nullptr) {
        constexpr double kWindow = 100;  // The net predicts points over this many ticks.
        uint8_t features[sim::kFeatureBytes];
        sim::encode_features(rolled, next_turn, features);
        double remaining = std::min(1.0, (sim::kMaxTicks + 1 - next_turn) / kWindow);
        value += options.net_weight * 1000 * remaining * options.net->evaluate(features);
    }
    return value;
}

uint64_t state_hash(const Game& game) {
    uint64_t hash = 1469598103934665603ull;
    auto mix = [&](uint64_t value) {
        hash ^= value;
        hash *= 1099511628211ull;
    };
    mix(static_cast<uint64_t>(game.score()));
    for (int i = 0; i < game.volcano_count(); i++) mix(static_cast<uint64_t>(game.volcano(i).x) * 100000 + game.volcano(i).y);
    for (int i = 0; i < game.dino_count(); i++) mix(static_cast<uint64_t>(game.dino(i).id) * 400 + game.dino(i).cell);
    for (int i = 0; i < game.meteor_count(); i++) {
        mix(static_cast<uint64_t>(game.meteor(i).x) * 1000 + game.meteor(i).y * 10 + game.meteor(i).turns);
    }
    return hash;
}

template <typename Work>
void parallel_for(int count, int threads, Work work) {
    std::atomic<int> next{0};
    auto run = [&](int worker) {
        for (int index = next.fetch_add(1); index < count; index = next.fetch_add(1)) work(index, worker);
    };
    std::vector<std::thread> pool;
    for (int worker = 1; worker < threads; worker++) pool.emplace_back(run, worker);
    run(0);
    for (std::thread& thread : pool) thread.join();
}

// Every way to play `turn` from this state, valued after in-flight meteors land.
void expand(const Game& state, int index, int turn, const Options& options, std::vector<Action>& actions,
            std::vector<Candidate>& found) {
    list_actions(state, options, actions);
    double flood = 0;
    Game bare = state;
    if (options.flood > 0) {
        bare.clear_dinosaurs();
        flood = sim::flood_outlook(bare, turn, {}, kFloodLookahead, kPocketLimit);
    }
    for (const Action& action : actions) {
        Game next = state;
        if (!next.step(turn, action)) continue;
        Game rolled = next;
        int last = std::min(sim::kMaxTicks, turn + kRolloutTicks);
        for (int future = turn + 1; future <= last; future++) rolled.step(future, Action{});
        double value = evaluate(rolled, last + 1, options);
        if (options.flood > 0) {
            bool volcano = action.type == Action::Type::TriggerVolcano;
            value += options.flood * (volcano ? sim::flood_outlook(bare, turn, {{0, action}}, kFloodLookahead, kPocketLimit) : flood);
        }
        found.push_back({value, state_hash(rolled), index, action});
    }
}

// A total order, so results don't depend on thread timing.
bool ranks_higher(const Candidate& a, const Candidate& b) {
    if (a.value != b.value) return a.value > b.value;
    if (a.parent != b.parent) return a.parent < b.parent;
    if (a.action.type != b.action.type) return a.action.type < b.action.type;
    if (a.action.x != b.action.x) return a.action.x < b.action.x;
    return a.action.y < b.action.y;
}

// Points a narrow beam finds from `start` over the next kLabelWindow ticks.
int label_points(const Game& start, int first, int width, const Options& options) {
    std::vector<Game> beam{start};
    std::vector<Game> next_beam;
    std::vector<Candidate> found;
    std::vector<Action> actions;
    std::unordered_set<uint64_t> seen;
    int last = first + kLabelWindow - 1;
    for (int turn = first; turn <= last; turn++) {
        found.clear();
        for (int i = 0; i < static_cast<int>(beam.size()); i++) expand(beam[i], i, turn, options, actions, found);
        std::sort(found.begin(), found.end(), ranks_higher);
        next_beam.clear();
        seen.clear();
        for (const Candidate& candidate : found) {
            if (static_cast<int>(next_beam.size()) >= width) break;
            if (!seen.insert(candidate.hash).second) continue;
            next_beam.push_back(beam[candidate.parent]);
            next_beam.back().step(turn, candidate.action);
        }
        beam.swap(next_beam);
    }
    int best = start.score();
    int end = std::min(sim::kMaxTicks, last + kRolloutTicks);
    for (Game& game : beam) {
        for (int future = last + 1; future <= end; future++) game.step(future, Action{});
        best = std::max(best, game.score());
    }
    return best - start.score();
}

void report(const sim::Terrain& terrain, const char* seed, const std::vector<Action>& plan, bool shield) {
    Game game(terrain, seed);
    game.set_spawn_shield(shield);
    std::map<int, std::pair<int, long long>> by_size;  // Kills in a tick: events, points.
    int meteors = 0;
    int volcanoes = 0;
    int waves = 0;
    int last_id = 0;
    int lowest_spawn = sim::kCells;
    for (int turn = 1; turn <= sim::kMaxTicks; turn++) {
        int before = game.score();
        Action action = plan[turn];
        meteors += action.type == Action::Type::LaunchMeteor;
        volcanoes += action.type == Action::Type::TriggerVolcano;
        game.step(turn, action);
        lowest_spawn = std::min(lowest_spawn, game.spawn_tiles());
        if (game.kills_this_tick() > 0) {
            auto& entry = by_size[game.kills_this_tick()];
            entry.first++;
            entry.second += game.score() - before;
        }
        for (int i = 0; i < game.dino_count(); i++) {
            if (game.dino(i).id > last_id) {
                last_id = game.dino(i).id;
                if (game.dino(i).spawn_turn == turn && (i == 0 || game.dino(i - 1).spawn_turn != turn)) waves++;
            }
        }
    }
    std::printf("RESULT score=%d kills=%d spawned=%d waves=%d meteors=%d volcanoes=%d lowest_spawn_tiles=%d\n", game.score(),
                game.total_kills(), last_id, waves, meteors, volcanoes, lowest_spawn);
    for (const auto& [size, entry] : by_size) {
        std::printf("  %2d-kill ticks: %4d  points %7lld\n", size, entry.first, entry.second);
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 5) {
        std::fprintf(stderr, "usage: sim_search <biome> <seed> <width> <plan file> [options]\n");
        return 2;
    }
    const char* biome = argv[1];
    const char* seed = argv[2];
    int width = std::atoi(argv[3]);
    const char* plan_path = argv[4];

    Options options;
    sim::ValueNet net;
    for (int i = 5; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        double value = std::atof(argv[i + 1]);
        if (name == "--radius") options.radius = static_cast<int>(value);
        else if (name == "--cluster") options.cluster = value;
        else if (name == "--spread") options.spread = value;
        else if (name == "--volcanoes") options.volcanoes = static_cast<int>(value);
        else if (name == "--threads") options.threads = static_cast<int>(value);
        else if (name == "--shield") options.shield = value != 0;
        else if (name == "--pocket") options.pocket = value;
        else if (name == "--grid-step") options.grid_step = static_cast<int>(value);
        else if (name == "--pocket-value") options.pocket_value = value;
        else if (name == "--flood") options.flood = value;
        else if (name == "--net-weight") options.net_weight = value;
        else if (name == "--data") options.data = argv[i + 1];
        else if (name == "--samples") options.samples = static_cast<int>(value);
        else if (name == "--every") options.every = static_cast<int>(value);
        else if (name == "--label-width") options.label_width = static_cast<int>(value);
        else if (name == "--label-step") options.label_step = static_cast<int>(value);
        else if (name == "--net") {
            if (!net.load(argv[i + 1])) {
                std::fputs("can't read the net file\n", stderr);
                return 2;
            }
            options.net = &net;
        }
    }

    sim::Terrain terrain(seed, biome);
    std::vector<Game> beam;
    beam.reserve(width);
    beam.emplace_back(terrain, seed);
    beam.back().set_spawn_shield(options.shield);
    std::vector<std::vector<Link>> history(sim::kMaxTicks + 1);
    std::ofstream data;
    if (!options.data.empty()) data.open(options.data, std::ios::binary);
    uint32_t draw = 2166136261u;
    for (const char* c = seed; *c != 0; c++) draw = (draw ^ static_cast<uint8_t>(*c)) * 16777619u;

    for (int turn = 1; turn <= sim::kMaxTicks; turn++) {
        std::vector<std::vector<Candidate>> found(options.threads);
        std::vector<std::vector<Action>> scratch(options.threads);
        parallel_for(static_cast<int>(beam.size()), options.threads, [&](int index, int worker) {
            expand(beam[index], index, turn, options, scratch[worker], found[worker]);
        });

        std::vector<Candidate> all;
        for (auto& items : found) all.insert(all.end(), items.begin(), items.end());
        std::sort(all.begin(), all.end(), ranks_higher);

        if (data.is_open() && turn % options.every == 0 && turn + kRolloutTicks + kLabelWindow <= sim::kMaxTicks) {
            std::vector<Game> samples;
            for (int i = 0; i < options.samples && !all.empty(); i++) {
                draw = draw * 1664525u + 1013904223u;
                size_t range = i % 2 == 0 ? std::min<size_t>(all.size(), width) : all.size();
                const Candidate& candidate = all[(draw >> 8) % range];
                samples.push_back(beam[candidate.parent]);
                samples.back().step(turn, candidate.action);
                for (int future = turn + 1; future <= turn + kRolloutTicks; future++) samples.back().step(future, Action{});
            }
            int first = turn + kRolloutTicks + 1;
            std::vector<int32_t> points(samples.size());
            Options label_options = options;
            label_options.grid_step = options.label_step;
            parallel_for(static_cast<int>(samples.size()), options.threads, [&](int index, int) {
                points[index] = label_points(samples[index], first, options.label_width, label_options);
            });
            for (size_t i = 0; i < samples.size(); i++) {
                uint8_t features[sim::kFeatureBytes];
                sim::encode_features(samples[i], first, features);
                int32_t tick = first;
                data.write(reinterpret_cast<const char*>(&tick), sizeof(tick));
                data.write(reinterpret_cast<const char*>(&points[i]), sizeof(points[i]));
                data.write(reinterpret_cast<const char*>(features), sizeof(features));
            }
        }

        std::vector<Game> next_beam;
        next_beam.reserve(width);
        std::unordered_set<uint64_t> seen;
        for (const Candidate& candidate : all) {
            if (static_cast<int>(next_beam.size()) >= width) break;
            if (!seen.insert(candidate.hash).second) continue;
            Game next = beam[candidate.parent];
            next.step(turn, candidate.action);
            next_beam.push_back(next);
            history[turn].push_back({candidate.parent, candidate.action});
        }
        beam.swap(next_beam);

        if (turn % 100 == 0) {
            int best = 0;
            for (const Game& game : beam) best = std::max(best, game.score());
            std::fprintf(stderr, "turn %4d  best score %d  actions %zu\n", turn, best, all.size());
        }
    }

    int best_index = 0;
    for (int i = 1; i < static_cast<int>(beam.size()); i++) {
        if (beam[i].score() > beam[best_index].score()) best_index = i;
    }

    std::vector<Action> plan(sim::kMaxTicks + 1);
    int index = best_index;
    for (int turn = sim::kMaxTicks; turn >= 1; turn--) {
        plan[turn] = history[turn][index].action;
        index = history[turn][index].parent;
    }

    std::ofstream file(plan_path);
    for (int turn = 1; turn <= sim::kMaxTicks; turn++) {
        if (plan[turn].type == Action::Type::None) continue;
        char kind = plan[turn].type == Action::Type::TriggerVolcano ? 'V' : 'M';
        file << turn << ' ' << kind << ' ' << plan[turn].x << ' ' << plan[turn].y << '\n';
    }
    report(terrain, seed, plan, options.shield);
    return 0;
}
