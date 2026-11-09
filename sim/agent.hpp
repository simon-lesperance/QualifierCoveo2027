#pragma once

#include <cstdint>
#include <utility>
#include <memory>
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

    // How many fixed guesses at the hidden RNG to cycle through, keeping each node's state
    // per guess so iterations don't replay the tree. 0 draws a fresh guess every iteration.
    int dice = 0;

    // RAVE: lines through the same action at the same tick share what they learn, whatever
    // led there. A node leans on that for about rave_k visits. 0 turns it off.
    double rave_k = 0;
    bool rave_order = false;     // Also try actions with a good shared record first.

    // Scores what each MCTS iteration leaves behind, on top of the points it earned.
    const ValueNet* net = nullptr;
    double value_weight = 1;
    const ValueNet* net2 = nullptr;  // If set, leaves use the mean of both nets, at twice the cost.
    int net_cost = 3;            // Simulated ticks charged per evaluation, so tests stay fair on time.

    // Ranks the root's actions, and steers visits there, as in AlphaZero. It can also
    // name targets far from any dinosaur, which the usual action list leaves out.
    PolicyNet* policy = nullptr;
    double puct = 1;
    int policy_targets = 30;     // How many of its favourite actions to add to the list.
    double prior_floor = 0.1;    // Share of the prior spread over all actions, so none is ruled out.
    int policy_cost = 1500;      // Simulated ticks charged per evaluation, so tests stay fair on time.
    bool policy_only = false;    // Plays the policy's top action, with no search.
    int root_halving = 0;        // Above 0, the root tries this many actions by sequential halving.
    double halving_prior = 0;    // How much the prior counts when halving.
    bool halving_crn = false;    // The k-th visit of every root action sees the same dice guess.
    double halving_skew = 0;     // Round r gets budget in proportion to (1 + skew)^-r; 0 splits evenly.
    double halving_max = 0;      // Halving ranks by mean + halving_max * (best - mean).
    // With halving, keeps the chosen action's subtree for the next tick. 1 grafts it below the
    // new root's actions, 2 also counts its visits at the root, 0 starts afresh each tick.
    int tree_reuse = 0;
    bool record_search = false;  // Keeps each search's root features and halving visits, for training.
    double tree_puct = 0;        // Above 0, nodes ranked by the tree net also use its prior this way.
    PolicyNet* tree_policy = nullptr;  // A cheaper net for inside the tree. Without it, policy does that too.
    int policy_depth = 0;        // Also orders the widening of nodes down to this depth.
    int policy_tree_cost = 0;    // Simulated ticks charged for each of those evaluations.
    int policy_tree_visits = 0;  // Waits for this many visits, so rarely seen nodes cost nothing.
    int policy_deep_visits = 0;  // Below depth 1, waits for this many visits instead.

    // Plans include waves at guessed tiles, drawn afresh each iteration. When the spawn
    // area is small, a meteor timed for the wave's first tick can kill all of it.
    bool plan_waves = false;
    int pocket_limit = 60;       // Aim at the spawn area when it has at most this many tiles.
    int pocket_targets = 4;      // How many of the best-covering tiles to add to the action list.
    double pocket_weight = 0;    // Share of the next wave's best-case blast added to each leaf's value.
    // Above 0, rollouts fire at the footprint covering the most points whenever it covers at
    // least this many dinosaurs, so a herd built in the tree pays off within the horizon.
    int rollout_fire = 0;
    double group_weight = 0;     // Share of the best single footprint's points (multiplier included) added to each leaf.

    // With no volcano active, tries every mountain with lava alone, and triggers the one
    // that leaves the smallest spawn area if that is worth at least volcano_threshold.
    bool volcano_plan = false;
    int volcano_lookahead = 40;
    double volcano_threshold = 1000;

    // Offers search the mountains whose lava would leave the smallest spawn area after
    // volcano_rank_ticks, instead of an even spread of them.
    bool volcano_rank = false;
    int volcano_rank_ticks = 30;

    // Credits a volcano with what its flood could earn beyond the search's view: this
    // share of the pocket value summed over volcano_lookahead ticks of lava alone.
    double flood_weight = 0;

    // Experiments only: plan with information a live bot can't have.
    bool reveal_rows = false;
    bool reveal_rng = false;
    bool reveal_waves = false;   // Without reveal_rng, this simulates waves at guessed positions.
    bool shield = false;         // Plans can't count on spawn-tick kills.
    bool grid = false;           // Also consider empty ground, to meet a wave before it spawns.
};

// Average points a meteor on a new wave could expect over the next `lookahead` ticks,
// after playing `first`, with lava alone. Dinosaurs are removed from a copy of the game.
double volcano_outlook(Game game, int tick, const Action& first, int lookahead, int limit);

// The same, for a plan of (ticks from now, action) pairs. `slope` adds that many points
// for every tile taken off the spawn area, which gives a greedy search a direction to
// follow while the pocket value is still zero.
double flood_outlook(Game game, int tick, const std::vector<std::pair<int, Action>>& plan, int lookahead, int limit,
                     double slope = 0);

// The tile whose blast covers the most spawn tiles, and how many there are of each.
int best_pocket_tile(const Game& game, int& size, int& covered);

// Milliseconds on the monotonic clock used for search deadlines.
double now_ms();

// Plans from what the server sends: no seed, no future rows, no knowledge of spawns.
struct KeptTree;

class Agent {
public:
    int last_iterations() const { return last_iterations_; }
    explicit Agent(AgentConfig config) : config_(config) {}

    Action act(const Observation& observation, const Game* truth = nullptr);

    void set_time_budget(double ms) { config_.time_budget_ms = ms; }
    void set_net(const ValueNet* net, double weight) {
        config_.net = net;
        config_.value_weight = weight;
    }
    void set_policy(PolicyNet* policy, double puct = 1) {
        config_.policy = policy;
        config_.puct = puct;
    }
    void set_tree_policy(PolicyNet* policy, int depth, int visits) {
        config_.tree_policy = policy;
        config_.policy_depth = depth;
        config_.policy_tree_visits = visits;
    }
    void set_root_halving(int actions, double prior_weight) {
        config_.root_halving = actions;
        config_.halving_prior = prior_weight;
    }
    void set_halving_options(bool crn, double max_weight) {
        config_.halving_crn = crn;
        config_.halving_max = max_weight;
    }
    void set_tree_reuse(int mode) { config_.tree_reuse = mode; }
    void set_volcano_rank(bool on) { config_.volcano_rank = on; }
    void set_flood_weight(double weight) { config_.flood_weight = weight; }
    void set_waves(int pocket_limit, int pocket_targets, double pocket_weight) {
        config_.plan_waves = true;
        config_.pocket_limit = pocket_limit;
        config_.pocket_targets = pocket_targets;
        config_.pocket_weight = pocket_weight;
    }
    void set_group_weight(double weight) { config_.group_weight = weight; }
    int last_simulated() const { return last_simulated_; }

    // Pauses of at least kGapMs between clock readings in the last timed search, as
    // [start, end] in now_ms() time. They show when the host took the CPU away.
    struct Gap {
        double start;
        double end;
    };
    static constexpr double kGapMs = 5.0;
    static constexpr size_t kMaxGaps = 16;
    const std::vector<Gap>& last_gaps() const { return gaps_; }

    // How the last MCTS search spread its visits over the root's actions.
    struct RootVisit {
        Action action;
        int visits;
    };
    const std::vector<RootVisit>& root_visits() const { return root_visits_; }

    // With record_search: the root's policy features, each halving action's code and visits,
    // and the code of the action chosen. tick is -1 when the last move had no search.
    struct SearchRecord {
        int tick = -1;
        std::vector<uint8_t> features;
        int chosen = 0;
        std::vector<std::pair<int, int>> arms;
    };
    const SearchRecord& last_record() const { return record_; }

private:
    struct Choice {
        Action action;
        double value;
    };

    bool pick_volcano(const Game& root, int tick, Action& action);
    int rank_volcanoes(const Game& root, int tick);
    Choice search(const Game& root, int tick, int budget, double deadline_ms);
    Choice search_mcts(const Game& root, int tick, int budget, double deadline_ms);
    Action policy_action(const Game& root, int tick);

    AgentConfig config_;
    int last_simulated_ = 0;
    int highest_id_ = 0;

    struct CachedState {
        Game game;
        int turn;
        int depth;
    };
    std::vector<CachedState> cached_states_;  // Kept between ticks to reuse the memory.
    std::vector<Action> ranked_volcanoes_;
    std::vector<double> flood_bonus_;  // Parallel to ranked_volcanoes_.
    std::vector<RootVisit> root_visits_;
    std::vector<Gap> gaps_;
    int last_iterations_ = 60;
    std::shared_ptr<KeptTree> kept_;
    SearchRecord record_;
};

}  // namespace sim
