#include "agent.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <unordered_set>
#include <vector>

#include "features.hpp"

namespace sim {

namespace {

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

void mark_around(int cell, int radius, bool* wanted) {
    int row = cell / kWidth;
    int column = cell % kWidth;
    for (int dy = -radius; dy <= radius; dy++) {
        int reach = radius - std::abs(dy);
        for (int dx = -reach; dx <= reach; dx++) {
            if (row + dy >= 0 && row + dy < kHeight && column + dx >= 0 && column + dx < kWidth) {
                wanted[(row + dy) * kWidth + column + dx] = true;
            }
        }
    }
}

void list_actions(const Game& game, int turn, const AgentConfig& config, std::vector<Action>& actions) {
    actions.clear();
    actions.push_back(Action{});
    int origin = game.origin_y();

    if (game.meteor_count() < kMaxMeteors) {
        bool wanted[kCells] = {};
        if (config.aim_current) {
            for (int i = 0; i < game.dino_count(); i++) mark_around(game.dino(i).cell, config.radius, wanted);
        }
        if (config.aim_predicted) {
            Game future = game;
            int last = std::min(kMaxTicks, turn + kMeteorDelay - 1);
            for (int t = turn; t <= last; t++) future.step(t, Action{});
            int shift = future.origin_y() - game.origin_y();
            for (int i = 0; i < future.dino_count(); i++) {
                int cell = future.dino(i).cell + shift * kWidth;  // Back to today's frame.
                if (cell < kCells) mark_around(cell, config.radius, wanted);
            }
        }
        if (config.grid) {
            for (int row = 1; row < kHeight; row += 3) {
                for (int column = 1; column < kWidth; column += 3) wanted[row * kWidth + column] = true;
            }
        }
        for (int cell = 0; cell < kCells; cell++) {
            if (!wanted[cell]) continue;
            Action action;
            action.type = Action::Type::LaunchMeteor;
            action.x = cell % kWidth;
            action.y = cell / kWidth + origin;
            actions.push_back(action);
        }
    }

    if (game.volcano_count() < kMaxVolcanoes && config.volcanoes > 0) {
        int mountains[kCells];
        int count = 0;
        for (int cell = 0; cell < kCells; cell++) {
            if (game.flags(cell) & kMountain) mountains[count++] = cell;
        }
        if (config.volcano_near_dinos && game.dino_count() > 0) {
            int distances[kCells] = {};
            for (int i = 0; i < count; i++) {
                int cell = mountains[i];
                int best = 1000;
                for (int dino = 0; dino < game.dino_count(); dino++) {
                    int other = game.dino(dino).cell;
                    best = std::min(best, std::abs(cell % kWidth - other % kWidth) +
                                              std::abs(cell / kWidth - other / kWidth));
                }
                distances[cell] = best;
            }
            std::stable_sort(mountains, mountains + count,
                             [&](int a, int b) { return distances[a] < distances[b]; });
            count = std::min(count, config.volcanoes);
        }
        int stride = config.volcano_near_dinos ? 1 : std::max(1, count / config.volcanoes);
        for (int i = 0; i < count; i += stride) {
            Action action;
            action.type = Action::Type::TriggerVolcano;
            action.x = mountains[i] % kWidth;
            action.y = mountains[i] / kWidth + origin;
            actions.push_back(action);
        }
    }
}

uint64_t state_hash(const Game& game) {
    uint64_t hash = 1469598103934665603ull;
    auto mix = [&](uint64_t value) {
        hash ^= value;
        hash *= 1099511628211ull;
    };
    mix(static_cast<uint64_t>(game.score()));
    for (int i = 0; i < game.dino_count(); i++) mix(static_cast<uint64_t>(game.dino(i).id) * 400 + game.dino(i).cell);
    for (int i = 0; i < game.meteor_count(); i++) {
        mix(static_cast<uint64_t>(game.meteor(i).x) * 1000 + game.meteor(i).y * 10 + game.meteor(i).turns);
    }
    return hash;
}

struct Candidate {
    double value;
    uint64_t hash;
    int parent;
    Action action;
    Action first;  // The move to play now if this line wins.
};

struct Node {
    Game game;
    Action first;
};

}  // namespace

Agent::Choice Agent::search(const Game& root, int tick, int budget, double deadline_ms) {
    std::vector<Node> frontier;
    frontier.push_back({root, Action{}});
    std::vector<Action> actions;
    Choice best{Action{}, -1};

    for (int depth = 0; tick + depth <= kMaxTicks && budget > 0; depth++) {
        int turn = tick + depth;
        std::vector<Candidate> candidates;
        bool out_of_time = false;

        for (int parent = 0; parent < static_cast<int>(frontier.size()) && !out_of_time; parent++) {
            list_actions(frontier[parent].game, turn, config_, actions);
            for (const Action& action : actions) {
                if (budget <= 0 || (deadline_ms > 0 && now_ms() >= deadline_ms)) {
                    out_of_time = true;
                    break;
                }
                Game next = frontier[parent].game;
                if (!next.step(turn, action)) continue;
                int last = std::min(kMaxTicks, turn + config_.horizon);
                for (int future = turn + 1; future <= last; future++) next.step(future, Action{});
                budget -= last - turn + 1;
                last_simulated_ += last - turn + 1;
                candidates.push_back({static_cast<double>(next.score()), state_hash(next), parent, action,
                                      depth == 0 ? action : frontier[parent].first});
            }
        }
        if (candidates.empty()) break;

        std::stable_sort(candidates.begin(), candidates.end(),
                         [](const Candidate& a, const Candidate& b) { return a.value > b.value; });
        if (candidates[0].value >= best.value) best = {candidates[0].first, candidates[0].value};
        if (out_of_time) break;

        std::vector<Node> next_frontier;
        std::unordered_set<uint64_t> seen;
        for (const Candidate& candidate : candidates) {
            if (static_cast<int>(next_frontier.size()) >= config_.beam) break;
            if (!seen.insert(candidate.hash).second) continue;
            Game next = frontier[candidate.parent].game;
            next.step(turn, candidate.action);
            next_frontier.push_back({next, candidate.first});
        }
        frontier.swap(next_frontier);
    }
    return best;
}

namespace {

// Actions most likely to matter first, so widening reaches them early.
void sort_actions(const Game& game, std::vector<Action>& actions) {
    int promises[kCells] = {};
    constexpr int kSquared = kMeteorRadius * kMeteorRadius;
    for (int i = 0; i < game.dino_count(); i++) {
        int row = game.dino(i).cell / kWidth;
        int column = game.dino(i).cell % kWidth;
        for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
            for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
                int target_row = row + dy;
                int target_column = column + dx;
                if (dx * dx + dy * dy <= kSquared && target_row >= 0 && target_row < kHeight &&
                    target_column >= 0 && target_column < kWidth) {
                    promises[target_row * kWidth + target_column]++;
                }
            }
        }
    }
    auto promise = [&](const Action& action) {
        if (action.type == Action::Type::None) return 0.5;
        if (action.type == Action::Type::TriggerVolcano) return 1.0;
        int row = action.y - game.origin_y();
        return row >= 0 && row < kHeight && action.x >= 0 && action.x < kWidth
                   ? static_cast<double>(promises[row * kWidth + action.x])
                   : 0.0;
    };
    std::stable_sort(actions.begin(), actions.end(),
                     [&](const Action& a, const Action& b) { return promise(a) > promise(b); });
}

struct Edge {
    Action action;
    int visits = 0;
    double total = 0;
    double best = -std::numeric_limits<double>::infinity();
    int child = -1;
};

struct TreeNode {
    bool opened = false;
    int visits = 0;
    int next_untried = 0;
    std::vector<Action> untried;
    std::vector<Edge> edges;
};

}  // namespace

struct MctsState {
    MctsState(const Game& root_game, int root_tick, int sim_budget)
        : root(root_game), tick(root_tick), budget(sim_budget), tree(1) {
        tree.reserve(4096);
    }

    Game root;
    int tick;
    int budget;
    std::vector<TreeNode> tree;
    std::vector<Action> actions;
    double lowest_reward = std::numeric_limits<double>::infinity();
    double highest_reward = -std::numeric_limits<double>::infinity();
    uint32_t iteration = 0;
    bool finished = false;  // Nothing left to decide, so further runs are no-ops.
};

Agent::Agent(AgentConfig config) : config_(config) {}

Agent::~Agent() = default;

Agent::Choice Agent::search_mcts(const Game& root, int tick, int budget, double deadline_ms) {
    MctsState state(root, tick, budget);
    mcts_run(state, deadline_ms);
    return mcts_best(state);
}

void Agent::mcts_run(MctsState& state, double deadline_ms) {
    const Game& root = state.root;
    const int tick = state.tick;
    int& budget = state.budget;
    std::vector<TreeNode>& tree = state.tree;
    std::vector<Action>& actions = state.actions;
    double& lowest_reward = state.lowest_reward;
    double& highest_reward = state.highest_reward;
    uint32_t& iteration = state.iteration;

    while (!state.finished && budget > 0 && (deadline_ms <= 0 || now_ms() < deadline_ms) &&
           !stop_.load(std::memory_order_relaxed)) {
        Game game = root;
        game.hide(false, true, false, static_cast<uint32_t>(tick) * 2654435761u + iteration * 40503u);
        iteration++;

        int path_node[32];
        int path_edge[32];
        int path_length = 0;
        int node = 0;
        int turn = tick;

        for (int depth = 0; depth < config_.mcts_depth && turn <= kMaxTicks; depth++) {
            // With every meteor in the air there is nothing to decide.
            if (game.meteor_count() >= kMaxMeteors && game.volcano_count() >= kMaxVolcanoes) {
                game.step(turn++, Action{});
                budget--;
                continue;
            }

            if (!tree[node].opened) {
                AgentConfig targeting = config_;
                targeting.aim_predicted = config_.aim_predicted && node == 0;
                list_actions(game, turn, targeting, actions);
                if (targeting.aim_predicted && game.meteor_count() < kMaxMeteors) {
                    budget -= std::min(kMeteorDelay, kMaxTicks - turn + 1);
                }
                sort_actions(game, actions);
                tree[node].untried = actions;
                tree[node].opened = true;
            }

            int edge_index = -1;
            size_t allowed = 1 + static_cast<size_t>(config_.widening * std::sqrt(static_cast<double>(tree[node].visits)));
            if (tree[node].edges.size() < allowed &&
                tree[node].next_untried < static_cast<int>(tree[node].untried.size())) {
                Edge edge;
                edge.action = tree[node].untried[tree[node].next_untried++];
                tree[node].edges.push_back(edge);
                edge_index = static_cast<int>(tree[node].edges.size()) - 1;
            } else {
                double log_visits = std::log(tree[node].visits + 1.0);
                double reward_range = std::max(1.0, highest_reward - lowest_reward);
                double best_score = -1e18;
                for (int i = 0; i < static_cast<int>(tree[node].edges.size()); i++) {
                    const Edge& edge = tree[node].edges[i];
                    double mean = edge.visits > 0 ? edge.total / edge.visits : 0;
                    double value = config_.optimism > 0
                                       ? mean + config_.optimism * (edge.best - mean)
                                       : mean;
                    double score = edge.visits == 0
                                       ? 1e17
                                       : (value - lowest_reward) / reward_range +
                                             config_.exploration * std::sqrt(log_visits / edge.visits);
                    if (score > best_score) {
                        best_score = score;
                        edge_index = i;
                    }
                }
            }

            path_node[path_length] = node;
            path_edge[path_length] = edge_index;
            path_length++;

            Action action = tree[node].edges[edge_index].action;
            if (!game.step(turn, action)) game.step(turn, Action{});
            budget--;
            turn++;

            int child = tree[node].edges[edge_index].child;
            if (child < 0) {
                child = static_cast<int>(tree.size());
                tree[node].edges[edge_index].child = child;
                tree.emplace_back();
            }
            node = child;
        }

        int last = std::min(kMaxTicks, turn - 1 + config_.horizon);
        for (int future = turn; future <= last; future++) {
            game.step(future, Action{});
            budget--;
        }
        last_simulated_ = config_.sim_budget - budget;

        double reward = game.score() - root.score();
        if (config_.net != nullptr) {
            constexpr double kWindow = 100;  // The net predicts points over this many ticks.
            int next_tick = last + 1;
            uint8_t features[kFeatureBytes];
            encode_features(game, next_tick, features);
            double remaining = std::min(1.0, (kMaxTicks + 1 - next_tick) / kWindow);
            reward += config_.value_weight * 1000 * remaining * config_.net->evaluate(features);
            budget -= config_.net_cost;
        }
        lowest_reward = std::min(lowest_reward, reward);
        highest_reward = std::max(highest_reward, reward);
        tree[0].visits++;
        for (int i = 0; i < path_length; i++) {
            Edge& edge = tree[path_node[i]].edges[path_edge[i]];
            edge.visits++;
            edge.total += reward;
            edge.best = std::max(edge.best, reward);
            if (i > 0) tree[path_node[i]].visits++;
        }
        if (path_length == 0) state.finished = true;  // Nothing to decide this tick.
    }
}

Agent::Choice Agent::mcts_best(const MctsState& state) const {
    Choice best{Action{}, -1};
    int most_visits = -1;
    for (const Edge& edge : state.tree[0].edges) {
        if (edge.visits > most_visits) {
            most_visits = edge.visits;
            best = {edge.action, edge.visits > 0 ? edge.total / edge.visits : 0};
        }
    }
    return best;
}

bool Agent::begin(const Observation& observation) {
    last_simulated_ = 0;
    stop_.store(false, std::memory_order_relaxed);
    mcts_.reset();
    Game root = Game::from_observation(observation, static_cast<uint32_t>(observation.tick) * 2654435761u);
    if (root.meteor_count() >= kMaxMeteors && root.volcano_count() >= kMaxVolcanoes) return false;
    mcts_ = std::make_unique<MctsState>(root, observation.tick, config_.sim_budget);
    return true;
}

Action Agent::resume(double time_ms) {
    if (!mcts_) return Action{};
    if (time_ms > 0) mcts_run(*mcts_, now_ms() + time_ms);
    return mcts_best(*mcts_).action;
}

Action Agent::act(const Observation& observation, const Game* truth) {
    last_simulated_ = 0;
    stop_.store(false, std::memory_order_relaxed);
    uint32_t rng_seed = static_cast<uint32_t>(observation.tick) * 2654435761u;
    Game root = Game::from_observation(observation, rng_seed);
    if (truth != nullptr) {
        root = *truth;
        root.hide(!config_.reveal_rows, !config_.reveal_rng, !config_.reveal_waves, rng_seed);
        root.set_spawn_shield(config_.shield);
    }
    if (root.meteor_count() >= kMaxMeteors && root.volcano_count() >= kMaxVolcanoes) return Action{};

    double deadline = config_.time_budget_ms > 0 ? now_ms() + config_.time_budget_ms : 0;
    if (config_.mcts) return search_mcts(root, observation.tick, config_.sim_budget, deadline).action;
    if (config_.samples <= 1) return search(root, observation.tick, config_.sim_budget, deadline).action;

    // Each sample guesses the hidden tie-breaks differently, and the best-backed move wins.
    struct Vote {
        Action action;
        double total;
    };
    std::vector<Vote> votes;
    for (int sample = 0; sample < config_.samples; sample++) {
        Game guess = Game::from_observation(
            observation, static_cast<uint32_t>(observation.tick) * 2654435761u + static_cast<uint32_t>(sample) * 40503u);
        double share = deadline > 0 ? now_ms() + (deadline - now_ms()) / (config_.samples - sample) : 0;
        Choice choice = search(guess, observation.tick, config_.sim_budget / config_.samples, share);
        auto found = std::find_if(votes.begin(), votes.end(), [&](const Vote& vote) {
            return vote.action.type == choice.action.type && vote.action.x == choice.action.x &&
                   vote.action.y == choice.action.y;
        });
        if (found == votes.end()) votes.push_back({choice.action, choice.value});
        else found->total += choice.value;
    }
    auto winner = std::max_element(votes.begin(), votes.end(), [](const Vote& a, const Vote& b) { return a.total < b.total; });
    return winner->action;
}

}  // namespace sim
