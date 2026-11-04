#pragma once

#include <cstdint>
#include <string_view>

#include "rng.hpp"
#include "terrain.hpp"

namespace sim {

constexpr int kWidth = 20;
constexpr int kHeight = 20;
constexpr int kCells = kWidth * kHeight;
constexpr int kMaxTicks = 1000;
constexpr int kMaxMeteors = 3;
constexpr int kMaxVolcanoes = 1;
constexpr int kMeteorDelay = 4;
constexpr int kMeteorRadius = 2;
constexpr int kShiftInterval = 5;
constexpr int kWaveCadence = 100;
constexpr int kDinosPerWave = 10;
constexpr int kDinoCap = 20;

enum class Species : uint8_t { Stegosaurus, Velociraptor, Triceratops, Tyrannosaurus };

struct Dino {
    int32_t id;
    int16_t spawn_turn;
    int16_t cell;  // Frame-relative: row * kWidth + x. Rows move with the frame.
    Species species;
    bool alive;
};

struct Meteor {
    int16_t x;
    int16_t y;  // World coordinates: a target can scroll out of the frame before impact.
    int16_t launched_on;
    int8_t turns;
};

struct Volcano {
    int16_t x;
    int16_t y;
};

struct Action {
    enum class Type : uint8_t { None, LaunchMeteor, TriggerVolcano };
    Type type = Type::None;
    int x = 0;
    int y = 0;  // World coordinates.
};

// What the server sends each tick, flattened so ctypes can fill it directly.
struct Observation {
    int32_t tick;
    int32_t origin_y;
    int32_t dino_count;
    int32_t dino_id[kDinoCap];
    int32_t dino_species[kDinoCap];
    int32_t dino_x[kDinoCap];
    int32_t dino_y[kDinoCap];  // World coordinates, as for meteors and volcanoes.
    int32_t dino_age[kDinoCap];
    int32_t meteor_count;
    int32_t meteor_x[kMaxMeteors];
    int32_t meteor_y[kMaxMeteors];
    int32_t meteor_turns[kMaxMeteors];
    int32_t volcano_count;
    int32_t volcano_x[kMaxVolcanoes];
    int32_t volcano_y[kMaxVolcanoes];
    uint8_t elevation[kCells];
    uint8_t flags[kCells];  // TileFlag bits.
    uint8_t corpses[kCells];
};

// All mutable game state. Trivially copyable, so search can branch with a plain copy.
class Game {
public:
    Game(const Terrain& terrain, std::string_view seed);

    // A game for planning, built only from what the bot can see. It has no terrain,
    // so new rows repeat the bottom row, and no waves, so plans can't chase imagined
    // dinosaurs. The hidden tie-breaks come from rng_seed.
    static Game from_observation(const Observation& observation, uint32_t rng_seed);

    // Removes what a live bot can't know from a copy of the true game. For
    // experiments that measure what each hidden thing is worth.
    void hide(bool rows, bool rng, bool waves, uint32_t rng_seed);

    // What the server would send at the start of `tick`.
    Observation observe(int tick) const;

    // Plays turn number `turn` (1-based), as the engine's playOneTick does.
    // Returns false if the action was rejected.
    bool step(int turn, Action action);

    // Not in the real game. Lets a search measure what foreseeing spawns is worth:
    // a dinosaur survives any impact on the tick it spawns.
    void set_spawn_shield(bool on) { spawn_shield_ = on; }

    // Tiles a new wave could spawn on right now.
    int spawn_tiles() const {
        int16_t bag[kCells];
        return spawn_bag(bag);
    }

    int score() const { return score_; }
    int origin_y() const { return origin_y_; }
    int total_kills() const { return total_kills_; }
    int kills_this_tick() const { return kills_; }

    int dino_count() const { return dino_count_; }
    const Dino& dino(int slot) const { return dinos_[slot]; }
    int meteor_count() const { return meteor_count_; }
    const Meteor& meteor(int slot) const { return meteors_[slot]; }
    int volcano_count() const { return volcano_count_; }
    const Volcano& volcano(int slot) const { return volcanoes_[slot]; }

    int elevation(int cell) const { return elevation_[cell]; }
    uint8_t flags(int cell) const { return flags_[cell]; }
    bool corpse(int cell) const { return corpses_[cell]; }

private:
    struct TickContext;

    explicit Game(uint32_t rng_seed) : terrain_(nullptr), rng_(rng_seed) {}

    bool apply(int turn, Action action);
    void update(int turn);
    void maybe_spawn_wave(int turn);
    void spawn_wave(int turn);
    int spawn_bag(int16_t* bag) const;
    void update_meteors(int turn);
    void resolve_meteor_impacts(int turn);
    void propagate_lava(int turn);
    void move_dinosaurs(int turn);
    void resolve_eating(int16_t* target, int turn);
    void resolve_conflicts(const int16_t* target, int16_t* result) const;
    void frame_shift(int turn);
    void settle_tick_score();
    void compact();

    enum class Cause { Meteor, Lava, Eaten, OffTail };
    void eliminate(Dino& dino, int turn, Cause cause);

    int decide(TickContext& ctx, int slot);
    int decide_stegosaurus(TickContext& ctx, int slot);
    int decide_velociraptor(TickContext& ctx, int slot);
    int decide_triceratops(TickContext& ctx, int slot);
    int decide_tyrannosaurus(TickContext& ctx, int slot);
    void build_packs(TickContext& ctx);

    const Terrain* terrain_;
    Arc4 rng_;

    int16_t origin_y_ = 0;
    int16_t last_wave_turn_ = 0;
    int16_t wave_cursor_ = 0;
    int32_t next_dino_id_ = 1;
    int32_t score_ = 0;
    int32_t total_kills_ = 0;
    int32_t kill_base_ = 0;
    int8_t kills_ = 0;
    bool map_clear_last_tick_ = false;
    bool spawn_shield_ = false;
    bool waves_enabled_ = true;

    int8_t dino_count_ = 0;
    int8_t meteor_count_ = 0;
    int8_t volcano_count_ = 0;
    Dino dinos_[kDinoCap];
    Meteor meteors_[kMaxMeteors];
    Volcano volcanoes_[kMaxVolcanoes];

    uint8_t elevation_[kCells];
    uint8_t flags_[kCells];
    bool corpses_[kCells];
};

}  // namespace sim
