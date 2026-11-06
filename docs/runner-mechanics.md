# Local runner mechanics

## Scope and reproducibility

This report describes the local Linux runner only; it does not inspect the
launcher’s authentication, upload, or remote-service behavior. Work was done in
the detached research worktree at the committed baseline
`cb569ee95078a286273d9b71d7b980a407e280f4`.

The inspected runner is
`localRunner/blitz-challenge-linux`, SHA-256
`dcffe40c78bbd106450b132407699732df6b9475416c645ce29904720a3b2801`.
It is a 64-bit x86-64 ELF containing a Bun 1.4.4 standalone bundle. ELF
`.bun` section offset and size reported by `readelf -SW` are `0x6804000` and
`0x53338`; the extractor independently finds offset `109068288` and size
`340792`. The embedded graph contains one readable, minified JavaScript module
(340,635 bytes). No source map or module bytecode was extracted. Names,
comments, and original TypeScript types may have been removed during bundling.

Reproduce the extraction into a new output directory:

```text
python3 tools/extract_runner.py localRunner/blitz-challenge-linux --out localRunner/research/extracted-fresh
```

The report’s source offsets are byte offsets in
`localRunner/research/extracted/raw/blitz-challenge-linux`. The bundle is
minified onto one long line, so offsets are more useful than line numbers.
Validate the extractor with:

```text
python3 -m unittest discover -s tools -p 'test_extract_runner.py' -v
node --check localRunner/research/extracted/raw/blitz-challenge-linux
readelf -SW localRunner/blitz-challenge-linux
```

The extracted module SHA-256 was
`cd626161ad63d7dbef1546561f0a1fb83ba881d7a4004449fd02142eb8adc36b` on two
independent extractions. Extraction tests passed, Node accepted the module’s
syntax, and the ELF section boundaries matched `readelf`.

## Recovered behavior

| Mechanic | Runner evidence | Finding |
|---|---|---|
| Default constants | `static DEFAULT_CONSTANTS`, offset 317445 | 1,000 ticks; 3 meteors; 1 volcano; meteor delay 4; radius 2; map shift every 5; wave cadence 100; 10 dinosaurs per wave; active cap 20. The configured constants can override these defaults. |
| Actions | Action enum `tl`, offset 296002; `applyTurnActions`, offset 309127 | The engine dispatches `LAUNCH_METEOR` and `TRIGGER_VOLCANO`; only the first submitted action is applied. |
| Tick order | `update`, offset 309553 | Spawn check, meteor countdown, impacts, lava spread, dinosaur movement/eating, frame shift, then score settlement. |
| Meteor footprint | `impactedTiles`, offset 316629; `resolveMeteorImpacts`, offset 309961 | Integer-grid offsets inside a Euclidean circle (`dx*dx + dy*dy <= radius*radius`), clipped to the current frame. An impact lowers each footprint tile’s elevation by one, floored at zero. |
| Meteor count-down | `updateMeteors`, offset 310594; `launchMeteor`, offset 313043 | The launch turn is excluded from countdown. The projectile impacts after the configured delay. |
| Volcano and lava | `triggerVolcano`, offset 313398; `propagateLava`, offset 313889 | A volcano must target an in-frame mountain and respect the active-volcano cap and uniqueness check. Each turn, lava spreads from active volcano tiles and existing lava to all eligible neighboring tiles at the lowest neighboring elevation, if that elevation is not higher than the source. |
| Grid and movement | `Xp` neighbor list, offset 303452; meteor footprint above | The engine uses ordinary integer `x,y` positions, four cardinal neighbors, and square-grid distances—not axial hex coordinates. |
| Species | `Le` and `Bn`, offset 302965 | The strategy dispatch and spawn roster enumerate exactly Stegosaurus, Velociraptor, Triceratops, and Tyrannosaurus. |
| Waves | `WAVE_MATRIX`, offset 306892; `maybeSpawnWave`, offset 307806; `spawnWaveFromMatrix`, offset 307257 | A wave is checked before the rest of the tick, on the cadence or after a cleared map. Spawning is limited by the active cap. |
| Score | `decayCurveScore`, offset 312270; `eliminate`, offset 312350; `settleTickScore`, offset 312712 | Each scored kill gets `ceil(max(0, 500 / sqrt(age / 4 + 5) - 60))`, with age computed as elimination turn minus spawn turn. The same-turn score is multiplied by `1 + 0.5 * (kills - 1)` and rounded. Predation (`Eaten`) is excluded from kill scoring. |
| Rolling frame | `frameShift`, offset 314970 | The origin advances one row every configured interval; off-frame dinosaurs are eliminated and scored. |

## Challenge-page claims checked

Several detailed page claims do not match the bundled game logic:

- **Dormancy bonus:** the scoring path computes the decay score from age, sums
  the kill base scores, and applies the multi-kill multiplier. It has no
  per-dinosaur stillness counter or dormancy multiplier.
- **Seismic cascades / aftershocks and double-bounce:** every impact obtains its
  footprint from the configured radius and deforms those tiles; there is no
  recent-impact history or recursive/secondary impact in the impact path.
- **Pterodactyl:** the species roster and strategy dispatch contain the four
  species above, and the action enum contains only the two disaster actions.
  No Pterodactyl response path is present in the recovered game logic.
- **Hex coordinates:** movement neighbors and meteor footprints use ordinary
  `x,y` integer offsets, with four cardinal neighbors and Euclidean-circle
  meteor bounds.
- **Bonus waves on clear:** the map-clear path causes a wave check on the next
  update; spawning is still constrained by the active-dinosaur cap.

These conclusions are based on the relevant executable game methods and
dispatch data, not merely on missing keyword strings. They describe this local
runner version and should be rechecked if the competition engine changes.

## Simulator comparison

The C++ simulator already uses matching default constants, the same decay
formula and multi-kill rounding, the same circular meteor footprint, and the
same four species. After rebuilding its native tools in this worktree,
`python3 tools/check_sim.py --games 3` compared all 1,001 recorded/replayed
states in each of three biomes and reported exact matches:

| Biome | Seed | Score | Kills | Result |
|---|---|---:|---:|---|
| deep_crevasse | `check-0` | 6,278 | 90 | Identical |
| mountain_range | `check-1` | 4,418 | 82 | Identical |
| volcanic_plains | `check-2` | 6,712 | 89 | Identical |

This is useful parity evidence for these seeded runs, not proof that every
possible state or hidden tie-break has been exhaustively checked. No simulator
or bot behavior was changed as part of this investigation.
