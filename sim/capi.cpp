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

SIM_EXPORT void* sim_agent_create(int beam, int radius, int horizon, int volcanoes) {
    sim::AgentConfig config;
    config.beam = beam;
    config.radius = radius;
    config.horizon = horizon;
    config.volcanoes = volcanoes;
    config.sim_budget = 1 << 30;  // The clock is the limit in a live game.
    return new sim::Agent(config);
}

SIM_EXPORT void sim_agent_destroy(void* agent) {
    delete static_cast<sim::Agent*>(agent);
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
