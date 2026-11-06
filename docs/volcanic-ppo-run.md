# Volcanic PPO run

This run continues our best PPO net on `volcanic_plains` only. The high scores mostly come from this biome, so we want to see whether a specialist net beats the general one. It needs no game server or launcher login, so please don't upload anything or run practice games for it.

It takes about 3 hours and uses the GPU and 11 CPU threads. The commands work on Linux and in Git Bash on Windows. On Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe`.

## Setup

Use a fresh clone, so that this build stays apart from your other work:

```bash
git clone --branch feature/volcanic-ppo https://github.com/Delta025/QualifierCoveo2027 coveo-volcanic
cd coveo-volcanic
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements-dev.txt
uv pip install --python .venv torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last command should print `True` and your GPU's name. Then build the simulator library:

```bash
.venv/bin/python tools/build_sim.py --vec-only
```

## Run

```bash
mkdir -p runs/ppo
.venv/bin/python tools/train_ppo.py --out runs/ppo/volcanic1 --resume nets/ppo_oracle1c.pt --biomes 4 --threads 11 --hours 3 --entropy 0.003 --eval-every 50 2>&1 | tee runs/ppo/volcanic1.log
```

`--biomes 4` selects `volcanic_plains`. The trainer saves `latest.pt` and `best.pt` in `runs/ppo/volcanic1/` at each evaluation.

## What to expect

- The first line is `150,484 parameters`.
- An `iter` line appears each minute. Its `score` starts low for a few minutes, because the first finished games are short ones. It should then settle near 44,000.
- Speed was about 14,000 samples/s on our Ryzen 5 5600 and RTX 3080.
- An `eval` line appears every 50 iterations, about every 8 minutes.

To check progress:

```bash
grep -a "^eval" runs/ppo/volcanic1.log | tail
```

The starting net scored this over 512 `volcanic_plains` games, one iteration into a test run:

| Mode | Mean | Best game |
|---|---|---:|
| `greedy` | 45,854 | 72,678 |
| `sampled` | 44,820 | 70,879 |

Each `eval` line uses only 128 games, so its mean can move by about 1,000 by chance. Look at the trend over several lines. The real test comes later, when we play the net as the search prior on paired seeds.

## If it stops

Resume from the last save into a new folder. Reusing the folder would overwrite `best.pt`.

```bash
.venv/bin/python tools/train_ppo.py --out runs/ppo/volcanic1b --resume runs/ppo/volcanic1/latest.pt --biomes 4 --threads 11 --hours 2 --entropy 0.003 --eval-every 50 2>&1 | tee runs/ppo/volcanic1b.log
```

Set `--hours` to the time left. If the GPU crashes or the machine freezes, send us the error, because our RTX 3080 failed several times during long runs.

## When it's done

Send Simon these files. `runs/` is git-ignored, so they won't be in the repository:

- `runs/ppo/volcanic1/best.pt`
- `runs/ppo/volcanic1/latest.pt`
- `runs/ppo/volcanic1.log`

If you had to resume, send the files from the last folder as well, and every log.
