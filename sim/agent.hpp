#pragma once

#include <cstdint>
#include <vector>

#include "game.hpp"
#include "policy_net.hpp"
#include "value_net.hpp"

namespace sim {

struct AgentConfig {
    int sim_budget = 6500;       // Simulated ticks per move, the estimated server capacity.
    double time_budget_ms = 0;   // 0 means no clock limit, which keeps tests deterministic.
    int beam = 12;
    int radius = 3;
    int horizon = kMeteorDelay;  // Ticks rolled out after each action, so in-flight meteors land.
    int volcanoes = 6;
    int samples = 1;             // Independent guesses at the hidden RNG, which then vote.

    // Open-loop MCTS instead of beam search. Every iteration guesses the hidden
    // tie-breaks afresh, so values average over them.
    bool mcts = false;
    double exploration = 0.5;
    int mcts_depth = 3;          // Decisions per iteration before the rollout.
    double widening = 3;         // A node may try 1 + widening * sqrt(visits) actions.

    // Scores what each MCTS iteration leaves behind, on top of the points it earned.
    const ValueNet* net = nullptr;
    double value_weight = 1;
    int net_cost = 3;            // Simulated ticks charged per evaluation, so tests stay fair on time.

    // Ranks the root's actions, and steers visits there, as in AlphaZero. It can also
    // name targets far from any dinosaur, which the usual action list leaves out.
    PolicyNet* policy = nullptr;
    double puct = 1;
    int policy_targets = 30;     // How many of its favourite actions to add to the list.
    double prior_floor = 0.1;    // Share of the prior spread over all actions, so none is ruled out.
    int policy_cost = 1500;      // Simulated ticks charged per evaluation, so tests stay fair on time.

    // Plans include waves at guessed tiles, drawn afresh each iteration. When the spawn
    // area is small, a meteor timed for the wave's first tick can kill all of it.
    bool plan_waves = false;
    int pocket_limit = 60;       // Aim at the spawn area when it has at most this many tiles.
    int pocket_targets = 4;      // How many of the best-covering tiles to add to the action list.
    double pocket_weight = 0;    // Share of the next wave's best-case blast added to each leaf's value.

    // Experiments only: plan with information a live bot can't have.
    bool reveal_rows = false;
    bool reveal_rng = false;
    bool reveal_waves = false;   // Without reveal_rng, this simulates waves at guessed positions.
    bool shield = false;         // Plans can't count on spawn-tick kills.
    bool grid = false;           // Also consider empty ground, to meet a wave before it spawns.
};

// Plans from what the server sends: no seed, no future rows, no knowledge of spawns.
class Agent {
public:
    explicit Agent(AgentConfig config) : config_(config) {}

    Action act(const Observation& observation, const Game* truth = nullptr);

    void set_time_budget(double ms) { config_.time_budget_ms = ms; }
    void set_net(const ValueNet* net, double weight) {
        config_.net = net;
        config_.value_weight = weight;
    }
    void set_policy(PolicyNet* policy) { config_.policy = policy; }
    void set_waves(int pocket_limit, int pocket_targets, double pocket_weight) {
        config_.plan_waves = true;
        config_.pocket_limit = pocket_limit;
        config_.pocket_targets = pocket_targets;
        config_.pocket_weight = pocket_weight;
    }
    int last_simulated() const { return last_simulated_; }

    // How the last MCTS search spread its visits over the root's actions.
    struct RootVisit {
        Action action;
        int visits;
    };
    const std::vector<RootVisit>& root_visits() const { return root_visits_; }

private:
    struct Choice {
        Action action;
        double value;
    };

    Choice search(const Game& root, int tick, int budget, double deadline_ms);
    Choice search_mcts(const Game& root, int tick, int budget, double deadline_ms);

    AgentConfig config_;
    int last_simulated_ = 0;
    int highest_id_ = 0;
    std::vector<RootVisit> root_visits_;
};

}  // namespace sim
