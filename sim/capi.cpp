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

// Returns 0 if the file can't be read. The net must outlive the agent, so it's never freed.
SIM_EXPORT int sim_agent_load_net(void* agent, const char* path, double weight) {
    auto* net = new sim::ValueNet();
    if (!net->load(path)) {
        delete net;
        return 0;
    }
    static_cast<sim::Agent*>(agent)->set_net(net, weight);
    return 1;
}

SIM_EXPORT void sim_agent_set_waves(void* agent, int pocket_limit, int pocket_targets, double pocket_weight) {
    static_cast<sim::Agent*>(agent)->set_waves(pocket_limit, pocket_targets, pocket_weight);
}

SIM_EXPORT void sim_agent_set_volcano_rank(void* agent, int on) {
    static_cast<sim::Agent*>(agent)->set_volcano_rank(on != 0);
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
