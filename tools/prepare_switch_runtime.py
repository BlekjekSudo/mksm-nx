"""Prepare ARM64/libnx SDK sources without modifying the pinned checkout."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from prepare_recomp_runtime import replace_once


def prepare(sdk: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    headers = out / "include/cpu"
    headers.mkdir(parents=True, exist_ok=True)
    ops = (sdk / "runtime/cpu/x86_ops.h").read_text(encoding="utf-8")
    # GCC 15 AArch64 ICEs when an aligned(1) scalar typedef is passed as a
    # function argument. A packed field keeps unaligned/alias semantics but
    # its value has the ordinary scalar type required by AAPCS64.
    ops = replace_once(ops, '#define X86_MEM8(a)',
                       'template <class T> struct __attribute__((packed, may_alias)) X86Unaligned { T value; };\n\n#define X86_MEM8(a)')
    for name, scalar, suffix in [('16', 'uint16_t', 'u16'), ('32', 'uint32_t', 'u32'),
                                  ('64', 'uint64_t', 'u64'), ('F32', 'float', 'f32'), ('F64', 'double', 'f64')]:
        ops = replace_once(ops, f'#define X86_MEM{name}(a) (*(x86_{suffix}u*)(base + (uint32_t)(a)))',
                           f'#define X86_MEM{name}(a) (((X86Unaligned<{scalar}>*)(base + (uint32_t)(a)))->value)')
    (headers / "x86_ops.h").write_text(ops, encoding="utf-8")
    (headers / "x86_context.h").write_bytes((sdk / "runtime/cpu/x86_context.h").read_bytes())
    dispatch = (sdk / "runtime/cpu/dispatch.cpp").read_text(encoding="utf-8")
    # Guest CPUID is already synthesized; neither branch needs host intrinsics.
    dispatch = replace_once(dispatch, '#ifdef _WIN32\n#include <intrin.h>\n#else\n#include <x86intrin.h>\n#endif', '')
    (out / "dispatch.cpp").write_text(dispatch, encoding="utf-8")

    # Preserve all title adaptations, including SFD dirty tracking and the
    # render worker's command ownership. Replace only host ISA/thread naming.
    threaded = (out / "render_thread.cpp").read_text(encoding="utf-8")
    threaded = replace_once(threaded, '#include <smmintrin.h>', '#include <cstring>')
    start = threaded.index('    size_t i = 0;\n    __m128i diff = _mm_setzero_si128();')
    tail = '    return i == n || !std::memcmp(a + i, b + i, n - i);\n'
    end = threaded.index(tail, start) + len(tail)
    # Keep the function's existing size/boundary conditions; memcmp also reads
    # every trailing byte and cannot overread a short payload.
    threaded = threaded[:start] + '    return std::memcmp(a, b, n) == 0;\n' + threaded[end:]
    threaded = threaded.replace('        pthread_setname_np(pthread_self(), "render");',
                                '        // libnx has no pthread name service.\n')
    threaded = '#include <switch.h>\n' + threaded
    threaded = '#include "switch/threading.h"\n' + threaded
    threaded = replace_once(threaded, '    void Run() {', '    void Run() {\n        nxthreads::Configure(0x3B);')
    (out / "render_thread.cpp").write_text(threaded, encoding="utf-8")

    threads = (sdk / "runtime/kernel/threads.cpp").read_text(encoding="utf-8")
    threads = replace_once(threads, '    pthread_setname_np(pthread_self(), name);',
                           '    (void)name; // libnx has no pthread name service.')
    threads = replace_once(threads, '    pthread_create(&th, &attr, [](void* p) -> void* {',
                           '    pthread_create(&th, &attr, [](void* p) -> void* {\n        nxthreads::Configure(0x3B);')
    (out / "threads.cpp").write_text('#include "switch/threading.h"\n' + threads, encoding="utf-8")

    memory = (sdk / "runtime/kernel/memory.cpp").read_text(encoding="utf-8")
    memory = memory.replace('#include <sys/mman.h>', '#include "switch/guest_memory.h"')
    start = memory.index('    const uint64_t size = 1ull << 32;')
    end = memory.index('    if (!g_base)', start)
    memory = memory[:start] + '    g_base = nxmem::Reserve();\n' + memory[end:]
    start = memory.index('#ifdef _WIN32', memory.index('bool Commit('))
    end = memory.index('#endif', start) + len('#endif')
    memory = memory[:start] + '    if (!nxmem::Commit(first, size_t(end - first))) return false;' + memory[end:]
    start = memory.index('#ifdef _WIN32', memory.index('void Decommit('))
    end = memory.index('#endif', start) + len('#endif')
    memory = memory[:start] + '    nxmem::Decommit(first, size_t(end - first));' + memory[end:]
    memory = '#include <new>\n' + memory
    # The image loader and pool allocators otherwise continue with an
    # unmapped address when Horizon rejects a backing allocation.
    memory = replace_once(memory, '    g_ranges_ready = true;\n', '')
    memory = replace_once(memory, '    Commit(kKernelDataBase, kKernelDataSize);',
                          '    if (!Commit(kKernelDataBase, kKernelDataSize)) throw std::bad_alloc();\n'
                          '    g_ranges_ready = true;')
    memory = replace_once(memory, '    g_user.Remove(b, e - b);',
                          '    if (!Commit(b, e - b)) throw std::bad_alloc();\n'
                          '    g_user.Remove(b, e - b);')
    memory = replace_once(memory, '    Commit(b, e - b);\n', '')
    memory = replace_once(memory, '        Commit(first, end - first);',
                          '        if (!Commit(first, end - first)) return 0xC0000017; // STATUS_NO_MEMORY')
    for allocator, function in [('g_contig', 'AllocContiguous'), ('g_system', 'AllocSystem')]:
        start = memory.index(f'uint32_t {function}(')
        end = memory.index('\nvoid Free', start)
        section = replace_once(memory[start:end], '    Commit(a, size);',
                               f'    if (!Commit(a, size)) {{ {allocator}.Free(a); return 0; }}')
        memory = memory[:start] + section + memory[end:]
    memory = replace_once(memory, '    Commit(a + 0x10000, size);',
                          '    if (!Commit(a + 0x10000, size)) { g_stacks.Free(a); return 0; }')
    (out / "memory.cpp").write_text(memory, encoding="utf-8")

    root = Path(__file__).resolve().parents[1]
    backend = (root / "src/linux/vk/vk_backend.cpp").read_text(encoding="utf-8")
    backend = replace_once(backend, '#include <SDL3/SDL.h>\n#include <SDL3/SDL_vulkan.h>', '#include "switch/vulkan_platform.h"')
    backend = backend.replace('    Uint32 count = 0;', '    uint32_t count = 0;')
    backend = backend.replace('SDL_Vulkan_GetInstanceExtensions', 'nxvk::Extensions')
    backend = backend.replace('SDL_GetError()', 'nxvk::Error()')
    backend = backend.replace('SDL_Vulkan_CreateSurface(static_cast<SDL_Window*>(app::Window()),', 'nxvk::CreateSurface(app::Window(),')
    backend = backend.replace('SDL_GetWindowSizeInPixels(static_cast<SDL_Window*>(app::Window()),', 'nxvk::WindowSize(app::Window(),')
    backend = replace_once(backend, 'bool VulkanBackend::InitUi() {', 'bool VulkanBackend::InitUi() {\n    return false; // The Switch uses the original game menus; no desktop overlay.')
    backend = backend.replace('LOGI("perf:', 'LOGR("perf:')
    backend = replace_once(backend, '        g_perf.swap += PerfMs(perf_swap);',
                           '        if (r == VK_SUCCESS && !presents_) LOGR("Switch: first Vulkan game present submitted");\n        g_perf.swap += PerfMs(perf_swap);')
    backend = backend.replace('LOGI("vulkan:', 'LOGR("vulkan:')
    # Native NVK supports push descriptors. Keep a checked descriptor-set path
    # for comparison; push descriptors avoid a pool allocation for every draw.
    backend = replace_once(backend, '    bool dynamic_ext_ = false;',
                           '    PFN_vkCmdPushDescriptorSetKHR push_descriptors_ = nullptr;\n    bool dynamic_ext_ = false;')
    backend = replace_once(backend, '    std::vector<const char*> ext = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};', '''    uint32_t extension_count = 0;
    vk.vkEnumerateDeviceExtensionProperties(gpu_, nullptr, &extension_count, nullptr);
    std::vector<VkExtensionProperties> available(extension_count);
    vk.vkEnumerateDeviceExtensionProperties(gpu_, nullptr, &extension_count, available.data());
    const bool want_push = HasExtension(available, VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME) &&
                           !std::getenv("XBE_DESCRIPTOR_SETS");
    std::vector<const char*> ext = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    if (want_push) ext.push_back(VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME);''')
    backend = replace_once(backend, '    if (!vkapi::LoadDevice(dev_)) return false;', '''    if (!vkapi::LoadDevice(dev_)) return false;
    if (want_push) push_descriptors_ = reinterpret_cast<PFN_vkCmdPushDescriptorSetKHR>(
        vk.vkGetDeviceProcAddr(dev_, "vkCmdPushDescriptorSetKHR"));
    LOGR("vulkan: descriptor binding=%s", push_descriptors_ ? "push" : "sets");''')
    backend = replace_once(backend, '    li.bindingCount = 10;',
                           '    li.flags = push_descriptors_ ? VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR : 0;\n    li.bindingCount = 10;')
    backend = replace_once(backend, '    VkDescriptorSet set = AllocSet();\n    if (!set) return;',
                           '    VkDescriptorSet set = push_descriptors_ ? VK_NULL_HANDLE : AllocSet();\n    if (!push_descriptors_ && !set) return;')
    backend = replace_once(backend, '    vk.vkUpdateDescriptorSets(dev_, 10, w, 0, nullptr);\n    vk.vkCmdBindDescriptorSets(Cmd(), VK_PIPELINE_BIND_POINT_GRAPHICS, layout_, 0, 1, &set, 0, nullptr);', '''    if (push_descriptors_) {
        push_descriptors_(Cmd(), VK_PIPELINE_BIND_POINT_GRAPHICS, layout_, 0, 10, w);
    } else {
        vk.vkUpdateDescriptorSets(dev_, 10, w, 0, nullptr);
        vk.vkCmdBindDescriptorSets(Cmd(), VK_PIPELINE_BIND_POINT_GRAPHICS, layout_, 0, 1, &set, 0, nullptr);
    }''')
    capture = r'''
    // Explicit diagnostic mode only: capture the actual guest front target.
    // ReadBack waits for this GPU copy; never enable it for FPS measurements.
    if (const char* directory = std::getenv("XBE_CAPTURE_DIR"); directory && t) {
        static auto capture_start = PerfClock::now();
        static unsigned capture_index = 0;
        constexpr int seconds[] = {5, 15, 30, 60, 120};
        const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(PerfClock::now() - capture_start).count();
        if (capture_index < std::size(seconds) && elapsed >= seconds[capture_index]) {
            CopySource source = PrepareCopySource(*t, {});
            std::vector<uint8_t> pixels;
            if (source.res && ReadBack(*source.res, t->gw, t->gh, pixels)) {
                std::error_code error;
                std::filesystem::create_directories(directory, error);
                auto path = std::filesystem::path(directory) / ("front-" + std::to_string(capture_index) + ".ppm");
                if (FILE* file = std::fopen(path.string().c_str(), "wb")) {
                    std::fprintf(file, "P6\n%u %u\n255\n", t->gw, t->gh);
                    std::vector<uint8_t> rgb(size_t(t->gw) * t->gh * 3);
                    for (size_t i = 0; i < rgb.size() / 3; ++i) {
                        rgb[i * 3] = pixels[i * 4 + 2];
                        rgb[i * 3 + 1] = pixels[i * 4 + 1];
                        rgb[i * 3 + 2] = pixels[i * 4];
                    }
                    const size_t written = std::fwrite(rgb.data(), 1, rgb.size(), file);
                    std::fclose(file);
                    LOGR("capture: front=%08X present=%llu %ux%u bytes=%zu file=%s", t->data,
                         static_cast<unsigned long long>(presents_), t->gw, t->gh, written, path.string().c_str());
                }
            }
            ++capture_index;
        }
    }
'''
    backend = '#include <cstdlib>\n' + backend
    backend = replace_once(backend, '    if (ramp_dirty_) UploadGammaRamp();', capture + '\n    if (ramp_dirty_) UploadGammaRamp();')
    (out / "vk_backend.cpp").write_text(backend, encoding="utf-8")
    api = (root / "src/linux/vk/vk_api.cpp").read_text(encoding="utf-8")
    api = replace_once(api, '#include <SDL3/SDL.h>\n#include <SDL3/SDL_vulkan.h>', '#include "switch/vulkan_platform.h"')
    start = api.index('    // the window was made with SDL_WINDOW_VULKAN')
    end = api.index('    if (!vk.vkGetInstanceProcAddr)', start)
    api = api[:start] + '    vk.vkGetInstanceProcAddr = nxvk::ProcAddress();\n' + api[end:]
    (out / "vk_api.cpp").write_text(api, encoding="utf-8")

    boot = (sdk / "runtime/kernel/boot.cpp").read_text(encoding="utf-8")
    boot = boot.replace('LOGI("boot:', 'LOGR("boot:')
    boot = replace_once(boot, '    mem::ReserveImage(img.base, img.image_size);',
                        '    LOGR("boot: committing image %08X+%X", img.base, img.image_size);\n    mem::ReserveImage(img.base, img.image_size);\n    LOGR("boot: image committed");')
    boot = replace_once(boot, '    InitKernelData();', '    LOGR("boot: sections copied, initializing kernel data tick=%08X launch=%08X hardware=%08X", DataAddress(156), DataAddress(164), DataAddress(322));\n    if (!DataAddress(156) || !DataAddress(164) || !DataAddress(322)) { LOGE("boot: missing kernel data registration"); return false; }\n    InitKernelData();\n    LOGR("boot: kernel data initialized");')
    boot = replace_once(boot, '    StartThread(t);', '    LOGR("boot: main guest thread created, starting original entry %08X", img.entry);\n    StartThread(t);\n    LOGR("boot: original entry started");')
    boot = '#include "switch/posix_io.h"\n' + boot
    boot = replace_once(boot, '    if (!block || !f.read(static_cast<char*>(HostPtr(block + 0x40)), head[4])) return;',
                        '    std::vector<char> pixels(head[4]);\n'
                        '    if (!block || !f.read(pixels.data(), head[4])) return;\n'
                        '    std::memcpy(HostPtr(block + 0x40), pixels.data(), head[4]);')
    boot = replace_once(boot, '        std::fwrite(pixels, 1, bytes, f);',
                        '        nxposix::Write(f, pixels, bytes);')
    boot = replace_once(boot, '        lf.read(static_cast<char*>(HostPtr(page)), 0x1000);',
                        '        std::vector<char> launch(0x1000);\n'
                        '        lf.read(launch.data(), launch.size());\n'
                        '        std::memcpy(HostPtr(page), launch.data(), size_t(lf.gcount()));')
    start = boot.index('    char self[4096] = {};')
    end = boot.index('#endif', start)
    boot = boot[:start] + '    LOGE("relaunch of %s is not supported by this Switch target", exe);\n    Shutdown(1);\n' + boot[end:]
    (out / "boot.cpp").write_text(boot, encoding="utf-8")
    kernel_data = (sdk / "runtime/kernel/xboxkrnl.cpp").read_text(encoding="utf-8")
    kernel_data = replace_once(kernel_data, '    std::thread([] {', '    LOGR("boot: kernel variables written, starting clock worker");\n    std::thread([] {\n        LOGR("boot: clock worker entered");')
    kernel_data = replace_once(kernel_data, '    }).detach();', '    }).detach();\n    LOGR("boot: clock worker detached");')
    kernel_data = '#include "switch/posix_io.h"\n' + kernel_data
    kernel_data = replace_once(kernel_data, '                std::fwrite(HostPtr(page), 1, 0x1000, f);',
                              '                nxposix::Write(f, HostPtr(page), 0x1000);')
    (out / "xboxkrnl.cpp").write_text(kernel_data, encoding="utf-8")
    disc = (sdk / "runtime/kernel/disc.cpp").read_text(encoding="utf-8")
    disc = replace_once(disc, '    ssize_t got = pread(', '    ssize_t got = nxposix::Pread(')
    (out / "disc.cpp").write_text('#include "switch/posix_io.h"\n' + disc, encoding="utf-8")

    files = (sdk / "runtime/kernel/files.cpp").read_text(encoding="utf-8")
    files = '#include <switch.h>\n#include <cerrno>\n#include <sys/stat.h>\n#include <unistd.h>\n#include "switch/posix_io.h"\n' + files
    # Horizon does not permit a second open while an existing file handle has
    # write access. libnx path stat opens another read handle internally. Keep
    # extracted disc assets read-only and query open files through their fd.
    files = replace_once(files, '    bool dir = false;', '    bool read_only = false;\n    bool dir = false;')
    files = replace_once(files, '    bool disc = false;', '    bool read_only = false;\n    bool disc = false;')
    files = replace_once(files, 'return {{}, true, disc->Find(parts)};',
                         'return {{}, true, true, disc->Find(parts)};')
    files = replace_once(files, '    return {MatchCase(best->second, parts).string()};',
                         '    return {MatchCase(best->second, parts).string(), best->first == kCdRom};')
    files = replace_once(files, '        fs::create_directories(v, ec);',
                         '        if (k != kCdRom) fs::create_directories(v, ec);')
    files = replace_once(files, '    bool exists = fs::exists(host, ec);', '''    bool exists = fs::exists(host, ec);
    if (t.read_only && (!exists || (disposition != 1 && disposition != 3) || (options & 0x1000))) {
        const uint32_t status = exists ? kAccessDenied : kObjectNameNotFound;
        IoStatus(iosb, status, 0);
        return status;
    }''')
    files = files.replace('\n    f->host = host;', '\n    f->read_only = t.read_only;\n    f->host = host;')
    files = replace_once(files, '\n        f->host = host;', '\n        f->read_only = t.read_only;\n        f->host = host;')
    files = replace_once(files, '    FILE* fp = std::fopen(host.c_str(), mode);',
                         '    if (t.read_only) mode = "rb";\n    FILE* fp = std::fopen(host.c_str(), mode);')
    files = replace_once(files, '    auto f = std::make_shared<File>();\n    f->read_only = t.read_only;\n    f->host = host;\n    f->xbox = Normalize(name);\n    f->dir = fs::is_directory(host, ec);',
                         '    if (t.read_only && (options & 0x1000)) { IoStatus(iosb, kAccessDenied, 0); return kAccessDenied; }\n'
                         '    auto f = std::make_shared<File>();\n    f->read_only = t.read_only;\n    f->host = host;\n    f->xbox = Normalize(name);\n    f->dir = fs::is_directory(host, ec);')
    files = replace_once(files, 'f->fp = std::fopen(host.c_str(), "r+b");',
                         'f->fp = std::fopen(host.c_str(), t.read_only ? "rb" : "r+b");')
    files = replace_once(files, 'if (write && f->entry)', 'if (write && (f->entry || f->read_only))')
    files = replace_once(files, 'if (f->entry && cls != 14 && cls != 4)',
                         'if ((f->entry || f->read_only) && cls != 14 && cls != 4)')
    files = replace_once(files, '    std::string host = ResolvePath(target, root);',
                         '    const auto destination = Resolve(target, root);\n'
                         '    if (destination.read_only || destination.disc) return kAccessDenied;\n'
                         '    std::string host = destination.host;')
    files = replace_once(files, '    std::string host = ResolvePath(ReadAnsiString(R32(a[0] + 4)), R32(a[0]));',
                         '    const auto destination = Resolve(ReadAnsiString(R32(a[0] + 4)), R32(a[0]));\n'
                         '    if (destination.read_only || destination.disc) return kAccessDenied;\n'
                         '    std::string host = destination.host;')
    files = replace_once(files, 'if (f->fp) std::fflush(f->fp);\n    IoStatus(a[1]',
                         'if (f->fp && !f->read_only) std::fflush(f->fp);\n    IoStatus(a[1]')
    start = files.index('    uint64_t size = f->dir ? 0 : f->entry ?')
    end = files.index('    std::memset(HostPtr(out), 0, len);', start)
    files = files[:start] + '''    uint64_t size = 0, time = 0;
    if (cls == 4 || cls == 5 || cls == 34) {
        if (f->entry) {
            size = f->dir ? 0 : f->entry->size;
            time = MountedDisc()->Time();
        } else if (f->fp) {
            std::lock_guard<std::mutex> lock(f->io);
            struct stat info{};
            if ((!f->read_only && std::fflush(f->fp) != 0) ||
                fstat(fileno(f->fp), &info) != 0 || info.st_size < 0) {
                LOGE("files: handle metadata failed guest=%s host=%s class=%u errno=%d fs=%08X",
                     f->xbox.c_str(), f->host.string().c_str(), cls, errno, fsdevGetLastResult());
                IoStatus(iosb, 0xC0000185, 0);
                return 0xC0000185;
            }
            size = uint64_t(info.st_size);
            time = uint64_t(info.st_mtime) * 10000000ull + 116444736000000000ull;
        } else {
            time = ToFileTime(fs::last_write_time(f->host, ec));
            if (ec) { IoStatus(iosb, 0xC0000185, 0); return 0xC0000185; }
        }
        NX_FILE_REPORT(8, "files: metadata guest=%s class=%u size=%llu read_only=%d",
                       f->xbox.c_str(), cls, static_cast<unsigned long long>(size), int(f->read_only));
    }
''' + files[end:]
    files = replace_once(files, '            uint64_t t = f->entry ? MountedDisc()->Time() : ToFileTime(fs::last_write_time(f->host, ec));',
                         '            uint64_t t = time;')
    files = replace_once(files, '            if (f->entry) FillNetworkInfo(out, *f->entry);\n            else FillNetworkInfo(out, f->host);',
                         '            FillNetworkInfo(out, time, size, f->dir);')
    files = replace_once(files, '''                std::fflush(f->fp);
                uint64_t size = fs::file_size(f->host, ec);
                if (cls == 20 || R64(in) < size) fs::resize_file(f->host, R64(in), ec);''', '''                struct stat info{};
                if (std::fflush(f->fp) != 0 || fstat(fileno(f->fp), &info) != 0 || info.st_size < 0 ||
                    ((cls == 20 || R64(in) < uint64_t(info.st_size)) &&
                     (R64(in) > uint64_t(INT64_MAX) || ftruncate(fileno(f->fp), off_t(R64(in))) != 0))) {
                    LOGE("files: resize failed guest=%s errno=%d fs=%08X", f->xbox.c_str(), errno, fsdevGetLastResult());
                    IoStatus(iosb, 0xC0000185, 0);
                    return 0xC0000185;
                }''')
    # Release diagnostics retain only bounded startup file events and actual
    # failures. Otherwise the game's disc-error screen hides the failing path.
    files = files.replace('LOG_FIRST(200, "missing %s", name.c_str());',
                          'NX_FILE_REPORT(32, "files: missing %s", name.c_str());')
    files = files.replace('LOG_FIRST(400, "open %s", name.c_str());',
                          'NX_FILE_REPORT(32, "files: open %s", name.c_str());')
    files = files.replace('LOGW("NtCreateFile:', 'LOGE("NtCreateFile:')
    files = replace_once(files, 'namespace kernel {', '''#define NX_FILE_REPORT(n, ...) do { \\
    static std::atomic<unsigned> count{0}; \\
    if (count.fetch_add(1, std::memory_order_relaxed) < (n)) LOGR(__VA_ARGS__); \\
} while (0)

namespace kernel {''')
    start = files.index('void Seek(FILE* fp, uint64_t pos) {')
    end = files.index('\nvoid IoStatus(', start)
    files = files[:start] + '''bool Seek(FILE* fp, uint64_t pos) {
    if (fseeko(fp, off_t(pos), SEEK_SET) == 0) return true;
    LOGE("files: seek offset=%llu errno=%d fs=%08X", static_cast<unsigned long long>(pos), errno, fsdevGetLastResult());
    return false;
}
''' + files[end:]
    files = replace_once(files, '    if (f->fp) Seek(f->fp, pos);', '''    NX_FILE_REPORT(8, "files: %s guest=%s host=%s offset=%llu bytes=%u address=%08X",
                   write ? "write" : "read", f->xbox.c_str(), f->host.string().c_str(),
                   static_cast<unsigned long long>(pos), len, buf);
    if (f->fp) {
        std::clearerr(f->fp);
        if (!Seek(f->fp, pos)) return Finish(*f, 0xC0000185, 0, event, apc, apc_ctx, iosb);
    }''')
    files = replace_once(files, 'std::fwrite(HostPtr(at), 1, chunk, f->fp)',
                          'nxposix::Write(f->fp, HostPtr(at), chunk)')
    files = replace_once(files, 'std::fread(HostPtr(at), 1, chunk, f->fp)',
                          'nxposix::Read(f->fp, HostPtr(at), chunk)')
    files = replace_once(files, '    f->pos = pos + n;', '''    f->pos = pos + n;
    if (f->fp && std::ferror(f->fp)) {
        const int io_error = errno;
        const Result fs_result = fsdevGetLastResult();
        LOGE("files: %s failed guest=%s host=%s offset=%llu requested=%u done=%u errno=%d fs=%08X",
             write ? "write" : "read", f->xbox.c_str(), f->host.string().c_str(),
             static_cast<unsigned long long>(pos), len, n, io_error, fs_result);
        const uint32_t status = write && io_error == ENOSPC ? 0xC000007F : 0xC0000185;
        return Finish(*f, status, n, event, apc, apc_ctx, iosb);
    }''')
    files = replace_once(files, '        if (!f->fp) return kAccessDenied;', '''        if (!f->fp) {
            LOGE("files: open failed guest=%s host=%s errno=%d fs=%08X", name.c_str(), host.c_str(), errno, fsdevGetLastResult());
            IoStatus(iosb, kAccessDenied, 0);
            return kAccessDenied;
        }''')
    (out / "files.cpp").write_text(files, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    before = {p: (p.read_bytes(), p.stat()) for p in args.out.rglob('*') if p.is_file()} if args.out.exists() else {}
    subprocess.run([sys.executable, Path(__file__).with_name('prepare_recomp_runtime.py'),
                    '--sdk', args.sdk, '--out', args.out], check=True)
    prepare(args.sdk, args.out)
    for path, (content, stat) in before.items():
        if path.exists() and path.read_bytes() == content:
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
