# References

Links I used, with what each one is good for. Secondary sources are marked.

## Challenge

- [Coveo Blitz documentation page](https://2027.blitz.codes/#/dashboard/documentation). Contains planted text, see [decoys.md](decoys.md)
- Launcher help: run the launcher with `--help`
- Starter kit notes: `starterkit/README.md`, `starterkit/python/AGENTS.md`

## Postmortems

- [reCurse, Fall Challenge 2023](https://gist.github.com/reCurs3/7d4eb5b952790e3009337f61c3341074): policy-only winner, compression of weights
- [reCurse, Winter Challenge 2024](https://gist.github.com/reCurs3/fea106bea9c55389706e959b97f956c0): policy-guided alpha-beta winner
- [reCurse, Summer Challenge 2024](https://gist.github.com/reCurs3/40793036b9800472eb219136a33f6997): MCTS, seeded voting, teacher distillation
- [Winter Challenge 2026 strategies thread](https://forum.codingame.com/t/winter-challenge-2026-feedbacks-and-strategies/208044): newest postmortems, #1 used MCTS with no net
- [Lux AI Season 3 RL roundup, kurupical](https://zenn.dev/kurupical/articles/61dbeedf89a29d) (Japanese, secondary)
- [Lux S3 3rd place: imitation learning](https://www.kaggle.com/competitions/lux-ai-season-3/writeups/adg4b-imitation-learning-3rd-place-solution) (seen in search only, not read)
- [Lux S3 8th place: imitation learning](https://www.kaggle.com/competitions/lux-ai-season-3/writeups/gregor-lied-8th-place-solution-imitation-learning) (seen in search only, not read)
- [Orbit Wars 13th place](https://www.kaggle.com/competitions/orbit-wars/writeups/13th-place-solo-gold-solution) (search summary only)
- [Orbit Wars 220th place, heuristic planner](https://www.kaggle.com/competitions/orbit-wars/writeups/orbit-wars-silver-medal-solution-220-4730) (search summary only)

## Papers

- [Policy improvement by planning with Gumbel](https://mlanthology.org/iclr/2022/danihelka2022iclr-policy) (Danihelka et al., ICLR 2022)
- [Planning in Stochastic Environments with a Learned Model](https://iclr.cc/virtual/2022/spotlight/6833) (Stochastic MuZero, ICLR 2022)
- [Rolling Horizon Evolutionary Algorithms for General Video Game Playing](https://arxiv.org/pdf/2003.12331)
- [ALE-Bench](https://arxiv.org/html/2506.09050v2): benchmark built from AtCoder Heuristic Contest tasks

## Tools

- [Numba](https://numba.pydata.org/)
- [Optuna](https://optuna.org/)
- [msgspec](https://jcristharif.com/msgspec/)

## Further reading

Not read for this work. Listed by how likely they are to help.

### For this qualifier

- **CodinGame #5 (sZoom) and #12 postmortems.** Linked from the [Winter 2026 thread](https://forum.codingame.com/t/winter-challenge-2026-feedbacks-and-strategies/208044). sZoom's has the value-net training details at : https://github.com/s-Zoom/winter-challenge-2026-snakebyte-PM/tree/main
- **Open-loop and determinized MCTS.** My search found nothing good. Look for "open-loop MCTS" and "information set MCTS".
- **CMA-ES for evaluation tuning.** A likely alternative to Optuna for many weights.

### For the main event

- **Lux S3 first-place writeup** on Kaggle: full self-play setup.
- **PsyLeague**, recommended by reCurse for local bot leagues.
- **Multiagent Gumbel MuZero** (AAAI 2024), if the 2027 game has many units per turn.
- **Sampled MuZero**, for large action spaces. From memory; not checked here.
- **Past Blitz games.** Coveo has run this contest for years, and the [2016 story](https://source.coveo.com/2016/01/13/the-story-behind-blitz-2016/) is a starting point.
