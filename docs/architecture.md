# Architecture

## Overview

```text
                    sim/ (C++ port of the engine, about 9 µs per tick)
                                       |
          +----------------------------+----------------------------+
          |                            |                            |
   Live bot (server)            Foresight search             Training
   bots/search_bot              sim_search                   sim_datagen, vec_env
   bot.py -> libsim.so          knows the whole seed         train_value, train_ppo
```

The simulator came first, and everything else shares it. It matches the real engine tick for tick, so anything tuned locally carries over. `tools/check_sim.py` tests that, and all of the top foresight plans replayed identically on the engine.

## Live bot

1. The server sends the game state each tick. `bots/search_bot/bot.py` copies it into a C struct.
2. `sim/capi.cpp` passes it to `sim/agent.cpp`, which searches for up to 95 ms of the 150 ms limit (120 ms on `feature/v12-120ms`).
3. The bot sends back the chosen meteor or volcano.

`application.py` and `game_message.py` are Coveo's and must not change.

### The search

Open-loop MCTS in `sim/agent.cpp`. Open-loop means nodes are action sequences, not states, so each iteration can guess the hidden dice afresh and values average over them.

| Part | Setting |
|---|---|
| Selection | UCT: normalised mean + `0.5 * sqrt(log N / n)`. Not PUCT |
| Widening | `1 + 3 * sqrt(visits)` actions per node, so promising ones are tried first |
| Depth | 5 decisions, then 4 idle ticks so in-flight meteors land |
| Targets | Tiles within radius 2 of a dinosaur, plus up to 4 spawn-pocket tiles |
| Volcanoes | Top 3 mountains, ranked by spawn tiles left after 30 ticks of lava alone |
| Waves | Simulated at guessed tiles |
| Value | `points + 0.3 * 1000 * remaining * net`, at each leaf |
| Move played | Most-visited root action |

Capacity on the server is about 5,000-6,800 simulated ticks per move with the net, and up to 11,000-24,000 without.

## Source map

| Path | Purpose |
|---|---|
| `sim/game.*`, `terrain.*`, `rng.*` | The game engine port |
| `sim/agent.*` | Live search, wave planning, flood outlook |
| `sim/features.*` | Encodes a position for the nets, using only what a live bot sees |
| `sim/value_net.*` | Hand-written value net inference |
| `sim/policy_net.*` | Hand-written policy net inference, checked against PyTorch by `sim_policytest` |
| `sim/capi.cpp` | C interface for the bot |
| `sim/vec_env.cpp` | Batched games for PPO, built as `build/libvec.dll` |

| Program | Source | Purpose |
|---|---|---|
| `sim_live` | `live_main.cpp` | Plays full games with only live information. **The main test tool** |
| `sim_search` | `search_main.cpp` | Foresight beam search. Writes plans and training data |
| `sim_datagen` | `datagen_main.cpp` | Value net data from live-bot games |
| `sim_policygen` | `policygen_main.cpp` | Policy data from MCTS visits. Superseded by `sim_search --plan-data` |
| `sim_replay` | `replay_main.cpp` | Replays an action list, for parity checks |
| `sim_pocket`, `sim_carve` | | Experiments on pockets and lava steering |
| `sim_nettest`, `sim_policytest` | | Check C++ inference against PyTorch |
| `sim_bench` | `bench_main.cpp` | Simulator speed |

| Tool | Purpose |
|---|---|
| `tools/build_sim.py` | Builds everything with Zig. `--linux` adds the server `.so` |
| `tools/run_local.py` | Plays games on the real local engine at server timing |
| `tools/check_sim.py` | Compares the simulator with the engine |
| `tools/make_zip.py` | Packages a bot for upload |
| `tools/compare_probe.py` | Compares a downloaded server game with a local replay |
| `tools/estimate_maps.py` | Runs foresight search over several seeds |
| `tools/verify_plan.py`, `analyze_plan.py` | Check a plan on the engine, and break down its score |
| `tools/train_value.py` | Trains the value net |
| `tools/train_policy.py` | Trains the policy net by imitation |
| `tools/train_ppo.py` | Trains the policy net with PPO |
| `tools/export_policy.py` | Exports a policy checkpoint for C++ inference |

`runs/`, `build/` and `localRunner/` are git-ignored, so data, checkpoints and plans stay on each machine. Training also needs PyTorch with CUDA, which isn't in `requirements-dev.txt`.

## Value net

16,293 parameters: two stride-2 convolutions, then two dense layers. It predicts points over the next 100 ticks, in thousands, and takes about 19 µs per evaluation. `bots/search_bot/value_net.bin` is gen2.

Inputs are 14 map channels of 20 × 20 plus 5 global numbers, 5,605 bytes in all. Samples must look like MCTS leaves, with meteors landed, or the net judges positions it never saw.

```bash
mkdir -p runs/data/mine
```

```bash
build/sim_datagen.exe runs/data/mine 100 --biome deep_crevasse --budget 7000 --threads 11 --net bots/search_bot/value_net.bin --value-weight 0.3
```

```bash
.venv/Scripts/python.exe tools/train_value.py runs/data/mine --out runs/nets/mine
```

```bash
build/sim_nettest.exe runs/nets/mine
```

## Policy net

150,484 parameters, a full-resolution CNN with dilated convolutions. It outputs one logit per tile for meteors and for volcanoes, plus one for doing nothing, with illegal actions masked. Inputs are the value net's plus spawn-area data and clock planes, built by `build_inputs` in `tools/train_ppo.py`. Inference takes about 8-13 ms on our PC, once per tick, so it could fit in a move's budget. `sim_live --policy file --policy-only 1 --plan-waves 1` plays it alone. Leave out `--plan-waves` and the net's wave clock is wrong, which cost about 7,000 points in one test.

### Foresight imitation, then PPO

1. Write foresight games. Each needs about 12 core-minutes at width 50, and runs fine with 1 thread per process. The output folder must exist first:

   ```bash
   mkdir -p runs/data/oracle
   ```

   ```bash
   build/sim_search.exe deep_crevasse fp-0 50 plan.txt --threads 1 --cluster 0 --spread 0 --grid-step 1 --net bots/search_bot/value_net.bin --net-weight 1 --flood 1 --plan-data runs/data/oracle/deep_crevasse-fp-0.bin
   ```

   Repeat over seeds and all three biomes. Each record is a position and the plan's move there. Keep training seeds apart from test seeds such as `est-*`.

2. Train by imitation. This took 2.4 minutes for 222 games, and ends by playing fresh seeds:

   ```bash
   .venv/Scripts/python.exe tools/train_policy.py runs/data/oracle --out runs/policy/mine --epochs 20
   ```

3. Continue with PPO on the real game, about 15,000 samples per second. It evaluates and saves `latest.pt` and `best.pt` at each `--eval-every`:

   ```bash
   .venv/Scripts/python.exe tools/train_ppo.py --out runs/ppo/mine --resume-net runs/policy/mine/policy.pt --threads 11 --hours 6 --entropy 0.003 --eval-every 50
   ```

To resume after a crash, pass `--resume runs/ppo/mine/latest.pt` with a new `--out` folder, so the old log and `best.pt` survive.

## Foresight search

`sim_search` is a beam search that knows the seed. At each tick it expands every kept state with every action, rolls each one 4 idle ticks so meteors land, and ranks by:

```text
score + net_weight * 1000 * remaining * net + flood * flood_outlook
```

Its width says how many states it keeps. In practice one good parent's children fill the beam, so it follows few distinct lines. Options for other data outputs are listed at the top of `sim/search_main.cpp`.
