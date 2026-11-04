// Checks ValueNet against PyTorch's outputs and measures its speed.
//
//   sim_nettest <folder from train_value.py>
//
// check.bin holds int32 count, then for each sample the feature bytes and a float32 output.

#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "features.hpp"
#include "value_net.hpp"

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: sim_nettest <folder>\n");
        return 2;
    }
    std::string folder = argv[1];

    sim::ValueNet net;
    if (!net.load((folder + "/value_net.bin").c_str())) {
        std::fprintf(stderr, "could not load the network\n");
        return 1;
    }

    std::FILE* file = std::fopen((folder + "/check.bin").c_str(), "rb");
    if (file == nullptr) {
        std::fprintf(stderr, "could not open check.bin\n");
        return 1;
    }
    int32_t count = 0;
    std::fread(&count, sizeof(count), 1, file);
    std::vector<uint8_t> features(count * sim::kPolicyBytes);
    std::vector<float> expected(count);
    for (int i = 0; i < count; i++) {
        std::fread(features.data() + i * sim::kPolicyBytes, 1, sim::kPolicyBytes, file);
        std::fread(&expected[i], sizeof(float), 1, file);
    }
    std::fclose(file);

    float worst = 0;
    for (int i = 0; i < count; i++) {
        worst = std::max(worst, std::fabs(net.evaluate(features.data() + i * sim::kPolicyBytes) - expected[i]));
    }
    std::printf("%d samples, worst difference from PyTorch %.7f\n", count, worst);

    constexpr int kRepeats = 200000;
    volatile float sink = 0;
    auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < kRepeats; i++) sink = net.evaluate(features.data() + (i % count) * sim::kPolicyBytes);
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::printf("%.2f us per evaluation\n", seconds / kRepeats * 1e6);
    (void)sink;
    return worst < 1e-3f ? 0 : 1;
}
