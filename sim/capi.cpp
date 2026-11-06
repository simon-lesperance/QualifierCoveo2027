// C interface for the Python bot, loaded through ctypes.

#include "agent.hpp"

#ifdef _WIN32
#define SIM_EXPORT extern "C" __declspec(dllexport)
#else
#define SIM_EXPORT extern "C" __attribute__((visibility("default")))
#endif

SIM_EXPORT int sim_observation_size() {
    return static_cast<int>(sizeof(sim::Observation));
}

SIM_EXPORT void* sim_agent_create(int radius, int horizon, int volcanoes, int depth, double widening, double exploration) {
    sim::AgentConfig config;
    config.mcts = true;
    config.radius = radius;
    config.horizon = horizon;
    config.volcanoes = volcanoes;
    config.mcts_depth = depth;
    config.widening = widening;
    config.exploration = exploration;
    config.sim_budget = 1 << 30;  // The clock is the limit in a live game.
    return new sim::Agent(config);
}

SIM_EXPORT void sim_agent_destroy(void* agent) {
    delete static_cast<sim::Agent*>(agent);
}

SIM_EXPORT void sim_agent_set_targeting(void* agent, int aim_predicted, int volcano_near_dinos) {
    static_cast<sim::Agent*>(agent)->set_targeting(aim_predicted != 0, volcano_near_dinos != 0);
}

SIM_EXPORT void sim_agent_set_optimism(void* agent, double weight) {
    static_cast<sim::Agent*>(agent)->set_optimism(weight);
}

// Writes the chosen action: type 0 none, 1 meteor, 2 volcano.
SIM_EXPORT void sim_agent_act(void* agent, const sim::Observation* observation, double time_ms, int* type, int* x, int* y) {
    auto* instance = static_cast<sim::Agent*>(agent);
    instance->set_time_budget(time_ms);
    sim::Action action = instance->act(*observation);
    *type = static_cast<int>(action.type);
    *x = action.x;
    *y = action.y;
}

// Simulated ticks spent on the last act() call.
SIM_EXPORT int sim_agent_last_simulated(void* agent) {
    return static_cast<sim::Agent*>(agent)->last_simulated();
}

// Starts an anytime search for this observation. Returns 0 when there is nothing to decide.
SIM_EXPORT int sim_agent_begin(void* agent, const sim::Observation* observation) {
    return static_cast<sim::Agent*>(agent)->begin(*observation) ? 1 : 0;
}

// Grows the current search for up to time_ms, then writes its best action (same encoding as act).
SIM_EXPORT void sim_agent_resume(void* agent, double time_ms, int* type, int* x, int* y) {
    sim::Action action = static_cast<sim::Agent*>(agent)->resume(time_ms);
    *type = static_cast<int>(action.type);
    *x = action.x;
    *y = action.y;
}

// Asks a running resume() on another thread to return early. Safe to call from any thread.
SIM_EXPORT void sim_agent_stop(void* agent) {
    static_cast<sim::Agent*>(agent)->stop();
}
