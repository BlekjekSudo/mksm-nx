#include <switch.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <utility>
#include <vector>
#include "kernel/kernel.h"
#include "kernel/disc.h"

Result fsdevGetLastResult() { return 0; }
#define LOG_FIRST(n, ...) LOGI(__VA_ARGS__)
#include SHAOLIN_SWITCH_FILES_TEST_SOURCE

extern "C" FILE* __real_fopen(const char*, const char*);
extern "C" int __real_fclose(FILE*);
extern "C" int __real_stat(const char*, struct stat*);
extern "C" int __real_fstat(int, struct stat*);
extern "C" int __real_ftruncate(int, off_t);

namespace fs = std::filesystem;
namespace {
struct OpenedFile { std::string path; bool writable; };
std::map<FILE*, OpenedFile> opened;
bool fail_fstat;
bool fail_ftruncate;
unsigned blocked_path_queries;
unsigned handle_queries;

std::string NormalPath(const char* path) { return fs::path(path).lexically_normal().string(); }
bool HasWriter(const char* path) {
    const auto normalized = NormalPath(path);
    for (const auto& [file, info] : opened)
        if (info.writable && info.path == normalized) return true;
    return false;
}
}

extern "C" FILE* __wrap_fopen(const char* path, const char* mode) {
    // Model an FS service refusing another open while a writable view exists.
    if (HasWriter(path)) { errno = EBUSY; return nullptr; }
    FILE* file = __real_fopen(path, mode);
    if (file) opened.emplace(file, OpenedFile{NormalPath(path),
                         std::strchr(mode, '+') || std::strchr(mode, 'w') || std::strchr(mode, 'a')});
    return file;
}
extern "C" int __wrap_fclose(FILE* file) { opened.erase(file); return __real_fclose(file); }
extern "C" int __wrap_stat(const char* path, struct stat* info) {
    // libnx path stat internally opens another read handle; fstat does not.
    if (HasWriter(path)) { ++blocked_path_queries; errno = EBUSY; return -1; }
    return __real_stat(path, info);
}
extern "C" int __wrap_fstat(int fd, struct stat* info) {
    ++handle_queries;
    if (fail_fstat) { errno = EIO; return -1; }
    return __real_fstat(fd, info);
}
extern "C" int __wrap_ftruncate(int fd, off_t size) {
    if (fail_ftruncate) { errno = EIO; return -1; }
    return __real_ftruncate(fd, size);
}

namespace kernel {
uint8_t* g_base = nullptr;
static std::map<uint32_t, ObjectRef>& Handles() { static std::map<uint32_t, ObjectRef> handles; return handles; }
static std::map<uint32_t, KernelFn>& Exports() { static std::map<uint32_t, KernelFn> exports; return exports; }
void RegisterExport(uint32_t ordinal, const char*, KernelFn function) { Exports()[ordinal] = function; }
uint32_t NewHandle(ObjectRef object) {
    static uint32_t next = 0x1000;
    Handles()[next] = std::move(object);
    return std::exchange(next, next + 4);
}
ObjectRef FromHandle(uint32_t handle) {
    auto found = Handles().find(handle);
    return found == Handles().end() ? nullptr : found->second;
}
bool CloseHandle(uint32_t handle) { return Handles().erase(handle) != 0; }
int32_t SetEvent(Object& object) { object.signal = 1; return 0; }
int32_t ResetEvent(Object& object) { object.signal = 0; return 0; }
Thread* Current() { return nullptr; }
void QueueApc(Thread&, uint32_t, uint32_t, uint32_t, uint32_t) {}
const Disc* MountedDisc() { return nullptr; }
const Disc::Entry* Disc::Find(const std::vector<std::string>&) const { std::abort(); }
uint32_t Disc::Read(const Entry&, uint64_t, void*, uint32_t) const { std::abort(); }
}

namespace {
uint32_t Call(uint32_t ordinal, std::initializer_list<uint32_t> arguments) {
    X86Context context{};
    context.esp = 0x100;
    unsigned i = 0;
    for (auto value : arguments) kernel::W32(context.esp + 4 + 4 * i++, value);
    kernel::Exports().at(ordinal)(context, kernel::g_base);
    return context.eax;
}

void Name(const std::string& name) {
    std::memcpy(kernel::HostPtr(0x500), name.data(), name.size());
    kernel::W16(0x400, uint16_t(name.size()));
    kernel::W32(0x404, 0x500);
    kernel::W32(0x300, 0);
    kernel::W32(0x304, 0x400);
    kernel::W32(0x200, 0);
}
uint32_t Open(const std::string& name, uint32_t disposition = 1, uint32_t options = 0x40, bool nt_open = false) {
    Name(name);
    uint32_t status = nt_open ? Call(202, {0x200, 0xC0000000, 0x300, 0x250, 0, options})
                              : Call(190, {0x200, 0xC0000000, 0x300, 0x250, 0, 0, 0, disposition, options});
    return status ? 0 : kernel::R32(0x200);
}

void Metadata(uint32_t handle, uint64_t expected_size) {
    const unsigned before = handle_queries;
    assert(Call(211, {handle, 0x250, 0x1000, 0x18, 5}) == 0);
    assert(kernel::R32(0x250) == 0 && kernel::R32(0x254) == 0x18);
    assert(kernel::R64(0x1000) == ((expected_size + 4095) & ~uint64_t(4095)));
    assert(kernel::R64(0x1008) == expected_size);
    assert(kernel::G<uint8_t>(0x1015) == 0);
    assert(Call(211, {handle, 0x250, 0x1000, 0x38, 34}) == 0);
    assert(kernel::R64(0x1028) == expected_size);
    assert(Call(211, {handle, 0x250, 0x1000, 0x28, 4}) == 0);
    assert(kernel::R32(0x1020) == 0x80);
    assert(handle_queries >= before + 3);
}

uint32_t Resize(uint32_t handle, uint32_t information_class, uint64_t size) {
    kernel::W64(0x1800, size);
    return Call(226, {handle, 0x250, 0x1800, 8, information_class});
}

void ResizeFailure(uint32_t handle, uint32_t information_class, uint64_t size, uint64_t expected_size) {
    const auto status = Resize(handle, information_class, size);
    assert(status != 0 && kernel::R32(0x250) == status && kernel::R32(0x254) == 0);
    // Check the real descriptor while the injected failure remains enabled.
    assert(opened.size() == 1);
    struct stat info{};
    assert(__real_fstat(fileno(opened.begin()->first), &info) == 0);
    assert(uint64_t(info.st_size) == expected_size);
}
}

int main(int argc, char** argv) {
    std::vector<uint8_t> guest(65536);
    kernel::g_base = guest.data();
    char temporary[] = "/tmp/shaolin-metadata-XXXXXX";
    char* directory = mkdtemp(temporary);
    assert(directory);
    const fs::path root(directory);
    fs::create_directory(root / "dvd");
    fs::create_directory(root / "hdd");
    const auto asset = root / "dvd/GAMEDATA.WAD";
    constexpr uint64_t asset_size = 59392;
    { std::ofstream file(asset, std::ios::binary); file << std::string(asset_size, 'a'); }

    // This demonstrates why a path metadata query is unsafe for an open
    // writable file, while keeping the filesystem fixture itself real.
    FILE* baseline = std::fopen(asset.c_str(), "r+b");
    assert(baseline);
    std::error_code error;
    assert(fs::file_size(asset, error) == uintmax_t(-1) && error);
    assert(blocked_path_queries > 0);
    struct stat native{};
    assert(fstat(fileno(baseline), &native) == 0 && native.st_size == asset_size);
    assert(std::fclose(baseline) == 0);
    blocked_path_queries = 0;

    kernel::MountDefaults((root / "dvd").string(), (root / "hdd").string());
    kernel::CreateSymbolicLink("\\??\\E:", "\\Device\\Harddisk0\\Partition1");
    const auto media = Open("D:\\GAMEDATA.WAD");
    if (argc == 2 && std::string(argv[1]) == "baseline") {
        const auto status = Call(211, {media, 0x250, 0x1000, 0x18, 5});
        const auto size = kernel::R64(0x1008);
        std::printf("old prepared NtQueryInformationFile: status=%08X size=%016llX blocked_path_queries=%u\n",
                    status, static_cast<unsigned long long>(size), blocked_path_queries);
        assert(status == 0 && size == uint64_t(-1) && blocked_path_queries > 0);
        kernel::CloseHandle(media);
        assert(fs::remove(asset));
        for (const char* name : {"E", "C", "X", "Y", "Z"}) fs::remove(root / "hdd" / name);
        assert(fs::remove(root / "hdd") && fs::remove(root / "dvd") && fs::remove(root));
        return 0;
    }
    assert(media && opened.size() == 1 && !opened.begin()->second.writable);
    Metadata(media, asset_size);
    const auto duplicate = Open("D:\\GAMEDATA.WAD", 1, 0x40, true);
    assert(duplicate && opened.size() == 2);
    Metadata(duplicate, asset_size);
    assert(Call(219, {media, 0, 0, 0, 0x250, 0x2000, 64, 0}) == 0);
    for (unsigned i = 0; i < 64; ++i) assert(kernel::G<uint8_t>(0x2000 + i) == 'a');
    assert(Call(236, {media, 0, 0, 0, 0x250, 0x2000, 1, 0}) == kernel::kAccessDenied);
    assert(Resize(media, 19, 0) == kernel::kAccessDenied);
    assert(Resize(media, 20, 0) == kernel::kAccessDenied);
    Metadata(media, asset_size);
    kernel::CloseHandle(duplicate);
    kernel::CloseHandle(media);
    assert(!Open("D:\\GAMEDATA.WAD", 5));

    const auto save = Open("E:\\buffered.dat", 5);
    assert(save && opened.size() == 1 && opened.begin()->second.writable);
    const std::string saved_bytes = "buffered save bytes";
    std::memcpy(kernel::HostPtr(0x2000), saved_bytes.data(), saved_bytes.size());
    assert(Call(236, {save, 0, 0, 0, 0x250, 0x2000, uint32_t(saved_bytes.size()), 0}) == 0);
    Metadata(save, saved_bytes.size());
    assert(blocked_path_queries == 0);

    assert(Resize(save, 19, saved_bytes.size() + 8192) == 0);
    Metadata(save, saved_bytes.size());
    const uint64_t extended_size = saved_bytes.size() + 4097;
    assert(Resize(save, 20, extended_size) == 0);
    Metadata(save, extended_size);
    kernel::W64(0x1900, 0);
    assert(Call(219, {save, 0, 0, 0, 0x250, 0x2000, uint32_t(extended_size), 0x1900}) == 0);
    assert(std::memcmp(kernel::HostPtr(0x2000), saved_bytes.data(), saved_bytes.size()) == 0);
    for (size_t i = saved_bytes.size(); i < extended_size; ++i) assert(kernel::G<uint8_t>(0x2000 + i) == 0);
    assert(Resize(save, 20, 7) == 0);
    Metadata(save, 7);
    assert(Resize(save, 19, 3) == 0);
    Metadata(save, 3);
    assert(blocked_path_queries == 0);

    fail_ftruncate = true;
    ResizeFailure(save, 20, 0, 3);
    ResizeFailure(save, 19, 0, 3);
    fail_ftruncate = false;
    Metadata(save, 3);

    fail_fstat = true;
    ResizeFailure(save, 20, 17, 3);
    ResizeFailure(save, 19, 0, 3);
    for (uint32_t information_class : {4u, 5u, 34u}) {
        std::memset(kernel::HostPtr(0x1000), 0xA7, 0x40);
        const auto status = Call(211, {save, 0x250, 0x1000, 0x40, information_class});
        assert(status != 0 && kernel::R32(0x250) == status && kernel::R32(0x254) == 0);
        for (unsigned i = 0; i < 0x40; ++i) assert(kernel::G<uint8_t>(0x1000 + i) == 0xA7);
    }
    fail_fstat = false;
    Metadata(save, 3);
    kernel::CloseHandle(save);
    assert(opened.empty());
    assert(fs::remove(root / "hdd/E/buffered.dat"));
    assert(fs::remove(asset));
    // Only remove the known, empty synthetic directories created by this test.
    for (const char* directory_name : {"E", "C", "X", "Y", "Z"}) fs::remove(root / "hdd" / directory_name);
    assert(fs::remove(root / "hdd") && fs::remove(root / "dvd") && fs::remove(root));
    std::puts("file metadata regression passed: existing-handle metadata, read-only DVD opens, buffered save size, "
              "truncate/extend/allocation semantics and errors preserved");
}
