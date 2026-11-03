// Replays a list of actions and prints the state after every tick, one JSON object
// per line, for tools/check_sim.py to compare against a recorded game.
//
//   sim_replay <biome> <seed> <actions file>
//
// Each line of the actions file is "<tick> <M|V> <x> <y>".

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <string>

#include "game.hpp"

namespace {

using sim::Action;
using sim::Game;

const char* species_name(sim::Species species) {
    switch (species) {
        case sim::Species::Stegosaurus:
            return "Stegosaurus";
        case sim::Species::Velociraptor:
            return "Velociraptor";
        case sim::Species::Triceratops:
            return "Triceratops";
        case sim::Species::Tyrannosaurus:
            return "Tyrannosaurus";
    }
    return "?";
}

void print_state(const Game& game, int tick, bool accepted) {
    int origin = game.origin_y();
    std::string out = "{\"tick\":" + std::to_string(tick) + ",\"score\":" + std::to_string(game.score()) +
                      ",\"originY\":" + std::to_string(origin) + ",\"kills\":" + std::to_string(game.total_kills()) +
                      ",\"accepted\":" + (accepted ? "true" : "false") + ",\"dinos\":[";
    for (int i = 0; i < game.dino_count(); i++) {
        const sim::Dino& dino = game.dino(i);
        if (i) out += ',';
        out += "[" + std::to_string(dino.id) + ",\"" + species_name(dino.species) + "\"," +
               std::to_string(dino.cell % sim::kWidth) + "," + std::to_string(dino.cell / sim::kWidth + origin) + "," +
               std::to_string(tick - dino.spawn_turn) + "]";
    }
    out += "],\"meteors\":[";
    for (int i = 0; i < game.meteor_count(); i++) {
        const sim::Meteor& meteor = game.meteor(i);
        if (i) out += ',';
        out += "[" + std::to_string(meteor.x) + "," + std::to_string(meteor.y) + "," + std::to_string(meteor.turns) + "]";
    }
    out += "],\"volcanoes\":[";
    for (int i = 0; i < game.volcano_count(); i++) {
        if (i) out += ',';
        out += "[" + std::to_string(game.volcano(i).x) + "," + std::to_string(game.volcano(i).y) + "]";
    }
    out += "],\"corpses\":[";
    bool first = true;
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (!game.corpse(cell)) continue;
        if (!first) out += ',';
        first = false;
        out += "[" + std::to_string(cell % sim::kWidth) + "," + std::to_string(cell / sim::kWidth + origin) + "]";
    }
    out += "],\"elevation\":[";
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (cell) out += ',';
        out += std::to_string(game.elevation(cell));
    }
    out += "],\"flags\":[";
    for (int cell = 0; cell < sim::kCells; cell++) {
        if (cell) out += ',';
        out += std::to_string(game.flags(cell));
    }
    out += "]}\n";
    std::fputs(out.c_str(), stdout);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "usage: sim_replay <biome> <seed> <actions file>\n";
        return 2;
    }

    std::map<int, Action> actions;
    std::ifstream file(argv[3]);
    int tick;
    char kind;
    int x, y;
    while (file >> tick >> kind >> x >> y) {
        Action action;
        action.type = kind == 'V' ? Action::Type::TriggerVolcano : Action::Type::LaunchMeteor;
        action.x = x;
        action.y = y;
        actions[tick] = action;
    }

    sim::Terrain terrain(argv[2], argv[1]);
    Game game(terrain, argv[2]);
    print_state(game, 0, true);
    for (int turn = 1; turn <= sim::kMaxTicks; turn++) {
        auto found = actions.find(turn);
        bool accepted = game.step(turn, found == actions.end() ? Action{} : found->second);
        print_state(game, turn, accepted);
    }
    return 0;
}
