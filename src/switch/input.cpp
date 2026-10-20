#include <switch.h>
#include <array>
#include <mutex>
#include "core/input.h"
#include "controller.h"
#include "game_preferences.h"
#include "switch/input.h"

namespace {
std::mutex lock;
std::array<input::Pad, 2> snapshots;
std::array<PadState, 2> devices;
}
namespace input {
void Init() {
    padConfigureInput(2, HidNpadStyleSet_NpadStandard);
    padInitialize(&devices[0], HidNpadIdType_No1, HidNpadIdType_Handheld);
    padInitialize(&devices[1], HidNpadIdType_No2);
}
Pad Poll(int port) {
    std::lock_guard guard(lock);
    return port >= 0 && port < int(snapshots.size()) ? snapshots[size_t(port)] : Pad{};
}
void Rumble(int, uint16_t, uint16_t) {} // not advertised until the device adapter supports it
}
namespace nxinput {
void Update() {
    std::array<input::Pad, 2> next{};
    for (size_t i = 0; i < devices.size(); ++i) {
        auto& device = devices[i];
        padUpdate(&device);
        auto& pad = next[i];
        pad.connected = padIsConnected(&device);
        const auto b = padGetButtons(&device);
        const uint64_t digital[] = {HidNpadButton_Up, HidNpadButton_Down, HidNpadButton_Left, HidNpadButton_Right,
                                    HidNpadButton_Plus, HidNpadButton_Minus, HidNpadButton_StickL, HidNpadButton_StickR};
        for (unsigned bit = 0; bit < 8; ++bit) if (b & digital[bit]) pad.buttons |= uint16_t(1u << bit);
        // Xbox A/B/X/Y follow the PS2 face-button positions. The rear triggers
        // provide PS2 L1/R1 (Xbox white/black); shoulders provide Xbox LT/RT.
        const uint64_t analog[] = {HidNpadButton_B, HidNpadButton_A, HidNpadButton_Y, HidNpadButton_X,
                                  HidNpadButton_ZR, HidNpadButton_ZL, HidNpadButton_L, HidNpadButton_R};
        for (unsigned bit = 0; bit < 8; ++bit) pad.analog[bit] = (b & analog[bit]) ? 255 : 0;
        auto left = padGetStickPos(&device, 0), right = padGetStickPos(&device, 1);
        pad.lx = int16_t(left.x); pad.ly = int16_t(left.y);
        pad.rx = int16_t(right.x); pad.ry = int16_t(right.y);
    }
    std::lock_guard guard(lock);
    snapshots = next;
}
}
namespace shaolin::controller {
bool HasRumble(int) { return false; }
void SetMultiplayer(bool) {} // physical controllers retain stable player ports
}
namespace shaolin::preferences {
void Apply(X86Context&, uint8_t*) {} // preserve the original menu's stored options
}
