#include <switch.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include "audio/audio.h"
#include "core/log.h"
#include "switch/threading.h"
#include "switch/audio_output.h"

namespace {
std::atomic<uint64_t> mixed_frames{}, max_mix_us{}, max_callback_gap_us{};
std::atomic<bool> device_started{};
void Maximum(std::atomic<uint64_t>& counter, uint64_t value) {
    auto before = counter.load(std::memory_order_relaxed);
    while (before < value && !counter.compare_exchange_weak(before, value, std::memory_order_relaxed)) {}
}
}
namespace nxaudio {
Stats Snapshot() { return {mixed_frames.load(), max_mix_us.load(), max_callback_gap_us.load(), device_started.load()}; }
}

namespace audio {
namespace {
constexpr uint32_t kFrames = kRate / 100;
constexpr size_t kBytes = (kFrames * 2 * sizeof(int16_t) + 0xFFF) & ~size_t(0xFFF);
constexpr size_t kBuffers = 6;
struct Slot { AudioOutBuffer buffer{}; void* memory = nullptr; };

void Run(std::function<void(float*, uint32_t)> render) {
    nxthreads::Configure(0x2D);
    std::array<Slot, kBuffers> slots;
    std::vector<float> floats(kFrames * 2);
    using clock = std::chrono::steady_clock;
    auto previous_callback = clock::now();
    Result rc = audoutInitialize();
    if (R_FAILED(rc)) { LOGE("audio: audoutInitialize failed: %08X", rc); return; }
    if (audoutGetSampleRate() != kRate || audoutGetChannelCount() != 2 ||
        audoutGetPcmFormat() != PcmFormat_Int16) {
        LOGE("audio: unsupported device format");
        audoutExit();
        return;
    }
    auto fill = [&](Slot& slot) {
        const auto before = clock::now();
        if (device_started.load(std::memory_order_relaxed))
            Maximum(max_callback_gap_us, std::chrono::duration_cast<std::chrono::microseconds>(before - previous_callback).count());
        previous_callback = before;
        render(floats.data(), kFrames);
        Maximum(max_mix_us, std::chrono::duration_cast<std::chrono::microseconds>(clock::now() - before).count());
        mixed_frames.fetch_add(kFrames, std::memory_order_relaxed);
        auto* pcm = static_cast<int16_t*>(slot.memory);
        for (size_t i = 0; i < floats.size(); ++i) {
            float f = std::isfinite(floats[i]) ? floats[i] : 0.0f;
            pcm[i] = int16_t(std::clamp(f * 32768.0f, -32768.0f, 32767.0f));
        }
        return audoutAppendAudioOutBuffer(&slot.buffer);
    };
    for (auto& slot : slots) {
        slot.memory = aligned_alloc(0x1000, kBytes);
        if (!slot.memory) { rc = MAKERESULT(Module_Libnx, LibnxError_OutOfMemory); break; }
        slot.buffer.buffer = slot.memory;
        slot.buffer.buffer_size = kBytes;
        slot.buffer.data_size = kFrames * 2 * sizeof(int16_t);
        rc = fill(slot);
        if (R_FAILED(rc)) break;
    }
    if (R_SUCCEEDED(rc)) rc = audoutStartAudioOut();
    device_started.store(R_SUCCEEDED(rc));
    while (R_SUCCEEDED(rc)) {
        AudioOutBuffer* released = nullptr;
        uint32_t count = 0;
        rc = audoutWaitPlayFinish(&released, &count, UINT64_MAX);
        while (R_SUCCEEDED(rc) && released) {
            auto it = std::find_if(slots.begin(), slots.end(), [&](const Slot& slot) { return &slot.buffer == released; });
            if (it == slots.end()) { rc = MAKERESULT(Module_Libnx, LibnxError_BadInput); break; }
            rc = fill(*it);
            if (R_SUCCEEDED(rc)) rc = audoutGetReleasedAudioOutBuffer(&released, &count);
        }
    }
    LOGE("audio: output worker stopped: %08X", rc);
    device_started.store(false);
    audoutStopAudioOut();
    audoutExit();
    for (auto& slot : slots) free(slot.memory);
}
}

void StartOutput(std::function<void(float*, uint32_t)> render) {
    // A device-driven worker owns the queue. Guest video decoding and frame
    // submission never drain/requeue the audio buffers or replay old PCM.
    static std::once_flag once;
    std::call_once(once, [&] { std::thread(Run, std::move(render)).detach(); });
}
}
