// A batch of games for training policies from Python. Not shipped with the bot.
//
// Each step takes one action per game and writes the next inputs, so nothing
// is copied twice. Finished games restart with a new seed by themselves.
//
// Actions: 0 is nothing, 1 + cell launches a meteor, 401 + cell triggers a volcano.
// Cells are frame-relative: row * width + column.

#include <cstdint>
#include <cstring>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "features.hpp"
#include "game.hpp"

#ifdef _WIN32
#define SIM_EXPORT extern "C" __declspec(dllexport)
#else
#define SIM_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

using sim::Action;
using sim::Game;
using sim::Terrain;

constexpr const char* kBiomes[3] = {"deep_crevasse", "mountain_range", "volcanic_plains"};

struct Slot {
    std::unique_ptr<Terrain> terrain;
    std::optional<Game> game;
    int tick = 1;
};

struct VecEnv {
    std::vector<Slot> slots;
    std::vector<int> biomes;
    std::string prefix;
    int threads = 1;
};


// Seeds depend only on the slot and how many games it has played.
void restart(VecEnv& env, int index, long long number) {
    Slot& slot = env.slots[index];
    std::string seed = env.prefix + "-" + std::to_string(index) + "-" + std::to_string(number);
    int biome = env.biomes[(index + number) % env.biomes.size()];
    slot.terrain = std::make_unique<Terrain>(seed, kBiomes[biome]);
    slot.game.emplace(*slot.terrain, seed);
    slot.tick = 1;
}

Action decode(int code, const Game& game) {
    Action action;
    if (code <= 0) return action;
    int cell = (code - 1) % sim::kCells;
    action.type = code <= sim::kCells ? Action::Type::LaunchMeteor : Action::Type::TriggerVolcano;
    action.x = cell % sim::kWidth;
    action.y = cell / sim::kWidth + game.origin_y();
    return action;
}

template <typename Work>
void parallel(int count, int threads, Work work) {
    std::vector<std::thread> pool;
    for (int t = 0; t < threads; t++) {
        pool.emplace_back([=] {
            for (int i = count * t / threads; i < count * (t + 1) / threads; i++) work(i);
        });
    }
    for (std::thread& thread : pool) thread.join();
}

}  // namespace

SIM_EXPORT int vec_feature_bytes() {
    return sim::kPolicyBytes;
}

// biome_mask has bit 0 for deep_crevasse, 1 for mountain_range, 2 for volcanic_plains.
// With desync, games start part-way through so they don't all finish together.
SIM_EXPORT void* vec_create(int envs, int threads, int biome_mask, const char* prefix, int desync) {
    auto* env = new VecEnv();
    env->slots.resize(envs);
    env->prefix = prefix;
    env->threads = threads;
    for (int biome = 0; biome < 3; biome++) {
        if (biome_mask & (1 << biome)) env->biomes.push_back(biome);
    }
    parallel(envs, threads, [env, desync](int i) {
        restart(*env, i, 0);
        if (desync) {
            Slot& slot = env->slots[i];
            int skip = static_cast<int>((i * 2654435761ull) % (sim::kMaxTicks - 1));
            for (int t = 0; t < skip; t++) slot.game->step(slot.tick++, Action{});
        }
    });
    return env;
}

SIM_EXPORT void vec_destroy(void* handle) {
    delete static_cast<VecEnv*>(handle);
}

SIM_EXPORT void vec_observe(void* handle, uint8_t* features, int32_t* ticks) {
    auto* env = static_cast<VecEnv*>(handle);
    parallel(static_cast<int>(env->slots.size()), env->threads, [=](int i) {
        const Slot& slot = env->slots[i];
        sim::encode_policy_features(*slot.game, slot.tick, features + i * sim::kPolicyBytes);
        ticks[i] = slot.tick;
    });
}

// scores[i] is the final score when games[i] finished on this step, otherwise -1.
SIM_EXPORT void vec_step(void* handle, const int32_t* actions, uint8_t* features, int32_t* ticks,
                         float* rewards, int32_t* kills, int32_t* scores, int64_t* games) {
    auto* env = static_cast<VecEnv*>(handle);
    parallel(static_cast<int>(env->slots.size()), env->threads, [=](int i) {
        Slot& slot = env->slots[i];
        Game& game = *slot.game;
        int score = game.score();
        int killed = game.total_kills();
        game.step(slot.tick, decode(actions[i], game));
        rewards[i] = static_cast<float>(game.score() - score);
        kills[i] = game.total_kills() - killed;
        slot.tick++;
        scores[i] = -1;
        if (slot.tick > sim::kMaxTicks) {
            scores[i] = game.score();
            games[i]++;
            restart(*env, i, games[i]);
        }
        sim::encode_policy_features(*slot.game, slot.tick, features + i * sim::kPolicyBytes);
        ticks[i] = slot.tick;
    });
}
