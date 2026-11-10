#include "agent.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <unordered_set>
#include <vector>

#include "features.hpp"

namespace sim {

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

namespace {

// How many spawn tiles a meteor on `cell` would cover.
int pocket_cover(const uint8_t* mask, int cell) {
    int row = cell / kWidth;
    int column = cell % kWidth;
    int count = 0;
    constexpr int kSquared = kMeteorRadius * kMeteorRadius;
    for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
        for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
            int r = row + dy;
            int c = column + dx;
            if (dx * dx + dy * dy <= kSquared && r >= 0 && r < kHeight && c >= 0 && c < kWidth) {
                count += mask[r * kWidth + c] != 0;
            }
        }
    }
    return count;
}

// Points a meteor timed for a new wave's first tick could expect, given the spawn area.
double pocket_value(const Game& game, int limit) {
    uint8_t mask[kCells];
    int size = game.spawn_mask(mask);
    if (size > limit || size == 0) return 0;
    int cover = 0;
    for (int cell = 0; cell < kCells; cell++) {
        if (mask[cell]) cover = std::max(cover, pocket_cover(mask, cell));
    }
    double kills = std::min<double>(cover, kDinosPerWave * static_cast<double>(cover) / std::max(size, kDinosPerWave));
    constexpr double kFreshValue = 164;
    return kFreshValue * kills * (1 + 0.5 * (kills - 1));
}

void list_actions(const Game& game, const AgentConfig& config, std::vector<Action>& actions,
                  const std::vector<Action>* volcano_list = nullptr) {
    actions.clear();
    actions.push_back(Action{});
    int origin = game.origin_y();

    if (game.meteor_count() < kMaxMeteors) {
        bool wanted[kCells] = {};
        for (int i = 0; i < game.dino_count(); i++) {
            int row = game.dino(i).cell / kWidth;
            int column = game.dino(i).cell % kWidth;
            for (int dy = -config.radius; dy <= config.radius; dy++) {
                int reach = config.radius - std::abs(dy);
                for (int dx = -reach; dx <= reach; dx++) {
                    if (row + dy >= 0 && row + dy < kHeight && column + dx >= 0 && column + dx < kWidth) {
                        wanted[(row + dy) * kWidth + column + dx] = true;
                    }
                }
            }
        }
        if (config.plan_waves && config.pocket_targets > 0) {
            uint8_t mask[kCells];
            int size = game.spawn_mask(mask);
            if (size <= config.pocket_limit) {
                int covered[kCells];
                for (int cell = 0; cell < kCells; cell++) covered[cell] = pocket_cover(mask, cell);
                for (int pick = 0; pick < config.pocket_targets; pick++) {
                    int best = static_cast<int>(std::max_element(covered, covered + kCells) - covered);
                    if (covered[best] == 0) break;
                    wanted[best] = true;
                    covered[best] = 0;
                }
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

    if (game.volcano_count() < kMaxVolcanoes && volcano_list != nullptr && !volcano_list->empty()) {
        for (const Action& action : *volcano_list) {
            int row = action.y - origin;
            if (row >= 0 && row < kHeight) actions.push_back(action);
        }
    } else if (game.volcano_count() < kMaxVolcanoes && config.volcanoes > 0) {
        int mountains[kCells];
        int count = 0;
        for (int cell = 0; cell < kCells; cell++) {
            if (game.flags(cell) & kMountain) mountains[count++] = cell;
        }
        int stride = std::max(1, count / config.volcanoes);
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
            list_actions(frontier[parent].game, config_, actions);
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
double action_promise(const Game& game, const Action& action, const uint8_t* pocket, int pocket_size) {
    if (action.type == Action::Type::None) return 0.5;
    if (action.type == Action::Type::TriggerVolcano) return 1;
    double count = 0;
    if (pocket != nullptr) {
        int cell = (action.y - game.origin_y()) * kWidth + action.x;
        count = pocket_cover(pocket, cell) * static_cast<double>(kDinosPerWave) / std::max(pocket_size, kDinosPerWave);
    }
    constexpr int kSquared = kMeteorRadius * kMeteorRadius;
    for (int i = 0; i < game.dino_count(); i++) {
        int dx = game.dino(i).cell % kWidth - action.x;
        int dy = game.dino(i).cell / kWidth + game.origin_y() - action.y;
        if (dx * dx + dy * dy <= kSquared) count++;
    }
    return count;
}

struct Edge {
    Action action;
    int visits = 0;
    double total = 0;
    double best = -1e300;
    int child = -1;
    float prior = 0;
};

int action_code(const Action& action, int origin) {
    if (action.type == Action::Type::None) return 0;
    int cell = (action.y - origin) * kWidth + action.x;
    return (action.type == Action::Type::LaunchMeteor ? 1 : 1 + kCells) + cell;
}

Action action_from_code(int code, int origin) {
    Action action;
    if (code == 0) return action;
    int cell = (code - 1) % kCells;
    action.type = code <= kCells ? Action::Type::LaunchMeteor : Action::Type::TriggerVolcano;
    action.x = cell % kWidth;
    action.y = cell / kWidth + origin;
    return action;
}

// Shared record of an action at one tick of the plan, for RAVE.
struct Amaf {
    int visits = 0;
    double total = 0;
};

constexpr int kAmafOffsets = 16;
constexpr int kAmafRows = 24;  // A frame, plus the rows a plan can scroll in.
constexpr int kAmafKeys = 1 + 2 * kAmafRows * kWidth;

int amaf_key(const Action& action, int origin) {
    if (action.type == Action::Type::None) return 0;
    int cell = (action.y - origin) * kWidth + action.x;
    return 1 + (action.type == Action::Type::LaunchMeteor ? 0 : kAmafRows * kWidth) + cell;
}

struct TreeNode {
    bool opened = false;
    bool ranked = false;
    int visits = 0;
    int next_untried = 0;
    std::vector<Action> untried;
    std::vector<Edge> edges;
    std::vector<float> priors;  // By action code, once the tree net has ranked this node.
};

// Copies the subtree under start, so its root is element 0.
void copy_subtree(const std::vector<TreeNode>& from, int start, std::vector<TreeNode>& to) {
    to.assign(1, from[start]);
    for (size_t i = 0; i < to.size(); i++) {
        for (size_t e = 0; e < to[i].edges.size(); e++) {
            int child = to[i].edges[e].child;
            if (child < 0) continue;
            to[i].edges[e].child = static_cast<int>(to.size());
            to.push_back(from[child]);
        }
    }
}

bool same_action(const Action& a, const Action& b) { return a.type == b.type && a.x == b.x && a.y == b.y; }

// Points for killing a dinosaur of this age, as the engine scores it.
int kill_base(int age) {
    static const auto table = [] {
        std::vector<int> values(kMaxTicks + 8);
        for (size_t i = 0; i < values.size(); i++) {
            values[i] = static_cast<int>(std::ceil(std::max(0.0, 500 / std::sqrt(i / 4.0 + 5) - 60)));
        }
        return values;
    }();
    return table[std::clamp(age, 0, static_cast<int>(table.size()) - 1)];
}

// For rollouts: the meteor whose footprint covers the most points if nobody moved, counting
// the multi-kill multiplier. Holds fire (None) unless it covers at least min_kills dinosaurs.
Action strike_action(const Game& game, int turn, int min_kills, double* best_value = nullptr, bool need_slot = true) {
    if (best_value != nullptr) *best_value = 0;
    if ((need_slot && game.meteor_count() >= kMaxMeteors) || game.dino_count() == 0) return Action{};
    int count[kCells] = {};
    int value[kCells] = {};
    for (int slot = 0; slot < game.dino_count(); slot++) {
        const Dino& dino = game.dino(slot);
        if (!dino.alive) continue;
        int row = dino.cell / kWidth, column = dino.cell % kWidth;
        int points = kill_base(turn + kMeteorDelay - dino.spawn_turn);
        for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
            int r = row + dy;
            if (r < 0 || r >= kHeight) continue;
            for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
                int c = column + dx;
                if (c < 0 || c >= kWidth || dx * dx + dy * dy > kMeteorRadius * kMeteorRadius) continue;
                count[r * kWidth + c]++;
                value[r * kWidth + c] += points;
            }
        }
    }
    int best = -1;
    double best_score = 0;
    for (int cell = 0; cell < kCells; cell++) {
        if (count[cell] < min_kills) continue;
        double score = value[cell] * (1 + 0.5 * (count[cell] - 1)) + count[cell];
        if (score > best_score) { best_score = score; best = cell; }
    }
    Action action;
    if (best_value != nullptr) *best_value = best_score;
    if (best < 0) return action;
    action.type = Action::Type::LaunchMeteor;
    action.x = best % kWidth;
    action.y = best / kWidth + game.origin_y();
    return action;
}

}  // namespace

struct KeptTree {
    int tick = -1;       // The tick it can be used on.
    int root_score = 0;  // Its rewards are relative to this score.
    std::vector<TreeNode> nodes;
};

Agent::Choice Agent::search_mcts(const Game& root, int tick, int budget, double deadline_ms) {
    std::vector<TreeNode> tree(1);
    tree.reserve(4096);
    std::vector<Action> actions;
    double lowest_reward = std::numeric_limits<double>::infinity();
    double highest_reward = -std::numeric_limits<double>::infinity();
    uint32_t iteration = 0;
    std::vector<int> alive;  // Sequential halving: root edges still in the running.
    int rounds = 1;
    int round_target = 1;
    int round = 0;
    // Share of last tick's iterations for each visit of a surviving action in round r.
    auto round_share = [&](int r, int alive_count) {
        double total = 0;
        for (int i = 0; i < rounds; i++) total += std::pow(1 + config_.halving_skew, -i);
        double weight = std::pow(1 + config_.halving_skew, -r) * rounds / total;
        return std::max(1, static_cast<int>(weight * last_iterations_ / (rounds * alive_count)));
    };

    // The prior of each action at the root, by action code.
    std::vector<float> prior;
    if (config_.policy != nullptr) {
        prior.assign(PolicyNet::kActions, 0);
        uint8_t features[kPolicyBytes];
        encode_policy_features(root, tick, features);
        if (config_.record_search) record_.features.assign(features, features + kPolicyBytes);
        float logits[PolicyNet::kActions];
        config_.policy->evaluate(features, tick, logits);

        bool meteors_free = root.meteor_count() < kMaxMeteors;
        bool volcano_free = root.volcano_count() < kMaxVolcanoes;
        float top = logits[0];
        int legal[PolicyNet::kActions];
        int legal_count = 0;
        legal[legal_count++] = 0;
        for (int cell = 0; cell < kCells; cell++) {
            if (meteors_free) legal[legal_count++] = 1 + cell;
            if (volcano_free && (root.flags(cell) & kMountain)) legal[legal_count++] = 1 + kCells + cell;
        }
        double sum = 0;
        for (int i = 0; i < legal_count; i++) top = std::max(top, logits[legal[i]]);
        for (int i = 0; i < legal_count; i++) {
            prior[legal[i]] = std::exp(logits[legal[i]] - top);
            sum += prior[legal[i]];
        }
        for (int i = 0; i < legal_count; i++) {
            prior[legal[i]] = static_cast<float>((1 - config_.prior_floor) * prior[legal[i]] / sum + config_.prior_floor / legal_count);
        }

        list_actions(root, config_, actions);
        std::vector<char> listed(PolicyNet::kActions, 0);
        for (const Action& action : actions) listed[action_code(action, root.origin_y())] = 1;
        std::sort(legal, legal + legal_count, [&](int a, int b) { return prior[a] > prior[b]; });
        for (int i = 0; i < std::min(legal_count, config_.policy_targets); i++) {
            if (!listed[legal[i]]) actions.push_back(action_from_code(legal[i], root.origin_y()));
        }
        std::stable_sort(actions.begin(), actions.end(), [&](const Action& a, const Action& b) {
            return prior[action_code(a, root.origin_y())] > prior[action_code(b, root.origin_y())];
        });
        tree[0].untried = actions;
        tree[0].opened = true;
        budget -= config_.policy_cost;
    }

    // Keyed by node * dice + guess. Index into cached_states_, and the finished line's value.
    constexpr double kNoReward = -1e300;
    int dice = config_.dice;
    std::vector<int> state_index;
    std::vector<double> leaf_reward;
    auto grow_caches = [&] {
        state_index.resize(tree.size() * dice, -1);
        leaf_reward.resize(tree.size() * dice, kNoReward);
    };
    std::vector<Game> dice_roots;
    if (dice > 0) {
        cached_states_.clear();
        grow_caches();
        for (int guess = 0; guess < dice; guess++) {
            dice_roots.push_back(root);
            dice_roots.back().hide(false, true, false, static_cast<uint32_t>(tick) * 2654435761u + guess * 40503u);
        }
    }

    std::vector<Amaf> amaf(config_.rave_k > 0 ? kAmafOffsets * kAmafKeys : 0);
    auto shared = [&](int offset, const Action& action) -> Amaf& {
        return amaf[std::min(offset, kAmafOffsets - 1) * kAmafKeys + amaf_key(action, root.origin_y())];
    };

    auto halving_value = [&](const Edge& edge) {
        if (edge.visits == 0) return lowest_reward;
        double mean = edge.total / edge.visits;
        return mean + config_.halving_max * (edge.best - mean);
    };
    // The least-visited surviving root action, after dropping the worse half once a round is done.
    auto halving_pick = [&] {
        auto least_visited = [&] {
            int least = alive[0];
            for (int i : alive) {
                if (tree[0].edges[i].visits < tree[0].edges[least].visits) least = i;
            }
            return least;
        };
        int pick = least_visited();
        if (tree[0].edges[pick].visits >= round_target && alive.size() > 1) {
            auto rank = [&](int i) {
                const Edge& edge = tree[0].edges[i];
                double reward_range = std::max(1.0, highest_reward - lowest_reward);
                return (halving_value(edge) - lowest_reward) / reward_range + config_.halving_prior * edge.prior;
            };
            std::stable_sort(alive.begin(), alive.end(), [&](int a, int b) { return rank(a) > rank(b); });
            alive.resize((alive.size() + 1) / 2);
            round++;
            round_target += round_share(round, static_cast<int>(alive.size()));
            pick = least_visited();
        }
        return pick;
    };

    // With a clock limit, a long pause between two readings means the host took the CPU away.
    double last_clock = deadline_ms > 0 ? now_ms() : 0;
    while (budget > 0) {
        if (deadline_ms > 0) {
            double clock = now_ms();
            if (clock - last_clock >= kGapMs && gaps_.size() < kMaxGaps) gaps_.push_back({last_clock, clock});
            last_clock = clock;
            if (clock >= deadline_ms) break;
        }
        int root_pick = -1;
        if (config_.root_halving > 0 && !tree[0].edges.empty()) root_pick = halving_pick();
        uint32_t draw = static_cast<uint32_t>(iteration);
        if (config_.root_halving > 0 && config_.halving_crn) {
            draw = 7919u + static_cast<uint32_t>(root_pick >= 0 ? tree[0].edges[root_pick].visits : 0);
        }
        int guess = dice > 0 ? static_cast<int>(iteration % dice) : 0;
        Game game = dice > 0 ? dice_roots[guess] : root;
        if (dice == 0) game.hide(false, true, false, static_cast<uint32_t>(tick) * 2654435761u + draw * 40503u);
        game.set_forecast_sample(draw);
        iteration++;

        int path_node[32];
        int path_edge[32];
        int path_offset[32];
        int path_length = 0;
        int node = 0;
        int turn = tick;
        int depth = 0;

        while (depth < config_.mcts_depth && turn <= kMaxTicks) {
            if (!tree[node].opened) {
                list_actions(game, config_, actions, &ranked_volcanoes_);
                uint8_t pocket[kCells];
                int pocket_size = kCells;
                if (config_.plan_waves) pocket_size = game.spawn_mask(pocket);
                bool small = config_.plan_waves && pocket_size <= config_.pocket_limit;
                std::vector<std::pair<double, int>> order(actions.size());
                for (size_t i = 0; i < actions.size(); i++) {
                    order[i] = {action_promise(game, actions[i], small ? pocket : nullptr, pocket_size), static_cast<int>(i)};
                }
                std::stable_sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
                std::vector<Action>& untried = tree[node].untried;
                untried.resize(actions.size());
                for (size_t i = 0; i < actions.size(); i++) untried[i] = actions[order[i].second];
                tree[node].opened = true;
            }
            if (config_.policy != nullptr && depth >= 1 && depth <= config_.policy_depth && !tree[node].ranked &&
                tree[node].visits >= (depth == 1 ? config_.policy_tree_visits : config_.policy_deep_visits)) {
                uint8_t features[kPolicyBytes];
                encode_policy_features(game, turn, features);
                float logits[PolicyNet::kActions];
                (config_.tree_policy != nullptr ? config_.tree_policy : config_.policy)->evaluate(features, turn, logits);
                int origin = game.origin_y();
                std::vector<Action>& untried = tree[node].untried;
                std::stable_sort(untried.begin() + tree[node].next_untried, untried.end(), [&](const Action& a, const Action& b) {
                    return logits[action_code(a, origin)] > logits[action_code(b, origin)];
                });
                if (config_.tree_puct > 0) {
                    float top = -1e30f;
                    for (const Action& action : untried) top = std::max(top, logits[action_code(action, origin)]);
                    double sum = 0;
                    for (const Action& action : untried) sum += std::exp(logits[action_code(action, origin)] - top);
                    tree[node].priors.assign(PolicyNet::kActions, 0);
                    for (const Action& action : untried) {
                        int code = action_code(action, origin);
                        tree[node].priors[code] = static_cast<float>(std::exp(logits[code] - top) / sum);
                    }
                    for (Edge& edge : tree[node].edges) edge.prior = tree[node].priors[action_code(edge.action, origin)];
                }
                tree[node].ranked = true;
                budget -= config_.policy_tree_cost;
            }

            int offset = turn - tick;
            int edge_index = -1;
            size_t allowed = 1 + static_cast<size_t>(config_.widening * std::sqrt(static_cast<double>(tree[node].visits)));
            if (node == 0 && config_.root_halving > 0) {
                allowed = 0;
                if (tree[0].edges.empty()) {
                    int count = std::min(config_.root_halving, static_cast<int>(tree[0].untried.size()));
                    for (int i = 0; i < count; i++) {
                        Edge edge;
                        edge.action = tree[0].untried[tree[0].next_untried++];
                        if (!prior.empty()) edge.prior = prior[action_code(edge.action, root.origin_y())];
                        tree[0].edges.push_back(edge);
                        alive.push_back(i);
                    }
                    rounds = std::max(1, static_cast<int>(std::ceil(std::log2(std::max(2, count)))));
                    round_target = round_share(0, count);
                    if (config_.tree_reuse > 0 && kept_ && kept_->tick == tick && !kept_->nodes.empty()) {
                        // Last tick's search below the action actually played, shifted to this root's score.
                        double shift = root.score() - kept_->root_score;
                        int base = static_cast<int>(tree.size());
                        for (TreeNode& kept : kept_->nodes) {
                            for (Edge& edge : kept.edges) {
                                if (edge.child >= 0) edge.child += base;
                                edge.total -= shift * edge.visits;
                                edge.best -= shift;
                                if (edge.visits > 0) {
                                    // UCT below needs a reward range before the first new result.
                                    lowest_reward = std::min(lowest_reward, edge.total / edge.visits);
                                    highest_reward = std::max(highest_reward, edge.total / edge.visits);
                                }
                            }
                        }
                        tree.insert(tree.end(), kept_->nodes.begin(), kept_->nodes.end());
                        if (dice > 0) grow_caches();
                        for (int i = 0; i < count; i++) {
                            for (const Edge& old : tree[base].edges) {
                                if (old.child < 0 || !same_action(old.action, tree[0].edges[i].action)) continue;
                                Edge& edge = tree[0].edges[i];
                                edge.child = old.child;
                                if (config_.tree_reuse >= 2) {
                                    edge.visits = old.visits;
                                    edge.total = old.total;
                                    edge.best = old.best;
                                    tree[0].visits += old.visits;
                                    lowest_reward = std::min(lowest_reward, old.total / std::max(1, old.visits));
                                    highest_reward = std::max(highest_reward, old.total / std::max(1, old.visits));
                                }
                            }
                        }
                    }
                }
                edge_index = root_pick >= 0 ? root_pick : halving_pick();
            }
            if (edge_index >= 0) {
                // Chosen above.
            } else if (tree[node].edges.size() < allowed &&
                tree[node].next_untried < static_cast<int>(tree[node].untried.size())) {
                std::vector<Action>& untried = tree[node].untried;
                if (config_.rave_k > 0 && config_.rave_order && node != 0) {
                    constexpr int kMinShared = 3;
                    double total = 0;
                    int visits = 0;
                    for (const Edge& edge : tree[node].edges) {
                        total += edge.total;
                        visits += edge.visits;
                    }
                    double best_mean = visits > 0 ? total / visits : -1e300;
                    int best = -1;
                    for (int i = tree[node].next_untried; i < static_cast<int>(untried.size()); i++) {
                        const Amaf& record = shared(offset, untried[i]);
                        if (record.visits < kMinShared) continue;
                        double mean = record.total / record.visits;
                        if (mean > best_mean) {
                            best_mean = mean;
                            best = i;
                        }
                    }
                    if (best >= 0) std::swap(untried[tree[node].next_untried], untried[best]);
                }
                Edge edge;
                edge.action = untried[tree[node].next_untried++];
                if (node == 0 && !prior.empty()) edge.prior = prior[action_code(edge.action, root.origin_y())];
                if (!tree[node].priors.empty()) edge.prior = tree[node].priors[action_code(edge.action, game.origin_y())];
                tree[node].edges.push_back(edge);
                edge_index = static_cast<int>(tree[node].edges.size()) - 1;
            } else {
                double log_visits = std::log(tree[node].visits + 1.0);
                double reward_range = std::max(1.0, highest_reward - lowest_reward);
                double root_factor = std::sqrt(static_cast<double>(tree[node].visits));
                bool use_prior = node == 0 && !prior.empty();
                bool use_tree_prior = !tree[node].priors.empty();
                double tree_factor = config_.tree_puct * root_factor;
                double best_score = -1e18;
                for (int i = 0; i < static_cast<int>(tree[node].edges.size()); i++) {
                    const Edge& edge = tree[node].edges[i];
                    double score = 1e17;
                    if (edge.visits > 0) {
                        double mean = edge.total / edge.visits;
                        if (config_.rave_k > 0 && node != 0) {
                            const Amaf& record = shared(offset, edge.action);
                            if (record.visits > 0) {
                                double beta = std::sqrt(config_.rave_k / (3 * edge.visits + config_.rave_k));
                                mean = (1 - beta) * mean + beta * record.total / record.visits;
                            }
                        }
                        score = (mean - lowest_reward) / reward_range;
                        if (use_prior) score += config_.puct * edge.prior * root_factor / (1 + edge.visits);
                        else if (use_tree_prior) score += tree_factor * edge.prior / (1 + edge.visits);
                        else score += config_.exploration * std::sqrt(log_visits / edge.visits);
                    }
                    if (score > best_score) {
                        best_score = score;
                        edge_index = i;
                    }
                }
            }

            path_node[path_length] = node;
            path_edge[path_length] = edge_index;
            path_offset[path_length] = offset;
            path_length++;

            Action action = tree[node].edges[edge_index].action;
            int child = tree[node].edges[edge_index].child;
            if (child < 0) {
                child = static_cast<int>(tree.size());
                tree[node].edges[edge_index].child = child;
                tree.emplace_back();
                if (dice > 0) grow_caches();
            }
            node = child;

            int cached = dice > 0 ? state_index[node * dice + guess] : -1;
            if (cached >= 0) {
                game = cached_states_[cached].game;
                turn = cached_states_[cached].turn;
                depth = cached_states_[cached].depth;
                continue;
            }

            game.step(turn, action);
            budget--;
            turn++;
            depth++;
            // With every meteor in the air there is nothing to decide.
            while (depth < config_.mcts_depth && turn <= kMaxTicks && game.meteor_count() >= kMaxMeteors &&
                   game.volcano_count() >= kMaxVolcanoes) {
                game.step(turn++, Action{});
                budget--;
                depth++;
            }
            if (dice > 0) {
                state_index[node * dice + guess] = static_cast<int>(cached_states_.size());
                cached_states_.push_back({game, turn, depth});
            }
        }

        double reward = dice > 0 ? leaf_reward[node * dice + guess] : kNoReward;
        budget--;  // Even a remembered result costs something.
        if (reward == kNoReward) {
            budget++;
            int last = std::min(kMaxTicks, turn - 1 + config_.horizon);
            for (int future = turn; future <= last; future++) {
                Action rollout_action;
                if (config_.rollout_fire > 0 && future + kMeteorDelay <= last) {
                    rollout_action = strike_action(game, future, config_.rollout_fire);
                }
                game.step(future, rollout_action);
                budget--;
            }
            last_simulated_ = config_.sim_budget - budget;

            reward = game.score() - root.score();
            if (config_.net != nullptr) {
                constexpr double kWindow = 100;  // The net predicts points over this many ticks.
                int next_tick = last + 1;
                uint8_t features[kFeatureBytes];
                encode_features(game, next_tick, features);
                double remaining = std::min(1.0, (kMaxTicks + 1 - next_tick) / kWindow);
                double value = config_.net->evaluate(features);
                if (config_.net2 != nullptr) {
                    value = 0.5 * (value + config_.net2->evaluate(features));
                    budget -= config_.net_cost;
                }
                reward += config_.value_weight * 1000 * remaining * value;
                budget -= config_.net_cost;
            }
            if (config_.pocket_weight > 0) reward += config_.pocket_weight * pocket_value(game, config_.pocket_limit);
            if (config_.group_weight > 0) {
                double potential = 0;
                strike_action(game, last + 1, 1, &potential, false);
                reward += config_.group_weight * potential;
            }
            if (!flood_bonus_.empty()) {
                for (int i = 0; i < path_length; i++) {
                    const Action& taken = tree[path_node[i]].edges[path_edge[i]].action;
                    if (taken.type != Action::Type::TriggerVolcano) continue;
                    for (size_t j = 0; j < ranked_volcanoes_.size(); j++) {
                        if (ranked_volcanoes_[j].x == taken.x && ranked_volcanoes_[j].y == taken.y) reward += flood_bonus_[j];
                    }
                }
            }
            if (dice > 0) leaf_reward[node * dice + guess] = reward;
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
            if (config_.rave_k > 0) {
                Amaf& record = shared(path_offset[i], edge.action);
                record.visits++;
                record.total += reward;
            }
        }
        if (path_length == 0) break;  // Nothing to decide this tick.
    }

    last_iterations_ = static_cast<int>(iteration);
    auto keep = [&](const Choice& choice) {
        if (config_.record_search && !record_.features.empty()) {
            record_.tick = tick;
            record_.chosen = action_code(choice.action, root.origin_y());
            record_.arms.clear();
            for (const Edge& edge : tree[0].edges) record_.arms.push_back({action_code(edge.action, root.origin_y()), edge.visits});
        }
        if (config_.tree_reuse > 0) {
            if (!kept_) kept_ = std::make_shared<KeptTree>();
            kept_->nodes.clear();
            kept_->tick = -1;
            for (const Edge& edge : tree[0].edges) {
                if (edge.child < 0 || !same_action(edge.action, choice.action)) continue;
                copy_subtree(tree, edge.child, kept_->nodes);
                kept_->tick = tick + 1;
                kept_->root_score = root.score();
            }
        }
        return choice;
    };
    Choice best{Action{}, -1};
    int most_visits = -1;
    root_visits_.clear();
    if (!alive.empty()) {
        // The survivors, by mean. Early rounds can stop part-way when the budget runs out.
        double best_mean = -1e300;
        int top_visits = 0;
        for (int i : alive) top_visits = std::max(top_visits, tree[0].edges[i].visits);
        for (int i : alive) {
            const Edge& edge = tree[0].edges[i];
            if (edge.visits * 2 < top_visits || edge.visits == 0) continue;
            double mean = halving_value(edge);
            if (mean > best_mean) {
                best_mean = mean;
                best = {edge.action, mean};
            }
        }
        if (best.value >= 0 || best_mean > -1e300) return keep(best);
    }
    for (const Edge& edge : tree[0].edges) {
        root_visits_.push_back({edge.action, edge.visits});
        if (edge.visits > most_visits) {
            most_visits = edge.visits;
            best = {edge.action, edge.visits > 0 ? edge.total / edge.visits : 0};
        }
    }
    return keep(best);
}

double flood_outlook(Game game, int tick, const std::vector<std::pair<int, Action>>& plan, int lookahead, int limit,
                     double slope) {
    constexpr int kStride = 5;
    double sum = 0;
    int samples = 0;
    int last = std::min(kMaxTicks, tick + lookahead);
    for (int future = tick; future <= last; future++) {
        int elapsed = future - tick;
        Action action;
        for (const auto& step : plan) {
            if (step.first == elapsed) action = step.second;
        }
        game.step(future, action);
        if (elapsed % kStride == 0 && elapsed >= 2 * kStride) {
            sum += pocket_value(game, limit);
            if (slope > 0) sum += slope * (kCells - game.spawn_tiles());
            samples++;
        }
    }
    return samples > 0 ? sum / samples : 0;
}

int best_pocket_tile(const Game& game, int& size, int& covered) {
    uint8_t mask[kCells];
    size = game.spawn_mask(mask);
    covered = 0;
    int best = 0;
    for (int cell = 0; cell < kCells; cell++) {
        int count = pocket_cover(mask, cell);
        if (count > covered) {
            covered = count;
            best = cell;
        }
    }
    return best;
}

double volcano_outlook(Game game, int tick, const Action& first, int lookahead, int limit) {
    constexpr int kStride = 5;
    game.step(tick, first);
    double sum = 0;
    int samples = 0;
    int last = std::min(kMaxTicks, tick + lookahead);
    for (int future = tick + 1; future <= last; future++) {
        game.step(future, Action{});
        if ((future - tick) % kStride == 0 && future - tick >= 2 * kStride) {
            sum += pocket_value(game, limit);
            samples++;
        }
    }
    return samples > 0 ? sum / samples : 0;
}

// Returns the lava-only ticks it simulated.
int Agent::rank_volcanoes(const Game& root, int tick) {
    constexpr int kStride = 5;
    ranked_volcanoes_.clear();
    flood_bonus_.clear();
    Game bare = root;
    bare.clear_dinosaurs();
    int simulated = 0;
    int ticks = std::max(config_.volcano_rank_ticks, config_.flood_weight > 0 ? config_.volcano_lookahead : 0);

    struct Flood {
        double pockets = 0;  // Pocket value summed over the samples.
        int spawn = kCells;  // Spawn tiles left after volcano_rank_ticks.
    };
    auto flood = [&](Game game, const Action& first) {
        Flood result;
        game.step(tick, first);
        int last = std::min(kMaxTicks, tick + ticks);
        for (int future = tick + 1; future <= last; future++) {
            game.step(future, Action{});
            int elapsed = future - tick;
            if (config_.flood_weight > 0 && elapsed % kStride == 0 && elapsed >= 2 * kStride) {
                result.pockets += pocket_value(game, config_.pocket_limit);
            }
            if (elapsed == config_.volcano_rank_ticks) result.spawn = game.spawn_tiles();
        }
        simulated += last - tick + 1;
        return result;
    };

    double without = config_.flood_weight > 0 ? flood(bare, Action{}).pockets : 0;
    std::vector<std::pair<Flood, Action>> ranked;
    for (int cell = 0; cell < kCells; cell++) {
        if (!(bare.flags(cell) & kMountain)) continue;
        Action candidate;
        candidate.type = Action::Type::TriggerVolcano;
        candidate.x = cell % kWidth;
        candidate.y = cell / kWidth + bare.origin_y();
        ranked.push_back({flood(bare, candidate), candidate});
    }
    std::stable_sort(ranked.begin(), ranked.end(), [](const auto& a, const auto& b) {
        if (a.first.pockets != b.first.pockets) return a.first.pockets > b.first.pockets;
        return a.first.spawn < b.first.spawn;
    });
    for (int i = 0; i < std::min<int>(config_.volcanoes, static_cast<int>(ranked.size())); i++) {
        ranked_volcanoes_.push_back(ranked[i].second);
        flood_bonus_.push_back(config_.flood_weight * std::max(0.0, ranked[i].first.pockets - without));
    }
    return simulated;
}

bool Agent::pick_volcano(const Game& root, int tick, Action& action) {
    auto outlook = [&](const Game& game, const Action& first) {
        last_simulated_ += config_.volcano_lookahead;
        return volcano_outlook(game, tick, first, config_.volcano_lookahead, config_.pocket_limit);
    };

    Game bare = root;
    bare.clear_dinosaurs();
    double without = outlook(bare, Action{});
    double best = 0;
    for (int cell = 0; cell < kCells; cell++) {
        if (!(bare.flags(cell) & kMountain)) continue;
        Action candidate;
        candidate.type = Action::Type::TriggerVolcano;
        candidate.x = cell % kWidth;
        candidate.y = cell / kWidth + bare.origin_y();
        double value = outlook(bare, candidate);
        if (value > best) {
            best = value;
            action = candidate;
        }
    }
    return best >= config_.volcano_threshold && best > without;
}

Action Agent::policy_action(const Game& root, int tick) {
    uint8_t features[kPolicyBytes];
    encode_policy_features(root, tick, features);
    float logits[PolicyNet::kActions];
    config_.policy->evaluate(features, tick, logits);

    int best = 0;
    for (int cell = 0; cell < kCells; cell++) {
        if (root.meteor_count() < kMaxMeteors && logits[1 + cell] > logits[best]) best = 1 + cell;
        if (root.volcano_count() < kMaxVolcanoes && (root.flags(cell) & kMountain) && logits[1 + kCells + cell] > logits[best]) {
            best = 1 + kCells + cell;
        }
    }
    return action_from_code(best, root.origin_y());
}

void Agent::update_forecast(const Observation& observation, Game& root) {
    double began = now_ms();
    if (!inference_) inference_ = std::make_shared<TerrainInference>("volcanic_plains");
    // Each row is fed once, when it first appears: before any meteor can lower it.
    int top = observation.origin_y + kHeight - 1;
    for (int y = std::max(seen_row_ + 1, observation.origin_y); y <= top; y++) {
        inference_->observe(y, observation.elevation + (y - observation.origin_y) * kWidth);
    }
    seen_row_ = std::max(seen_row_, top);
    double observed = now_ms();
    inference_->work(config_.infer_ms);
    double worked = now_ms();
    if (std::getenv("SIM_INFER_DEBUG") && inference_->failed() && !infer_reported_) {
        infer_reported_ = true;
        std::fprintf(stderr, "terrain inference failed by tick %d (rows to %d)\n", observation.tick, inference_->last_row());
    }
    // The draws depend only on the hypotheses, the rows seen and the first row drawn, so they
    // are redrawn only when one of those changed.
    std::array<uint64_t, 3> sample_key = {inference_->version(), static_cast<uint64_t>(inference_->last_row()),
                                          static_cast<uint64_t>(top)};
    if (config_.terrain_samples && sample_key == sample_key_ && forecast_.first_y == top + 1) {
        // Same draws as last tick.
    } else if (config_.terrain_samples) {
        sample_key_ = sample_key;
        forecast_.first_y = top + 1;
        forecast_.count = 0;
        forecast_.samples = 0;
        // Draws need no majority vote, and the search sees at most two frame shifts ahead.
        constexpr int kSampleRows = 3;
        for (int count = std::min(kSampleRows, RowForecast::kMax); count > 0 && forecast_.samples == 0; count--) {
            bool ok = true;
            for (int s = 0; s < RowForecast::kSamples && ok; s++) {
                ok = inference_->sample_rows(top + 1, count, static_cast<uint32_t>(s),
                                             &forecast_.sample_elevation[s][0][0], &forecast_.sample_flags[s][0][0]);
            }
            if (ok) {
                forecast_.count = count;
                forecast_.samples = RowForecast::kSamples;
            }
        }
    } else {
        forecast_.first_y = top + 1;
        forecast_.count = 0;
        forecast_.samples = 0;
        for (int i = 0; i < RowForecast::kMax; i++) {
            if (!inference_->predict(top + 1 + i, forecast_.elevation[i], forecast_.flags[i])) break;
            forecast_.count++;
        }
    }
    if (std::getenv("SIM_INFER_DEBUG")) {
        double spent = now_ms() - began;
        infer_calls_++;
        infer_total_ms_ += spent;
        infer_max_ms_ = std::max(infer_max_ms_, spent);
        if (spent > 30)
            std::fprintf(stderr, "slow infer tick %d observe %.1f work %.1f sample %.1f hypotheses %d\n", observation.tick,
                         observed - began, worked - observed, now_ms() - worked, inference_->hypothesis_count());
        if (observation.tick % 250 == 0)
            std::fprintf(stderr, "infer tick %d calls %d mean %.3f ms max %.3f ms\n", observation.tick, infer_calls_,
                         infer_total_ms_ / infer_calls_, infer_max_ms_);
    }
    root.set_forecast(&forecast_);
}

Action Agent::act(const Observation& observation, const Game* truth) {
    double started = now_ms();  // Terrain inference below spends from the same budget.
    last_simulated_ = 0;
    root_visits_.clear();
    gaps_.clear();
    uint32_t rng_seed = static_cast<uint32_t>(observation.tick) * 2654435761u;
    Game root = Game::from_observation(observation, rng_seed);
    for (int i = 0; i < observation.dino_count; i++) highest_id_ = std::max(highest_id_, observation.dino_id[i]);
    if (config_.plan_waves) root.enable_waves(observation.tick, highest_id_);
    if (truth != nullptr) {
        root = *truth;
        root.hide(!config_.reveal_rows, !config_.reveal_rng, !config_.reveal_waves, rng_seed);
        // Hiding the waves turned wave planning off; put back the live bot's guessed waves.
        if (config_.plan_waves && !config_.reveal_waves) root.enable_waves(observation.tick, highest_id_);
        root.set_spawn_shield(config_.shield);
    }
    if (config_.terrain_infer) update_forecast(observation, root);
    if (config_.forecast_truth && truth != nullptr && truth->terrain() != nullptr) {
        int top = observation.origin_y + kHeight - 1;
        forecast_.first_y = top + 1;
        forecast_.count = std::min(config_.forecast_truth, RowForecast::kMax);
        for (int i = 0; i < RowForecast::kMax; i++) truth->terrain()->row(top + 1 + i, kWidth, forecast_.elevation[i], forecast_.flags[i]);
        root.set_forecast(&forecast_);
    }
    if (root.meteor_count() >= kMaxMeteors && root.volcano_count() >= kMaxVolcanoes) return Action{};

    if (config_.volcano_plan && root.volcano_count() == 0) {
        Action volcano;
        if (pick_volcano(root, observation.tick, volcano)) return volcano;
    }

    if (config_.policy != nullptr && config_.policy_only) return policy_action(root, observation.tick);

    double deadline = config_.time_budget_ms > 0 ? started + config_.time_budget_ms : 0;
    ranked_volcanoes_.clear();
    flood_bonus_.clear();
    int budget = config_.sim_budget;
    if (config_.volcano_rank && root.volcano_count() == 0) {
        constexpr int kLavaTicksPerTick = 3;  // Without dinosaurs a tick is much cheaper.
        budget -= rank_volcanoes(root, observation.tick) / kLavaTicksPerTick;
    }
    if (config_.mcts) return search_mcts(root, observation.tick, budget, deadline).action;
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
