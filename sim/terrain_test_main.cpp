// How well can upcoming rows be inferred from rows already seen? Feeds true terrain to
// TerrainInference row by row, as a live bot would see it, and scores its predictions of
// the next rows against the truth and against copying the last row.
//
//   sim_terrain_test <seeds> [--first n]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "terrain.hpp"
#include "terrain_infer.hpp"

int main(int argc, char** argv) {
    int seeds = argc > 1 ? std::atoi(argv[1]) : 20;
    int first = 0;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (std::string(argv[i]) == "--first") first = std::atoi(argv[i + 1]);
    }
    constexpr int kAhead = 8;
    long tiles[kAhead] = {}, right[kAhead] = {}, copy_right[kAhead] = {}, rows_exact[kAhead] = {}, rows_total[kAhead] = {};
    long flag_right[kAhead] = {}, copy_flag_right[kAhead] = {};
    int failures = 0;
    long phase_tiles[5] = {}, phase_right[5] = {};
    double slowest = 0, total_ms = 0, initial_ms = 0, predict_ms = 0, predict_slowest = 0;
    long predicts = 0;
    long observes = 0, hypotheses = 0, steps = 0;
    for (int s = first; s < first + seeds; s++) {
        std::string seed = "live-" + std::to_string(s);
        sim::Terrain terrain(seed, "volcanic_plains");
        std::vector<std::vector<uint8_t>> height(225, std::vector<uint8_t>(20)), flags(225, std::vector<uint8_t>(20));
        for (int y = 0; y < 225; y++) terrain.row(y, 20, height[y].data(), flags[y].data());
        sim::TerrainInference inference("volcanic_plains");
        int failed_at = -1;
        for (int y = 0; y <= 218; y++) {
            auto start = std::chrono::steady_clock::now();
            inference.observe(y, height[y].data());
            inference.work(0);
            double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
            if (y < 20) initial_ms += ms; else slowest = std::max(slowest, ms);
            if (y >= 20 && ms > 30) std::printf("slow observe %.1f ms at y %d, hypotheses %d\n", ms, y, inference.hypothesis_count());
            total_ms += ms;
            observes++;
            if (inference.failed() && failed_at < 0) failed_at = y;
            if (y < 19) continue;
            hypotheses += inference.hypothesis_count();
            if (s == first && y % 10 == 0) std::printf("y %d assigned %d hypotheses %d matching truth %d\n", y, inference.assigned(), inference.hypothesis_count(), inference.matching(terrain));
            steps++;
            for (int a = 0; a < kAhead; a++) {
                int target = y + 1 + a;
                if (target >= 220) continue;
                uint8_t h[20], f[20];
                auto p0 = std::chrono::steady_clock::now();
                bool ok = inference.predict(target, h, f);
                double pm = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - p0).count();
                predict_ms += pm; predicts++; predict_slowest = std::max(predict_slowest, pm);
                int exact = 1;
                for (int x = 0; x < 20; x++) {
                    tiles[a]++;
                    bool good = ok && h[x] == height[target][x];
                    right[a] += good;
                    flag_right[a] += ok ? f[x] == flags[target][x] : flags[y][x] == flags[target][x];
                    copy_right[a] += height[y][x] == height[target][x];
                    copy_flag_right[a] += flags[y][x] == flags[target][x];
                    exact &= good;
                }
                rows_exact[a] += exact;
                if (a == 0) { phase_tiles[y % 5] += 20; for (int x = 0; x < 20; x++) phase_right[y % 5] += ok && h[x] == height[target][x]; }
                rows_total[a]++;
            }
        }
        if (failed_at >= 0) {
            failures++;
            std::printf("%s failed at row %d\n", seed.c_str(), failed_at);
        }
    }
    std::printf("initial 20 rows %.1f ms per game; predict mean %.2f ms, slowest %.1f ms\n", initial_ms / seeds, predict_ms / predicts, predict_slowest);
    std::printf("%d seeds, %d failed; mean hypotheses %.1f; observe mean %.3f ms, slowest %.1f ms\n", seeds, failures,
                steps ? double(hypotheses) / steps : 0.0, total_ms / observes, slowest);
    for (int p = 0; p < 5; p++) std::printf("last row %% 5 == %d: next row heights right %.1f%%\n", p, 100.0 * phase_right[p] / phase_tiles[p]);
    for (int a = 0; a < kAhead; a++) {
        std::printf("row +%d: heights right %.1f%% (copy %.1f%%), flags right %.1f%% (copy %.1f%%), whole rows %.1f%%\n", a + 1,
                    100.0 * right[a] / tiles[a], 100.0 * copy_right[a] / tiles[a], 100.0 * flag_right[a] / tiles[a],
                    100.0 * copy_flag_right[a] / tiles[a], 100.0 * rows_exact[a] / rows_total[a]);
    }
}
