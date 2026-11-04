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

struct TreeNode {
    bool opened = false;
    int visits = 0;
    int next_untried = 0;
    std::vector<Action> untried;
    std::vector<Edge> edges;
};

}  // namespace

Agent::Choice Agent::search_mcts(const Game& root, int tick, int budget, double deadline_ms) {
    std::vector<TreeNode> tree(1);
    tree.reserve(4096);
    std::vector<Action> actions;
    double lowest_reward = std::numeric_limits<double>::infinity();
    double highest_reward = -std::numeric_limits<double>::infinity();
    uint32_t iteration = 0;

    // The prior of each action at the root, by action code.
    std::vector<float> prior;
    if (config_.policy != nullptr) {
        prior.assign(PolicyNet::kActions, 0);
        uint8_t features[kFeatureBytes];
        encode_features(root, tick, features);
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

    while (budget > 0 && (deadline_ms <= 0 || now_ms() < deadline_ms)) {
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

            int edge_index = -1;
            size_t allowed = 1 + static_cast<size_t>(config_.widening * std::sqrt(static_cast<double>(tree[node].visits)));
            if (tree[node].edges.size() < allowed &&
                tree[node].next_untried < static_cast<int>(tree[node].untried.size())) {
                Edge edge;
                edge.action = tree[node].untried[tree[node].next_untried++];
                if (node == 0 && !prior.empty()) edge.prior = prior[action_code(edge.action, root.origin_y())];
                tree[node].edges.push_back(edge);
                edge_index = static_cast<int>(tree[node].edges.size()) - 1;
            } else {
                double log_visits = std::log(tree[node].visits + 1.0);
                double reward_range = std::max(1.0, highest_reward - lowest_reward);
                double root_factor = std::sqrt(static_cast<double>(tree[node].visits));
                bool use_prior = node == 0 && !prior.empty();
                double best_score = -1e18;
                for (int i = 0; i < static_cast<int>(tree[node].edges.size()); i++) {
                    const Edge& edge = tree[node].edges[i];
                    double score = 1e17;
                    if (edge.visits > 0) {
                        score = (edge.total / edge.visits - lowest_reward) / reward_range;
                        if (use_prior) score += config_.puct * edge.prior * root_factor / (1 + edge.visits);
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
        if (config_.pocket_weight > 0) reward += config_.pocket_weight * pocket_value(game, config_.pocket_limit);
        if (!flood_bonus_.empty()) {
            for (int i = 0; i < path_length; i++) {
                const Action& taken = tree[path_node[i]].edges[path_edge[i]].action;
                if (taken.type != Action::Type::TriggerVolcano) continue;
                for (size_t j = 0; j < ranked_volcanoes_.size(); j++) {
                    if (ranked_volcanoes_[j].x == taken.x && ranked_volcanoes_[j].y == taken.y) reward += flood_bonus_[j];
                }
            }
        }
        lowest_reward = std::min(lowest_reward, reward);
        highest_reward = std::max(highest_reward, reward);
        tree[0].visits++;
        for (int i = 0; i < path_length; i++) {
            Edge& edge = tree[path_node[i]].edges[path_edge[i]];
            edge.visits++;
            edge.total += reward;
            if (i > 0) tree[path_node[i]].visits++;
        }
        if (path_length == 0) break;  // Nothing to decide this tick.
    }

    Choice best{Action{}, -1};
    int most_visits = -1;
    root_visits_.clear();
    for (const Edge& edge : tree[0].edges) {
        root_visits_.push_back({edge.action, edge.visits});
        if (edge.visits > most_visits) {
            most_visits = edge.visits;
            best = {edge.action, edge.visits > 0 ? edge.total / edge.visits : 0};
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

Action Agent::act(const Observation& observation, const Game* truth) {
    last_simulated_ = 0;
    root_visits_.clear();
    uint32_t rng_seed = static_cast<uint32_t>(observation.tick) * 2654435761u;
    Game root = Game::from_observation(observation, rng_seed);
    for (int i = 0; i < observation.dino_count; i++) highest_id_ = std::max(highest_id_, observation.dino_id[i]);
    if (config_.plan_waves) root.enable_waves(observation.tick, highest_id_);
    if (truth != nullptr) {
        root = *truth;
        root.hide(!config_.reveal_rows, !config_.reveal_rng, !config_.reveal_waves, rng_seed);
        root.set_spawn_shield(config_.shield);
    }
    if (root.meteor_count() >= kMaxMeteors && root.volcano_count() >= kMaxVolcanoes) return Action{};

    if (config_.volcano_plan && root.volcano_count() == 0) {
        Action volcano;
        if (pick_volcano(root, observation.tick, volcano)) return volcano;
    }

    double deadline = config_.time_budget_ms > 0 ? now_ms() + config_.time_budget_ms : 0;
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
