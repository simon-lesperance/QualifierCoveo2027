#pragma once

#include <cstdint>

#include "game.hpp"
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
    double optimism = 0;         // Blend each edge's best return into its mean.

    // Where to aim. A meteor lands kMeteorDelay ticks later, so targets can be placed
    // around where dinosaurs are predicted to be then, as well as where they are now.
    bool aim_current = true;
    bool aim_predicted = false;  // MCTS limits prediction to root candidates.
    bool volcano_near_dinos = false;  // Rank volcanoes by distance to dinosaurs, not by map order.

    // Scores what each MCTS iteration leaves behind, on top of the points it earned.
    const ValueNet* net = nullptr;
    double value_weight = 1;
    int net_cost = 3;            // Simulated ticks charged per evaluation, so tests stay fair on time.

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
    void set_optimism(double weight) { config_.optimism = weight; }
    void set_targeting(bool aim_predicted, bool volcano_near_dinos) {
        config_.aim_predicted = aim_predicted;
        config_.volcano_near_dinos = volcano_near_dinos;
    }
    int last_simulated() const { return last_simulated_; }

private:
    struct Choice {
        Action action;
        double value;
    };

    Choice search(const Game& root, int tick, int budget, double deadline_ms);
    Choice search_mcts(const Game& root, int tick, int budget, double deadline_ms);

    AgentConfig config_;
    int last_simulated_ = 0;
};

}  // namespace sim
