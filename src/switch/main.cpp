#include <switch.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <unistd.h>
#include <iterator>
#include <thread>
#include "app/app.h"
#include "core/input.h"
#include "core/log.h"
#include "gpu/backend.h"
#include "host.h"
#include "kernel/crypto.h"
#include "kernel/kernel.h"
#include "runtime_paths.h"
#include "switch/input.h"
#include "switch/threading.h"
#include "switch/thread_detach.h"
#include "switch/audio_output.h"

extern const char kImageSha1[];
extern const char kSwitchBuildId[];
namespace {
// Match the desktop target's host limit and the guest's 60 Hz vertical blank.
constexpr uint32_t kFrameRate = 60;
std::atomic<uint64_t> paced_frames{}, pacing_ns{};
}
extern "C" {
alignas(16) u8 __nx_exception_stack[0x8000];
u64 __nx_exception_stack_size = sizeof(__nx_exception_stack);
u32 __nx_exception_ignoredebug = 1;
void _start();
void __libnx_exception_handler(ThreadExceptionDump* context) {
    // Avoid the runtime log mutex: the faulting thread may already own it.
    char report[2048];
    int count = std::snprintf(report, sizeof(report),
        "build=%s base=%llX desc=%X ESR=%X FAR=%llX PC=%llX LR=%llX SP=%llX FP=%llX\n",
        kSwitchBuildId, static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(&_start)),
        context->error_desc, context->esr, static_cast<unsigned long long>(context->far.x),
        static_cast<unsigned long long>(context->pc.x), static_cast<unsigned long long>(context->lr.x),
        static_cast<unsigned long long>(context->sp.x), static_cast<unsigned long long>(context->fp.x));
    for (unsigned i = 0; i < 29 && count > 0 && size_t(count) < sizeof(report) - 48; ++i)
        count += std::snprintf(report + count, sizeof(report) - size_t(count), "X%u=%llX\n", i,
                               static_cast<unsigned long long>(context->cpu_gprs[i].x));
    if (count > 0) {
        svcOutputDebugString(report, size_t(count));
        int fd = open("sdmc:/switch/shaolin-recomp/logs/exception.txt", O_CREAT | O_TRUNC | O_WRONLY, 0666);
        if (fd >= 0) { write(fd, report, size_t(count)); close(fd); }
    }
    svcExitProcess();
}
}
namespace shaolin { Options options; }
namespace shaolin::paths {
const std::filesystem::path& Root() { static const std::filesystem::path root("sdmc:/switch/shaolin-recomp"); return root; }
std::filesystem::path Cache() { return Root() / "cache"; }
}
namespace app {
const GameInfo kGame{
    .title = L"Shaolin Monks Recomp", .short_title = L"Shaolin Monks Recomp", .name = L"ShaolinMonksRecomp",
    .log = "runtime", .settings = "settings.toml", .splash = L"", .missing = L"Missing original game files",
    .images = {{"default.xbe", "ShaolinMonksRecomp"}}, .options = {},
    .load = nullptr, .save = nullptr, .init = nullptr, .loaded = nullptr,
    .window = nullptr, .message = nullptr, .ui = nullptr, .pad = nullptr, .rumble = nullptr
};
Settings& Get() {
    static Settings settings{.width = 1280, .height = 720, .render_percent = 67, .fps_cap = kFrameRate, .vsync = true};
    return settings;
}
Mode Desktop() { return {1280, 720}; }
Mode Output() { return Desktop(); }
float RenderScale(uint32_t) { return 1.0f; } // original render resolution, scaled only for presentation
bool Widescreen() { return true; }
std::filesystem::path ExeDir() { return shaolin::paths::Root(); }
int Instance() { return 0; }
void* Window() { return nwindowGetDefault(); }
bool HandingOver() { return false; }
void FinishHandover() {}
void SplashFirstFrame() { LOG_FIRST(1, "Switch: original game submitted its first present"); }
void OpenGameWindow(bool) {}
std::recursive_mutex& UiLock() { static std::recursive_mutex lock; return lock; }
void Pace() {
    using clock = std::chrono::steady_clock;
    static auto next = clock::now();
    const auto now = clock::now();
    const auto period = std::chrono::nanoseconds(1000000000ull / Get().fps_cap);
    next += period;
    if (next < now) next = now;
    std::this_thread::sleep_until(next);
    pacing_ns.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(clock::now() - now).count(),
                        std::memory_order_relaxed);
    paced_frames.fetch_add(1, std::memory_order_relaxed);
}
}
namespace gpu {
std::unique_ptr<Backend> CreateVulkanBackend();
std::unique_ptr<Backend> CreateBackend() { return CreateVulkanBackend(); }
}

int RunGame() {
    using namespace std::filesystem;
    const auto root = shaolin::paths::Root();
    std::error_code error;
    create_directories(root / "logs", error);
    create_directories(root / "cache", error);
    create_directories(root / "hdd", error);
    if (FILE* diagnostics = std::freopen((root / "logs/native-stderr.log").string().c_str(), "w", stderr))
        std::setvbuf(diagnostics, nullptr, _IOLBF, 0);
    logx::Init((root / "logs").string(), "runtime");
    LOGR("Shaolin Monks Recomp: experimental ARM64/libnx target build=%s XBE=%s", kSwitchBuildId, kImageSha1);
    LOGR("Switch: frame limit=%u; PS2 button positions, shoulder/trigger functions exchanged", kFrameRate);
    nxthreads::Configure(0x3B);
    if (exists(root / "thread-selftest.flag")) {
        static std::atomic<unsigned> completed{};
        const auto before = nxthreads::DetachedSnapshot();
        for (unsigned batch = 0; batch < 8; ++batch) {
            for (unsigned i = 0; i < 8; ++i) std::thread([] { ++completed; }).detach();
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
            while (nxthreads::DetachedSnapshot().cleaned < before.cleaned + (batch + 1) * 8 &&
                   std::chrono::steady_clock::now() < deadline) svcSleepThread(1000000);
        }
        const auto after = nxthreads::DetachedSnapshot();
        LOGR("thread selftest: callbacks=%u accepted=%llu cleaned=%llu", completed.load(),
             static_cast<unsigned long long>(after.accepted - before.accepted),
             static_cast<unsigned long long>(after.cleaned - before.cleaned));
        if (completed != 64 || after.cleaned - before.cleaned != 64) { logx::Flush(); return 1; }
    }
    if (appletGetAppletType() != AppletType_Application && appletGetAppletType() != AppletType_SystemApplication) {
        LOGE("Full application memory is required; use title takeover");
        logx::Flush();
        return 1;
    }
    const auto game = root / "game";
    std::ifstream file(game / "default.xbe", std::ios::binary);
    if (!file) { LOGE("Missing %s", (game / "default.xbe").c_str()); logx::Flush(); return 1; }
    std::vector<uint8_t> image((std::istreambuf_iterator<char>(file)), {});
    alignas(8) std::array<uint8_t, 96> context{};
    std::array<uint8_t, 20> sha{};
    crypto::ShaInit(context.data()); crypto::ShaUpdate(context.data(), image.data(), image.size()); crypto::ShaFinal(context.data(), sha.data());
    char hex[41]{};
    for (unsigned i = 0; i < 20; ++i) std::snprintf(hex + i * 2, 3, "%02x", unsigned(sha[i]));
    if (std::string(hex) != kImageSha1) { LOGE("XBE does not match this translation"); logx::Flush(); return 1; }
    input::Init();
    nxinput::Update();
    setenv("XBE_PERF", "1", 1);
    if (exists(root / "descriptor-sets.flag")) setenv("XBE_DESCRIPTOR_SETS", "1", 1);
    if (std::ifstream flags{root / "nvk-debug.txt"}; flags) {
        std::string value;
        std::getline(flags, value);
        setenv("NVK_DEBUG", value.c_str(), 1);
        LOGR("diagnostic NVK_DEBUG=%s", value.c_str());
    }
    if (exists(root / "capture.flag")) {
        setenv("XBE_CAPTURE_DIR", (root / "logs/captures").string().c_str(), 1);
        LOGR("diagnostic GPU capture enabled; frame timings include readback stalls");
    }
    if (!kernel::Boot(std::move(image), "default.xbe", game.string(), (root / "hdd").string())) {
        logx::Flush();
        return 1;
    }
    auto previous_report = std::chrono::steady_clock::now();
    auto next_report = previous_report + std::chrono::seconds(5);
    uint64_t previous_frames = 0, previous_pacing_ns = 0;
    while (appletMainLoop()) {
        nxinput::Update();
        if (std::chrono::steady_clock::now() >= next_report) {
            const auto audio = nxaudio::Snapshot();
            LOGR("audio: started=%d frames=%llu max_mix_us=%llu max_callback_gap_us=%llu queue_budget_us=60000",
                 int(audio.started), static_cast<unsigned long long>(audio.frames), static_cast<unsigned long long>(audio.max_mix_us),
                 static_cast<unsigned long long>(audio.max_callback_gap_us));
            const auto now = std::chrono::steady_clock::now();
            const auto frames = paced_frames.load(std::memory_order_relaxed);
            const auto wait = pacing_ns.load(std::memory_order_relaxed);
            const auto count = frames - previous_frames;
            const double seconds = std::chrono::duration<double>(now - previous_report).count();
            LOGR("pacing: cap=%u guest_swaps=%.1f fps throttle=%.2f ms/swap",
                 app::Get().fps_cap, count / seconds, count ? double(wait - previous_pacing_ns) / count / 1e6 : 0.0);
            previous_frames = frames;
            previous_pacing_ns = wait;
            previous_report = now;
            next_report = now + std::chrono::seconds(5);
        }
        svcSleepThread(4000000);
    }
    kernel::Shutdown(0);
}

int main() {
    try { return RunGame(); }
    catch (const std::exception& error) { LOGE("native exception: %s", error.what()); }
    catch (...) { LOGE("unknown native exception on main thread"); }
    logx::Flush();
    return 1;
}
