# Game rules, from the engine

These rules come from the JS engine embedded in the launcher, not from the web page, which is unreliable: see [decoys.md](decoys.md). Extracted engine code is Coveo's, so it stays out of this repository. Simon confirmed that inspecting the runner is fine for this qualifier, but the rules for the main event need checking again.

Verified on replays: determinism, footprint size, meteor delay, score formula, multi-kill formula. The C++ simulator in `sim/` implements all of this and matches the engine tick for tick, so play has tested the rest indirectly. Server limits are in [server.md](server.md).

## Constants

Read these from `constants` at runtime; the docs say they may be retuned.

| Constant | Value |
|---|---|
| Ticks | 1000 |
| Map frame | 20 x 20, x fixed at 0-19 |
| Frame shift | +1 y every 5 ticks (200 rows in a game) |
| Meteors in flight | 3 |
| Meteor delay / radius | 4 / 2 |
| Volcanoes active | 1 |
| Wave cadence / size | 100 ticks / 10 dinosaurs |
| Alive cap | 20 |

## Order inside one tick

1. Apply the bot's action (one only).
2. Spawn a wave if due.
3. Count down meteors (not those launched this tick).
4. Resolve impacts.
5. Spread lava, kill dinosaurs on lava.
6. Dinosaurs decide on one shared snapshot, then eating, then conflict resolution, then lava check.
7. Frame shift if `tick % 5 == 0`; dinosaurs outside are killed and scored.
8. Settle the tick's score.
9. If nothing is alive, the next wave spawns next tick.

**Consequence of steps 2 and 4:** a meteor can kill a dinosaur on the tick it spawns, at age 0, with no chance to flee. This was seen in a replay (164 points on tick 1000).

## Scoring

```
base(age)  = ceil(max(0, 500 / sqrt(age / 4 + 5) - 60))
tick score = round(sum(base) * (1 + 0.5 * (kills - 1)))
```

| Age | 0 | 5 | 10 | 20 | 30 | 50 | 100 | 200 | 258+ |
|---|---|---|---|---|---|---|---|---|---|
| Base | 164 | 140 | 123 | 99 | 82 | 60 | 32 | 8 | 0 |

- All deaths in a tick share the multiplier: meteor, lava, and frame exit.
- A dinosaur worth 0 still counts toward `kills`.
- Eaten dinosaurs score nothing and do not count.
- No hidden multipliers exist. All 56 checkable kill events in one replay matched this formula exactly, including 45 kills of dinosaurs still for 3+ ticks.

### Points for one tick

| Kills | Age 0 | Age 10 | Age 30 | Age 50 |
|---|---|---|---|---|
| 1 | 164 | 123 | 82 | 60 |
| 3 | 984 | 738 | 492 | 360 |
| 5 | 2,460 | 1,845 | 1,230 | 900 |
| 10 | 9,020 | 6,765 | 4,510 | 3,300 |
| 20 | 34,440 | 25,830 | 17,220 | 12,600 |

Grouping beats speed. Five dinosaurs killed together at age 30 score 1,230; the same five killed separately at age 5 score 700.

## Meteors

- Launched on tick `t`, lands inside tick `t + 4`, before dinosaurs move. They get 4 moves.
- Footprint is a Euclidean disc: `dx² + dy² <= r²`, 13 tiles, clipped at the frame.
- Throughput is 3 launches per 5 ticks, about **600 per game**. The starter bot's 399 rejected launches in a game match this.
- Delay is fixed, so **at most one meteor lands per tick**.
- Each impact lowers footprint elevation by 1, floor 0. Terrain flags do not change.

## Lava and volcanoes

- A volcano needs a mountain tile. It cannot be cancelled and lasts until its row scrolls out (up to about 100 ticks).
- Each tick, the volcano and every lava tile look at their non-impassable, non-lava neighbours. If the lowest is not above the tile's own elevation, lava enters **all** neighbours at that lowest elevation.
- Lava is permanent until it scrolls away, but **it only spreads while a volcano is active**. Once the volcano scrolls out, all lava stops.
- Lava can cover a lot of the map: 205 of 400 tiles in one random game.
- Meteors reshape where lava goes on later ticks.

## Dinosaur behaviour

All movement is 4-neighbour. Dinosaurs avoid a "danger set": meteor footprints, lava, impassable tiles, and tiles next to lava or mountains that lava could flow into. If nothing is safe, they use the full candidate list.

| Species | Normal behaviour | Flee behaviour |
|---|---|---|
| Stegosaurus | Walks by BFS to one shared target: `y = origin + 13`, `x` zigzags on a 38-tick cycle. **All Stegosaurus converge on the same point.** Never idles. | Flees when in a footprint or under 2 safe neighbours |
| Velociraptor | Can idle. BFS to the nearest corpse, ties broken by distance from other dinosaurs. Keeps `y >= origin + 3`. | Picks the latest-impact tile, then the shortest path to safety |
| Triceratops | Adjacent ones form a pack and move by one shared step toward high ground (`elevation + 0.2 * row`, within 6 steps). Singles walk to the nearest other pack. | As Stegosaurus |
| Tyrannosaurus | Can idle. Prefers tiles with prey, then most safe neighbours, then avoids shared impact timing, then distance. Keeps `y >= origin + 3`. | As Velociraptor |

- Random tie-breaks use the shared game RNG.
- Dinosaurs escape greedily, with perfect knowledge of footprints and timing. They die when the path to safety is longer than the time left, or is blocked by terrain, lava or other dinosaurs.
- Two dinosaurs cannot end on one tile; swaps and cycles are allowed.

## Waves

A wave spawns when the 100-tick timer ends or on the tick after the map is cleared. Either one resets the timer. The composition cycles (Stego, Raptor, Trike, T-rex):

| Wave | S | V | T | R | | Wave | S | V | T | R |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 10 | 0 | 0 | 0 | | 6 | 0 | 2 | 7 | 1 |
| 2 | 8 | 2 | 0 | 0 | | 7 | 6 | 4 | 0 | 0 |
| 3 | 6 | 4 | 0 | 0 | | 8 | 0 | 2 | 7 | 1 |
| 4 | 0 | 2 | 8 | 0 | | 9 | 2 | 2 | 3 | 3 |
| 5 | 6 | 4 | 0 | 0 | | 10 | 3 | 0 | 4 | 3 |

- If the cap trims a wave, the easier species are kept first.
- Spawn tiles are uniform over tiles that are passable, lava-free, unoccupied, **and connected to the bottom row** through passable, lava-free tiles. Walls of lava can therefore shrink the spawn zone.

## Maps and randomness

- Three biomes: `deep_crevasse`, `mountain_range`, `volcanic_plains`. Elevation runs 0-20, from layered seeded noise.
- The same seed and the same actions replay the same game (verified). The RNG is shared, so changing one action changes every later spawn and tie-break.
- The bot never sees the seed, so future rows and spawns are unknown during a game.

## Refreshing the engine excerpt

After a new launcher release, find the `// @bun` script near the end of the exe, pretty-print it with Prettier, and diff the game section against your previous copy, kept outside this repository.
