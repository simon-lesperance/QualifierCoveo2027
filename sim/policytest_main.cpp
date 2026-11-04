// Checks the hand-written policy net against values PyTorch wrote, and times it.
//
//   sim_policytest <folder with policy_net.bin and policy_check.bin>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "policy_net.hpp"

int main(int argc, char** argv) {
    std::string folder = argv[1];
    sim::PolicyNet net;
    if (!net.load((folder + "/policy_net.bin").c_str())) {
        std::fprintf(stderr, "could not load the net\n");
        return 1;
    }
    std::FILE* file = std::fopen((folder + "/policy_check.bin").c_str(), "rb");
    int32_t count;
    std::fread(&count, sizeof(count), 1, file);
    std::vector<uint8_t> features(sim::kFeatureBytes);
    std::vector<float> expected(sim::PolicyNet::kActions);
    std::vector<float> logits(sim::PolicyNet::kActions);
    double worst = 0;
    double total_ms = 0;
    for (int i = 0; i < count; i++) {
        int32_t tick;
        std::fread(&tick, sizeof(tick), 1, file);
        std::fread(features.data(), 1, features.size(), file);
        std::fread(expected.data(), sizeof(float), expected.size(), file);
        auto start = std::chrono::steady_clock::now();
        net.evaluate(features.data(), tick, logits.data());
        total_ms += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        for (size_t a = 0; a < logits.size(); a++) {
            if (expected[a] > -1000) worst = std::max(worst, std::fabs(static_cast<double>(expected[a] - logits[a])));
        }
    }
    std::printf("%d samples, worst difference %.5f, %.2f ms per evaluation\n", count, worst, total_ms / count);
    return 0;
}
