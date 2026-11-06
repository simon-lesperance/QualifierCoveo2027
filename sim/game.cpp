#include "game.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <type_traits>

namespace sim {

static_assert(std::is_trivially_copyable_v<Game>);

namespace {

constexpr int kNoHazard = 127;
constexpr int kUnreachable = 32767;
constexpr int kStegoTargetRow = 2 * kHeight / 3;
constexpr int kStegoCycle = 2 * (kWidth - 1);
constexpr int kTrailingRows = 3;  // Dinosaurs keep this far from the frame's trailing edge.
constexpr int kPackReach = 6;
constexpr int kPackMinRow = 2;
constexpr double kPackRowWeight = 0.2;
constexpr int kMaxFields = 24;
constexpr int kMaxThreats = kCells + kMaxMeteors;

constexpr int kWaveMatrix[10][4] = {
    {10, 0, 0, 0}, {8, 2, 0, 0}, {6, 4, 0, 0}, {0, 2, 8, 0}, {6, 4, 0, 0},
    {0, 2, 7, 1},  {6, 4, 0, 0}, {0, 2, 7, 1}, {2, 2, 3, 3}, {3, 0, 4, 3},
};

constexpr int kSteps[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};

constexpr int column_of(int cell) { return cell % kWidth; }
constexpr int row_of(int cell) { return cell / kWidth; }
constexpr int cell_at(int row, int column) { return row * kWidth + column; }
constexpr bool in_frame(int row, int column) {
    return row >= 0 && row < kHeight && column >= 0 && column < kWidth;
}

constexpr auto make_neighbors() {
    std::array<std::array<int16_t, 4>, kCells> table{};
    for (int cell = 0; cell < kCells; cell++) {
        for (int i = 0; i < 4; i++) {
            int row = row_of(cell) + kSteps[i][1];
            int column = column_of(cell) + kSteps[i][0];
            table[cell][i] = in_frame(row, column) ? static_cast<int16_t>(cell_at(row, column)) : int16_t{-1};
        }
    }
    return table;
}

constexpr auto kNeighbors = make_neighbors();

// JS Math.round: halves round up, unlike std::round.
double js_round(double value) {
    double lower = std::floor(value);
    return value - lower >= 0.5 ? lower + 1 : lower;
}

const auto kDecay = [] {
    std::array<int, kMaxTicks + 2> table{};
    for (int age = 0; age < static_cast<int>(table.size()); age++) {
        table[age] = static_cast<int>(std::ceil(std::max(0.0, 500 / std::sqrt(age / 4.0 + 5) - 60)));
    }
    return table;
}();

struct Moves {
    int16_t cell[6];
    int count = 0;

    void push(int value) { cell[count++] = static_cast<int16_t>(value); }
    int operator[](int index) const { return cell[index]; }
};

template <typename Score>
void keep_best(Moves& moves, Score score, bool maximize) {
    using Value = decltype(score(0));
    Value values[6];
    Value best{};
    for (int i = 0; i < moves.count; i++) {
        values[i] = score(moves[i]);
        if (i == 0 || (maximize ? values[i] > best : values[i] < best)) best = values[i];
    }
    int kept = 0;
    for (int i = 0; i < moves.count; i++) {
        if (values[i] == best) moves.cell[kept++] = moves.cell[i];
    }
    moves.count = kept;
}

template <typename Score>
void keep_max(Moves& moves, Score score) {
    keep_best(moves, score, true);
}

template <typename Score>
void keep_min(Moves& moves, Score score) {
    keep_best(moves, score, false);
}

void flood(const uint8_t* flags, int16_t* dist, int16_t* queue, int tail) {
    for (int head = 0; head < tail; head++) {
        int cell = queue[head];
        int16_t next = dist[cell] + 1;
        for (int16_t neighbor : kNeighbors[cell]) {
            if (neighbor < 0 || dist[neighbor] != kUnreachable) continue;
            if (flags[neighbor] & (kImpassable | kLava)) continue;
            dist[neighbor] = next;
            queue[tail++] = neighbor;
        }
    }
}

// Steps from a dangerous cell to the nearest safe, passable cell. Paths may cross
// lava but not impassable terrain. Searching outward from the queried cell stops at
// the first safe cell, instead of flooding the whole frame from every safe cell.
int nearest_safe(const uint8_t* flags, const bool* danger, int cell) {
    if (flags[cell] & kImpassable) return kUnreachable;
    uint64_t seen[(kCells + 63) / 64] = {};
    seen[cell >> 6] |= uint64_t{1} << (cell & 63);
    int16_t queue[kCells];
    queue[0] = static_cast<int16_t>(cell);
    int head = 0;
    int tail = 1;
    for (int distance = 1; head < tail; distance++) {
        for (int layer_end = tail; head < layer_end; head++) {
            for (int neighbor : kNeighbors[queue[head]]) {
                if (neighbor < 0 || (flags[neighbor] & kImpassable)) continue;
                uint64_t bit = uint64_t{1} << (neighbor & 63);
                if (seen[neighbor >> 6] & bit) continue;
                if (!danger[neighbor]) return distance;
                seen[neighbor >> 6] |= bit;
                queue[tail++] = static_cast<int16_t>(neighbor);
            }
        }
    }
    return kUnreachable;
}

}  // namespace

struct Game::TickContext {
    struct Field {
        int source;
        int16_t dist[kCells];
    };

    int turn;
    bool shift_turn;
    int8_t hazard[kCells];  // Turns until a tile is deadly: 0 for lava, kNoHazard if safe.
    bool danger[kCells];

    int threat_count = 0;
    int8_t threat_row[kMaxThreats];
    int8_t threat_column[kMaxThreats];

    int field_count = 0;
    Field fields[kMaxFields];
    bool corpse_field_ready = false;
    bool has_corpses = false;
    int16_t corpse_field[kCells];

    int pack_count = 0;
    int8_t pack_size[kDinoCap];
    int8_t pack_members[kDinoCap][kDinoCap];
    int8_t pack_of[kDinoCap];
    int16_t pack_centroid[kDinoCap];
    bool has_assignment[kDinoCap];
    int8_t assigned_row[kDinoCap];
    int8_t assigned_column[kDinoCap];

    bool in_next_frame(int cell) const { return !shift_turn || row_of(cell) >= 1; }

    // Tiles a dinosaur may step to: inside the next frame and out of danger when possible.
    Moves safe_moves(int cell) const {
        Moves all;
        for (int neighbor : kNeighbors[cell]) {
            if (neighbor >= 0) all.push(neighbor);
        }
        Moves inside;
        for (int i = 0; i < all.count; i++) {
            if (in_next_frame(all[i])) inside.push(all[i]);
        }
        const Moves& base = inside.count > 0 ? inside : all;
        Moves safe;
        for (int i = 0; i < base.count; i++) {
            if (!danger[base[i]]) safe.push(base[i]);
        }
        return safe.count > 0 ? safe : base;
    }

    // Stationary dinosaurs flee when a hazard is coming or they are nearly boxed in.
    bool must_flee(const uint8_t* flags, int cell) const {
        if (hazard[cell] != kNoHazard) return true;
        int free_neighbors = 0;
        for (int neighbor : kNeighbors[cell]) {
            if (neighbor >= 0 && !(flags[neighbor] & kImpassable) && !danger[neighbor]) free_neighbors++;
        }
        return free_neighbors < 2;
    }

    // Keeps the latest-impact options, then the ones nearest to safety if all are doomed.
    void prefer_survival(Moves& moves, const uint8_t* flags) {
        keep_max(moves, [&](int c) { return static_cast<int>(hazard[c]); });
        if (hazard[moves[0]] != kNoHazard) {
            keep_min(moves, [&](int c) { return safety_distance(flags, c); });
        }
    }

    int flee(Game& game, Moves options, int from) {
        if (in_next_frame(from) && hazard[from] == kNoHazard) options.push(from);
        prefer_survival(options, game.flags_);
        keep_max(options, [&](int c) { return threat_distance(c); });
        return options[game.rng_.next_int(options.count)];
    }

    const int16_t* distances_from(const uint8_t* flags, int source) {
        for (int i = 0; i < field_count; i++) {
            if (fields[i].source == source) return fields[i].dist;
        }
        Field& field = fields[field_count++];
        field.source = source;
        std::fill_n(field.dist, kCells, static_cast<int16_t>(kUnreachable));
        field.dist[source] = 0;
        int16_t queue[kCells];
        queue[0] = static_cast<int16_t>(source);
        flood(flags, field.dist, queue, 1);
        return field.dist;
    }

    int safety_distance(const uint8_t* flags, int cell) {
        return danger[cell] ? nearest_safe(flags, danger, cell) : 0;
    }

    // Squared, because only the order matters. The engine compares Math.hypot values,
    // and those keep exact ties such as hypot(9, 2) and hypot(7, 6).
    int threat_distance(int cell) const {
        int best = std::numeric_limits<int>::max();
        int row = row_of(cell);
        int column = column_of(cell);
        for (int i = 0; i < threat_count; i++) {
            int dx = column - threat_column[i];
            int dy = row - threat_row[i];
            best = std::min(best, dx * dx + dy * dy);
        }
        return best;
    }
};

Game::Game(const Terrain& terrain, std::string_view seed) : terrain_(&terrain), rng_(seed) {
    std::memset(corpses_, 0, sizeof(corpses_));
    for (int row = 0; row < kHeight; row++) {
        terrain.row(row, kWidth, elevation_ + row * kWidth, flags_ + row * kWidth);
    }
    spawn_wave(0);
}

Game Game::from_observation(const Observation& observation, uint32_t rng_seed) {
    Game game(rng_seed);
    game.origin_y_ = static_cast<int16_t>(observation.origin_y);
    game.waves_enabled_ = false;
    std::memcpy(game.elevation_, observation.elevation, kCells);
    std::memcpy(game.flags_, observation.flags, kCells);
    for (int cell = 0; cell < kCells; cell++) game.corpses_[cell] = observation.corpses[cell] != 0;

    game.dino_count_ = static_cast<int8_t>(observation.dino_count);
    for (int i = 0; i < observation.dino_count; i++) {
        int row = observation.dino_y[i] - observation.origin_y;
        game.dinos_[i] = {observation.dino_id[i], static_cast<int16_t>(observation.tick - 1 - observation.dino_age[i]),
                          static_cast<int16_t>(cell_at(row, observation.dino_x[i])),
                          static_cast<Species>(observation.dino_species[i]), true};
    }
    game.meteor_count_ = static_cast<int8_t>(observation.meteor_count);
    for (int i = 0; i < observation.meteor_count; i++) {
        game.meteors_[i] = {static_cast<int16_t>(observation.meteor_x[i]), static_cast<int16_t>(observation.meteor_y[i]),
                            int16_t{-1}, static_cast<int8_t>(observation.meteor_turns[i])};
    }
    game.volcano_count_ = static_cast<int8_t>(observation.volcano_count);
    for (int i = 0; i < observation.volcano_count; i++) {
        game.volcanoes_[i] = {static_cast<int16_t>(observation.volcano_x[i]), static_cast<int16_t>(observation.volcano_y[i])};
    }
    return game;
}

void Game::enable_waves(int tick, int highest_id) {
    waves_enabled_ = true;
    wave_cursor_ = static_cast<int16_t>((highest_id + kDinosPerWave - 1) / kDinosPerWave);
    next_dino_id_ = highest_id + 1;
    map_clear_last_tick_ = dino_count_ == 0;
    int newest = 0;
    for (int i = 0; i < dino_count_; i++) newest = std::max<int>(newest, dinos_[i].spawn_turn);
    last_wave_turn_ = static_cast<int16_t>(dino_count_ > 0 ? newest : tick - 1);
}

void Game::hide(bool rows, bool rng, bool waves, uint32_t rng_seed) {
    if (rows) terrain_ = nullptr;
    if (rng) rng_ = Arc4(rng_seed);
    if (waves) waves_enabled_ = false;
}

Observation Game::observe(int tick) const {
    Observation observation{};
    observation.tick = tick;
    observation.origin_y = origin_y_;
    observation.dino_count = dino_count_;
    for (int i = 0; i < dino_count_; i++) {
        observation.dino_id[i] = dinos_[i].id;
        observation.dino_species[i] = static_cast<int32_t>(dinos_[i].species);
        observation.dino_x[i] = column_of(dinos_[i].cell);
        observation.dino_y[i] = row_of(dinos_[i].cell) + origin_y_;
        observation.dino_age[i] = tick - 1 - dinos_[i].spawn_turn;
    }
    observation.meteor_count = meteor_count_;
    for (int i = 0; i < meteor_count_; i++) {
        observation.meteor_x[i] = meteors_[i].x;
        observation.meteor_y[i] = meteors_[i].y;
        observation.meteor_turns[i] = meteors_[i].turns;
    }
    observation.volcano_count = volcano_count_;
    for (int i = 0; i < volcano_count_; i++) {
        observation.volcano_x[i] = volcanoes_[i].x;
        observation.volcano_y[i] = volcanoes_[i].y;
    }
    std::memcpy(observation.elevation, elevation_, kCells);
    std::memcpy(observation.flags, flags_, kCells);
    for (int cell = 0; cell < kCells; cell++) observation.corpses[cell] = corpses_[cell];
    return observation;
}

bool Game::step(int turn, Action action) {
    bool accepted = apply(turn, action);
    update(turn);
    return accepted;
}

bool Game::apply(int turn, Action action) {
    int row = action.y - origin_y_;
    switch (action.type) {
        case Action::Type::None:
            return true;
        case Action::Type::LaunchMeteor: {
            if (!in_frame(row, action.x) || meteor_count_ >= kMaxMeteors) return false;
            meteors_[meteor_count_++] = {static_cast<int16_t>(action.x), static_cast<int16_t>(action.y),
                                         static_cast<int16_t>(turn), kMeteorDelay};
            return true;
        }
        case Action::Type::TriggerVolcano: {
            if (!in_frame(row, action.x) || !(flags_[cell_at(row, action.x)] & kMountain)) return false;
            if (volcano_count_ >= kMaxVolcanoes) return false;
            for (int i = 0; i < volcano_count_; i++) {
                if (volcanoes_[i].x == action.x && volcanoes_[i].y == action.y) return false;
            }
            volcanoes_[volcano_count_++] = {static_cast<int16_t>(action.x), static_cast<int16_t>(action.y)};
            return true;
        }
    }
    return false;
}

void Game::update(int turn) {
    kills_ = 0;
    kill_base_ = 0;
    maybe_spawn_wave(turn);
    update_meteors(turn);
    resolve_meteor_impacts(turn);
    propagate_lava(turn);
    move_dinosaurs(turn);
    frame_shift(turn);
    settle_tick_score();
    compact();
    map_clear_last_tick_ = dino_count_ == 0;
}

void Game::compact() {
    int kept = 0;
    for (int i = 0; i < dino_count_; i++) {
        if (dinos_[i].alive) dinos_[kept++] = dinos_[i];
    }
    dino_count_ = static_cast<int8_t>(kept);
}

void Game::maybe_spawn_wave(int turn) {
    if (!waves_enabled_) return;  // Planning copies never imagine a wave.
    if (map_clear_last_tick_) {
        map_clear_last_tick_ = false;
        spawn_wave(turn);
        return;
    }
    if (turn > 0 && turn >= last_wave_turn_ + kWaveCadence) spawn_wave(turn);
}

int Game::spawn_bag(int16_t* bag) const {
    bool occupied[kCells] = {};
    for (int i = 0; i < dino_count_; i++) occupied[dinos_[i].cell] = true;

    // Reachable from the bottom row through passable, lava-free tiles.
    bool reachable[kCells] = {};
    int16_t queue[kCells];
    int tail = 0;
    constexpr uint8_t kBlocked = kImpassable | kLava;
    for (int cell = cell_at(kHeight - 1, 0); cell < kCells; cell++) {
        if (!(flags_[cell] & kBlocked)) {
            reachable[cell] = true;
            queue[tail++] = static_cast<int16_t>(cell);
        }
    }
    for (int head = 0; head < tail; head++) {
        for (int neighbor : kNeighbors[queue[head]]) {
            if (neighbor < 0 || reachable[neighbor] || (flags_[neighbor] & kBlocked)) continue;
            reachable[neighbor] = true;
            queue[tail++] = static_cast<int16_t>(neighbor);
        }
    }

    int size = 0;
    for (int cell = 0; cell < kCells; cell++) {
        if (!(flags_[cell] & kBlocked) && !occupied[cell] && reachable[cell]) bag[size++] = static_cast<int16_t>(cell);
    }
    if (size > 0) return size;
    for (int cell = 0; cell < kCells; cell++) {
        if (!(flags_[cell] & kBlocked) && !occupied[cell]) bag[size++] = static_cast<int16_t>(cell);
    }
    return size;
}

void Game::spawn_wave(int turn) {
    const int* wave = kWaveMatrix[wave_cursor_ % 10];
    wave_cursor_++;
    last_wave_turn_ = static_cast<int16_t>(turn);

    int quota = std::min(kDinosPerWave, kDinoCap - dino_count_);
    if (quota <= 0) return;

    int16_t bag[kCells];
    int bag_size = spawn_bag(bag);
    for (int species = 0; species < 4; species++) {
        for (int i = 0; i < wave[species] && quota > 0; i++, quota--) {
            if (bag_size == 0) return;
            int pick = rng_.next_int(bag_size);
            int cell = bag[pick];
            bag[pick] = bag[--bag_size];
            dinos_[dino_count_++] = {next_dino_id_++, static_cast<int16_t>(turn), static_cast<int16_t>(cell),
                                     static_cast<Species>(species), true};
        }
    }
}

void Game::update_meteors(int turn) {
    for (int i = 0; i < meteor_count_; i++) {
        if (meteors_[i].launched_on != turn) meteors_[i].turns--;
    }
}

void Game::resolve_meteor_impacts(int turn) {
    int kept = 0;
    Meteor landing[kMaxMeteors];
    int landing_count = 0;
    for (int i = 0; i < meteor_count_; i++) {
        if (meteors_[i].turns <= 0) landing[landing_count++] = meteors_[i];
        else meteors_[kept++] = meteors_[i];
    }
    meteor_count_ = static_cast<int8_t>(kept);

    constexpr int kRadiusSquared = kMeteorRadius * kMeteorRadius;
    for (int i = 0; i < landing_count; i++) {
        int center_row = landing[i].y - origin_y_;
        int center_column = landing[i].x;
        for (int d = 0; d < dino_count_; d++) {
            Dino& dino = dinos_[d];
            if (!dino.alive || (spawn_shield_ && dino.spawn_turn == turn)) continue;
            int row = row_of(dino.cell) - center_row;
            int column = column_of(dino.cell) - center_column;
            if (row * row + column * column <= kRadiusSquared) eliminate(dino, turn, Cause::Meteor);
        }
        for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
            for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
                if (dx * dx + dy * dy > kRadiusSquared || !in_frame(center_row + dy, center_column + dx)) continue;
                uint8_t& tile = elevation_[cell_at(center_row + dy, center_column + dx)];
                if (tile > 0) tile--;
            }
        }
    }
}

void Game::propagate_lava(int turn) {
    // Lava only spreads while a volcano is active.
    if (volcano_count_ == 0) return;

    bool spreading[kCells] = {};
    auto spread_from = [&](int cell) {
        int lowest = -1;
        for (int neighbor : kNeighbors[cell]) {
            if (neighbor < 0 || (flags_[neighbor] & (kImpassable | kLava))) continue;
            if (lowest < 0 || elevation_[neighbor] < lowest) lowest = elevation_[neighbor];
        }
        if (lowest < 0 || lowest > elevation_[cell]) return;
        for (int neighbor : kNeighbors[cell]) {
            if (neighbor < 0 || (flags_[neighbor] & (kImpassable | kLava))) continue;
            if (elevation_[neighbor] == lowest) spreading[neighbor] = true;
        }
    };

    for (int i = 0; i < volcano_count_; i++) {
        int row = volcanoes_[i].y - origin_y_;
        if (in_frame(row, volcanoes_[i].x)) spread_from(cell_at(row, volcanoes_[i].x));
    }
    for (int cell = 0; cell < kCells; cell++) {
        if (flags_[cell] & kLava) spread_from(cell);
    }
    for (int cell = 0; cell < kCells; cell++) {
        if (spreading[cell]) flags_[cell] |= kLava;
    }
    for (int i = 0; i < dino_count_; i++) {
        Dino& dino = dinos_[i];
        if (dino.alive && (flags_[dino.cell] & kLava)) eliminate(dino, turn, Cause::Lava);
    }
}

void Game::eliminate(Dino& dino, int turn, Cause cause) {
    dino.alive = false;
    if (cause != Cause::OffTail) corpses_[dino.cell] = true;
    if (cause == Cause::Eaten) return;
    total_kills_++;
    kills_++;
    kill_base_ += kDecay[turn - dino.spawn_turn];
}

void Game::settle_tick_score() {
    if (kills_ == 0) return;
    score_ += static_cast<int32_t>(js_round(kill_base_ * (1 + 0.5 * (kills_ - 1))));
}

void Game::frame_shift(int turn) {
    if (turn <= 0 || turn % kShiftInterval != 0) return;
    origin_y_++;

    constexpr int kKept = kCells - kWidth;
    std::memmove(elevation_, elevation_ + kWidth, kKept);
    std::memmove(flags_, flags_ + kWidth, kKept);
    std::memmove(corpses_, corpses_ + kWidth, kKept);
    std::memset(corpses_ + kKept, 0, kWidth);
    if (terrain_ != nullptr) {
        terrain_->row(origin_y_ + kHeight - 1, kWidth, elevation_ + kKept, flags_ + kKept);
    } else {
        // The new bottom row is unknown: assume it resembles the row above it.
        std::memcpy(elevation_ + kKept, elevation_ + kKept - kWidth, kWidth);
        for (int i = 0; i < kWidth; i++) flags_[kKept + i] = flags_[kKept - kWidth + i] & ~kLava;
    }

    for (int i = 0; i < dino_count_; i++) {
        Dino& dino = dinos_[i];
        if (!dino.alive) continue;
        dino.cell = static_cast<int16_t>(dino.cell - kWidth);
        if (dino.cell < 0) eliminate(dino, turn, Cause::OffTail);
    }

    int kept = 0;
    for (int i = 0; i < volcano_count_; i++) {
        if (volcanoes_[i].y >= origin_y_) volcanoes_[kept++] = volcanoes_[i];
    }
    volcano_count_ = static_cast<int8_t>(kept);
}

void Game::move_dinosaurs(int turn) {
    TickContext ctx;
    ctx.turn = turn;
    ctx.shift_turn = turn > 0 && turn % kShiftInterval == 0;

    for (int cell = 0; cell < kCells; cell++) {
        ctx.hazard[cell] = (flags_[cell] & kLava) ? int8_t{0} : static_cast<int8_t>(kNoHazard);
        ctx.danger[cell] = (flags_[cell] & (kLava | kImpassable)) != 0;
    }
    constexpr int kRadiusSquared = kMeteorRadius * kMeteorRadius;
    for (int i = 0; i < meteor_count_; i++) {
        const Meteor& meteor = meteors_[i];
        int center_row = meteor.y - origin_y_;
        for (int dx = -kMeteorRadius; dx <= kMeteorRadius; dx++) {
            for (int dy = -kMeteorRadius; dy <= kMeteorRadius; dy++) {
                if (dx * dx + dy * dy > kRadiusSquared || !in_frame(center_row + dy, meteor.x + dx)) continue;
                int cell = cell_at(center_row + dy, meteor.x + dx);
                ctx.danger[cell] = true;
                if (meteor.turns < ctx.hazard[cell]) ctx.hazard[cell] = meteor.turns;
            }
        }
        ctx.threat_row[ctx.threat_count] = static_cast<int8_t>(center_row);
        ctx.threat_column[ctx.threat_count++] = static_cast<int8_t>(meteor.x);
    }
    for (int cell = 0; cell < kCells; cell++) {
        uint8_t flags = flags_[cell];
        if (flags & kLava) {
            ctx.threat_row[ctx.threat_count] = static_cast<int8_t>(row_of(cell));
            ctx.threat_column[ctx.threat_count++] = static_cast<int8_t>(column_of(cell));
        }
        // Tiles lava could flow into count as danger, including beside dormant mountains.
        if (!(flags & (kLava | kMountain))) continue;
        for (int neighbor : kNeighbors[cell]) {
            if (neighbor < 0 || (flags_[neighbor] & (kImpassable | kLava))) continue;
            if (elevation_[neighbor] <= elevation_[cell]) ctx.danger[neighbor] = true;
        }
    }

    build_packs(ctx);

    int16_t target[kDinoCap];
    for (int slot = 0; slot < dino_count_; slot++) {
        const Dino& dino = dinos_[slot];
        if (!dino.alive) continue;
        int move = decide(ctx, slot);
        target[slot] = (flags_[move] & kImpassable) && move != dino.cell ? dino.cell : static_cast<int16_t>(move);
    }

    resolve_eating(target, turn);

    int16_t result[kDinoCap];
    resolve_conflicts(target, result);
    for (int slot = 0; slot < dino_count_; slot++) {
        Dino& dino = dinos_[slot];
        if (!dino.alive) continue;
        dino.cell = result[slot];
        if (flags_[dino.cell] & kLava) eliminate(dino, turn, Cause::Lava);
    }
}

void Game::resolve_eating(int16_t* target, int turn) {
    int8_t slot_at[kCells];
    std::memset(slot_at, -1, sizeof(slot_at));
    for (int slot = 0; slot < dino_count_; slot++) {
        if (dinos_[slot].alive) slot_at[dinos_[slot].cell] = static_cast<int8_t>(slot);
    }

    int group_count = 0;
    int16_t group_target[kDinoCap];
    int8_t group_winner[kDinoCap];
    for (int slot = 0; slot < dino_count_; slot++) {
        const Dino& dino = dinos_[slot];
        if (!dino.alive || dino.species != Species::Tyrannosaurus) continue;
        int prey = slot_at[target[slot]];
        if (prey < 0 || dinos_[prey].species == Species::Tyrannosaurus) continue;

        int group = 0;
        while (group < group_count && group_target[group] != target[slot]) group++;
        if (group == group_count) {
            group_count++;
            group_target[group] = target[slot];
            group_winner[group] = static_cast<int8_t>(slot);
        } else if (dino.id < dinos_[group_winner[group]].id) {
            group_winner[group] = static_cast<int8_t>(slot);
        }
    }

    for (int group = 0; group < group_count; group++) {
        Dino& prey = dinos_[slot_at[group_target[group]]];
        if (!prey.alive) continue;
        eliminate(prey, turn, Cause::Eaten);
        dinos_[group_winner[group]].cell = group_target[group];
    }
}

// Mirrors the engine's move resolver, including its stack order, which decides
// who gets a contested tile.
void Game::resolve_conflicts(const int16_t* target, int16_t* result) const {
    int8_t slot_at[kCells];
    std::memset(slot_at, -1, sizeof(slot_at));

    bool settled[kDinoCap] = {};
    bool is_mover[kDinoCap] = {};
    int8_t movers[kDinoCap];
    int mover_count = 0;
    for (int slot = 0; slot < dino_count_; slot++) {
        const Dino& dino = dinos_[slot];
        if (!dino.alive) continue;
        slot_at[dino.cell] = static_cast<int8_t>(slot);
        if (target[slot] == dino.cell) {
            settled[slot] = true;
            result[slot] = dino.cell;
        } else {
            is_mover[slot] = true;
            movers[mover_count++] = static_cast<int8_t>(slot);
        }
    }

    int8_t blocker[kDinoCap];
    int8_t waiters[kDinoCap][4];
    int8_t waiter_count[kDinoCap] = {};
    for (int i = 0; i < mover_count; i++) {
        int mover = movers[i];
        int occupant = slot_at[target[mover]];
        blocker[mover] = -1;
        if (occupant >= 0 && occupant != mover && is_mover[occupant]) {
            blocker[mover] = static_cast<int8_t>(occupant);
            waiters[occupant][waiter_count[occupant]++] = static_cast<int8_t>(mover);
        }
    }

    // Rings of movers all go through together.
    bool visited[kDinoCap] = {};
    for (int i = 0; i < mover_count; i++) {
        int start = movers[i];
        if (visited[start]) continue;
        int slow = start;
        int fast = start;
        do {
            slow = blocker[slow];
            fast = blocker[fast];
            if (fast >= 0) fast = blocker[fast];
        } while (slow >= 0 && fast >= 0 && slow != fast);

        if (slow >= 0 && fast >= 0) {
            int member = slow;
            do {
                visited[member] = true;
                settled[member] = true;
                result[member] = target[member];
                member = blocker[member];
            } while (member >= 0 && member != slow);
        }
        for (int walker = start; walker >= 0 && !visited[walker]; walker = blocker[walker]) visited[walker] = true;
    }

    bool taken[kCells] = {};
    int8_t stack[kDinoCap * 5];
    int stack_size = 0;
    for (int i = 0; i < mover_count; i++) {
        int mover = movers[i];
        if (settled[mover]) continue;
        if (blocker[mover] < 0 || settled[blocker[mover]]) stack[stack_size++] = static_cast<int8_t>(mover);
    }
    for (int slot = 0; slot < dino_count_; slot++) {
        if (dinos_[slot].alive && settled[slot]) taken[result[slot]] = true;
    }

    while (stack_size > 0) {
        int mover = stack[--stack_size];
        if (settled[mover]) continue;
        int wanted = target[mover];
        if (!taken[wanted]) {
            result[mover] = static_cast<int16_t>(wanted);
            taken[wanted] = true;
        } else {
            result[mover] = dinos_[mover].cell;
            taken[dinos_[mover].cell] = true;
        }
        settled[mover] = true;
        for (int i = 0; i < waiter_count[mover]; i++) {
            int waiter = waiters[mover][i];
            if (!settled[waiter]) stack[stack_size++] = static_cast<int8_t>(waiter);
        }
    }

    for (int i = 0; i < mover_count; i++) {
        if (!settled[movers[i]]) result[movers[i]] = dinos_[movers[i]].cell;
    }
}

int Game::decide(TickContext& ctx, int slot) {
    switch (dinos_[slot].species) {
        case Species::Stegosaurus:
            return decide_stegosaurus(ctx, slot);
        case Species::Velociraptor:
            return decide_velociraptor(ctx, slot);
        case Species::Triceratops:
            return decide_triceratops(ctx, slot);
        case Species::Tyrannosaurus:
            return decide_tyrannosaurus(ctx, slot);
    }
    return dinos_[slot].cell;
}

namespace {

int stego_target(int turn) {
    int phase = turn % kStegoCycle;
    return cell_at(kStegoTargetRow, phase < kWidth ? phase : kStegoCycle - phase);
}

int manhattan(int a, int b) {
    return std::abs(column_of(a) - column_of(b)) + std::abs(row_of(a) - row_of(b));
}

}  // namespace

int Game::decide_stegosaurus(TickContext& ctx, int slot) {
    int cell = dinos_[slot].cell;
    Moves moves = ctx.safe_moves(cell);
    if (ctx.must_flee(flags_, cell)) return ctx.flee(*this, moves, cell);

    int target = stego_target(ctx.turn);
    if (cell == target) target = stego_target(ctx.turn + 1);
    const int16_t* dist = ctx.distances_from(flags_, target);
    Moves best = moves;
    keep_min(best, [&](int c) { return static_cast<int>(dist[c]); });
    int choice = best[rng_.next_int(best.count)];
    return dist[choice] == kUnreachable ? ctx.flee(*this, moves, cell) : choice;
}

int Game::decide_velociraptor(TickContext& ctx, int slot) {
    int cell = dinos_[slot].cell;
    Moves moves = ctx.safe_moves(cell);
    if (ctx.in_next_frame(cell) && ctx.hazard[cell] == kNoHazard) moves.push(cell);
    ctx.prefer_survival(moves, flags_);

    Moves forward;
    for (int i = 0; i < moves.count; i++) {
        if (row_of(moves[i]) >= kTrailingRows) forward.push(moves[i]);
    }
    if (forward.count > 0) moves = forward;

    if (!ctx.corpse_field_ready) {
        int16_t queue[kCells];
        int tail = 0;
        for (int i = 0; i < kCells; i++) {
            bool source = corpses_[i] && row_of(i) >= kTrailingRows;
            ctx.corpse_field[i] = source ? int16_t{0} : static_cast<int16_t>(kUnreachable);
            if (source) queue[tail++] = static_cast<int16_t>(i);
        }
        ctx.has_corpses = tail > 0;
        flood(flags_, ctx.corpse_field, queue, tail);
        ctx.corpse_field_ready = true;
    }
    if (ctx.has_corpses) {
        keep_min(moves, [&](int c) { return static_cast<int>(ctx.corpse_field[c]); });
    }

    keep_max(moves, [&](int c) {
        int best = kUnreachable;
        for (int other = 0; other < dino_count_; other++) {
            if (other != slot && dinos_[other].alive) best = std::min(best, manhattan(c, dinos_[other].cell));
        }
        return best == kUnreachable ? 0 : best;
    });
    return moves[rng_.next_int(moves.count)];
}

void Game::build_packs(TickContext& ctx) {
    int8_t herd[kDinoCap];
    int herd_size = 0;
    for (int slot = 0; slot < dino_count_; slot++) {
        ctx.has_assignment[slot] = false;
        ctx.pack_of[slot] = -1;
        if (dinos_[slot].alive && dinos_[slot].species == Species::Triceratops) {
            herd[herd_size++] = static_cast<int8_t>(slot);
        }
    }

    // Slots follow id order, so packs come out sorted by lowest id, as the engine sorts them.
    bool seen[kDinoCap] = {};
    for (int i = 0; i < herd_size; i++) {
        int first = herd[i];
        if (seen[first]) continue;
        int pack = ctx.pack_count++;
        int size = 0;
        int sum_row = 0;
        int sum_column = 0;
        int8_t stack[kDinoCap];
        int stack_size = 0;
        stack[stack_size++] = static_cast<int8_t>(first);
        seen[first] = true;
        while (stack_size > 0) {
            int member = stack[--stack_size];
            ctx.pack_members[pack][size++] = static_cast<int8_t>(member);
            ctx.pack_of[member] = static_cast<int8_t>(pack);
            sum_row += row_of(dinos_[member].cell);
            sum_column += column_of(dinos_[member].cell);
            for (int j = 0; j < herd_size; j++) {
                int other = herd[j];
                if (!seen[other] && manhattan(dinos_[member].cell, dinos_[other].cell) == 1) {
                    seen[other] = true;
                    stack[stack_size++] = static_cast<int8_t>(other);
                }
            }
        }
        ctx.pack_size[pack] = static_cast<int8_t>(size);
        ctx.pack_centroid[pack] = static_cast<int16_t>(cell_at(sum_row / size, sum_column / size));
    }

    for (int pack = 0; pack < ctx.pack_count; pack++) {
        // Every pack draws a seed, even one too small to use it.
        uint32_t pack_seed = static_cast<uint32_t>(rng_.next_int(2147483648.0));
        int size = ctx.pack_size[pack];
        if (size < 2) continue;

        // Head for the highest ground within reach, favouring rows ahead.
        int centroid = ctx.pack_centroid[pack];
        auto height = [&](int cell) { return elevation_[cell] + kPackRowWeight * row_of(cell); };
        bool reached[kCells] = {};
        reached[centroid] = true;
        int16_t frontier[kCells];
        int frontier_begin = 0;
        int frontier_end = 1;
        frontier[0] = static_cast<int16_t>(centroid);
        int destination = -1;
        double best_height = -std::numeric_limits<double>::infinity();
        if (row_of(centroid) >= kPackMinRow) {
            destination = centroid;
            best_height = height(centroid);
        }
        for (int depth = 0; depth < kPackReach && frontier_begin < frontier_end; depth++) {
            int depth_end = frontier_end;
            for (int f = frontier_begin; f < depth_end; f++) {
                for (int neighbor : kNeighbors[frontier[f]]) {
                    if (neighbor < 0 || reached[neighbor] || (flags_[neighbor] & kImpassable)) continue;
                    reached[neighbor] = true;
                    frontier[frontier_end++] = static_cast<int16_t>(neighbor);
                    if (row_of(neighbor) < kPackMinRow) continue;
                    double value = height(neighbor);
                    if (value > best_height) {
                        destination = neighbor;
                        best_height = value;
                    }
                }
            }
            frontier_begin = depth_end;
        }
        if (destination < 0) destination = centroid;

        // One shared step, chosen at random among those that get closest.
        int best_distance = kUnreachable;
        int step_options[4];
        int option_count = 0;
        for (int i = 0; i < 4; i++) {
            int distance = std::abs(column_of(centroid) + kSteps[i][0] - column_of(destination)) +
                           std::abs(row_of(centroid) + kSteps[i][1] - row_of(destination));
            if (distance < best_distance) {
                best_distance = distance;
                option_count = 0;
            }
            if (distance == best_distance) step_options[option_count++] = i;
        }
        Arc4 pack_rng(pack_seed);
        int step = step_options[pack_rng.next_int(option_count)];

        for (int m = 0; m < size; m++) {
            int member = ctx.pack_members[pack][m];
            ctx.has_assignment[member] = true;
            ctx.assigned_row[member] = static_cast<int8_t>(row_of(dinos_[member].cell) + kSteps[step][1]);
            ctx.assigned_column[member] = static_cast<int8_t>(column_of(dinos_[member].cell) + kSteps[step][0]);
        }
    }
}

int Game::decide_triceratops(TickContext& ctx, int slot) {
    int cell = dinos_[slot].cell;
    Moves moves = ctx.safe_moves(cell);
    if (ctx.must_flee(flags_, cell)) return ctx.flee(*this, moves, cell);

    if (ctx.has_assignment[slot] && in_frame(ctx.assigned_row[slot], ctx.assigned_column[slot])) {
        int step = cell_at(ctx.assigned_row[slot], ctx.assigned_column[slot]);
        if (!(flags_[step] & (kImpassable | kLava))) return step;
    }

    // Otherwise, join the nearest other pack.
    int nearest = -1;
    int nearest_distance = kUnreachable;
    for (int pack = 0; pack < ctx.pack_count; pack++) {
        if (pack == ctx.pack_of[slot]) continue;
        int distance = manhattan(cell, ctx.pack_centroid[pack]);
        if (distance < nearest_distance) {
            nearest_distance = distance;
            nearest = ctx.pack_centroid[pack];
        }
    }
    if (nearest < 0) return ctx.flee(*this, moves, cell);

    if (ctx.in_next_frame(cell)) moves.push(cell);
    keep_max(moves, [&](int c) { return static_cast<int>(ctx.hazard[c]); });
    const int16_t* dist = ctx.distances_from(flags_, nearest);
    keep_min(moves, [&](int c) { return static_cast<int>(dist[c]); });
    return moves[rng_.next_int(moves.count)];
}

int Game::decide_tyrannosaurus(TickContext& ctx, int slot) {
    int cell = dinos_[slot].cell;
    Moves moves = ctx.safe_moves(cell);
    if (ctx.in_next_frame(cell) && ctx.hazard[cell] == kNoHazard) moves.push(cell);
    ctx.prefer_survival(moves, flags_);

    Moves forward;
    for (int i = 0; i < moves.count; i++) {
        if (row_of(moves[i]) >= kTrailingRows) forward.push(moves[i]);
    }
    if (forward.count > 0) moves = forward;

    // Prey first, then the most open tile.
    keep_max(moves, [&](int c) {
        for (int other = 0; other < dino_count_; other++) {
            if (other != slot && dinos_[other].alive && dinos_[other].cell == c) return 1;
        }
        return 0;
    });
    keep_max(moves, [&](int c) {
        int open = 0;
        for (int neighbor : kNeighbors[c]) {
            if (neighbor >= 0 && !(flags_[neighbor] & kImpassable) && ctx.hazard[neighbor] == kNoHazard) open++;
        }
        return open;
    });

    // Avoid sharing an impact turn with other doomed dinosaurs: they would multiply the score.
    keep_min(moves, [&](int c) {
        int impact = ctx.hazard[c];
        if (impact == kNoHazard) return 0;
        int shared = 0;
        for (int other = 0; other < dino_count_; other++) {
            if (other != slot && dinos_[other].alive && ctx.hazard[dinos_[other].cell] == impact) shared++;
        }
        return shared;
    });
    keep_max(moves, [&](int c) {
        int best = kUnreachable;
        for (int other = 0; other < dino_count_; other++) {
            if (other != slot && dinos_[other].alive) best = std::min(best, manhattan(c, dinos_[other].cell));
        }
        return best == kUnreachable ? 0 : best;
    });
    return moves[rng_.next_int(moves.count)];
}

}  // namespace sim
