// Same BFS as the Numba probe, to compare a prebuilt library against the JIT.
//
// Build for the server from the repository root:
//   .venv/bin/python -m ziglang cc -target x86_64-linux-gnu.2.31 -O2 -shared -fPIC \
//     -o experiments/probe_bot/libprobe.so experiments/probe_bot/probe_native.c

#include <stdint.h>
#include <string.h>

static int bfs(const uint8_t *blocked, int start, int32_t *dist, int32_t *queue) {
    static const int dy[4] = {0, 0, 1, -1};
    static const int dx[4] = {1, -1, 0, 0};
    memset(dist, 0xff, 400 * sizeof(int32_t));
    int head = 0, tail = 1;
    dist[start] = 0;
    queue[0] = start;
    while (head < tail) {
        int v = queue[head++];
        int y = v / 20, x = v % 20;
        for (int i = 0; i < 4; i++) {
            int ny = y + dy[i], nx = x + dx[i];
            if (ny < 0 || ny >= 20 || nx < 0 || nx >= 20) continue;
            int u = ny * 20 + nx;
            if (dist[u] == -1 && !blocked[u]) {
                dist[u] = dist[v] + 1;
                queue[tail++] = u;
            }
        }
    }
    return tail;
}

int64_t bfs_many(const uint8_t *blocked, int start, int count) {
    int32_t dist[400], queue[400];
    int64_t total = 0;
    for (int i = 0; i < count; i++) total += bfs(blocked, start, dist, queue);
    return total;
}
