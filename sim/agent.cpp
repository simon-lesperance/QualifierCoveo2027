#include "agent.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <unordered_set>
#include <vector>

namespace sim {

namespace {

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

void list_actions(const Game& game, const AgentConfig& config, std::vector<Action>& actions) {
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

Action Agent::act(const Observation& observation) {
    Game root = Game::from_observation(observation, static_cast<uint32_t>(observation.tick) * 2654435761u);
    if (root.meteor_count() >= kMaxMeteors && root.volcano_count() >= kMaxVolcanoes) return Action{};

    double deadline = config_.time_budget_ms > 0 ? now_ms() + config_.time_budget_ms : 0;
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
