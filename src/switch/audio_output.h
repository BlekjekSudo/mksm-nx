#pragma once
#include <cstdint>
namespace nxaudio {
struct Stats { uint64_t frames, max_mix_us, max_callback_gap_us; bool started; };
Stats Snapshot();
}
