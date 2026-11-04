#pragma once

#include <cstdint>

#include "game.hpp"

namespace sim {

struct AgentConfig {
    int sim_budget = 6500;       // Simulated ticks per move, the estimated server capacity.
    double time_budget_ms = 0;   // 0 means no clock limit, which keeps tests deterministic.
    int beam = 12;
    int radius = 3;
    int horizon = kMeteorDelay;  // Ticks rolled out after each action, so in-flight meteors land.
    int volcanoes = 6;
    int samples = 1;             // Independent guesses at the hidden RNG, which then vote.
};

// Plans from what the server sends: no seed, no future rows, no knowledge of spawns.
class Agent {
public:
    explicit Agent(AgentConfig config) : config_(config) {}

    Action act(const Observation& observation);

    void set_time_budget(double ms) { config_.time_budget_ms = ms; }
    int last_simulated() const { return last_simulated_; }

private:
    struct Choice {
        Action action;
        double value;
    };

    Choice search(const Game& root, int tick, int budget, double deadline_ms);

    AgentConfig config_;
    int last_simulated_ = 0;
};

}  // namespace sim
