#include "switch/posix_io.h"
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

extern "C" size_t __real_fread(void*, size_t, size_t, FILE*);
extern "C" size_t __real_fwrite(const void*, size_t, size_t, FILE*);
extern "C" ssize_t __real_read(int, void*, size_t);

namespace {
constexpr size_t kTransferLimit = 256u << 10;
constexpr size_t kPayloadSize = kTransferLimit * 2 + 123;
alignas(4096) std::array<unsigned char, 8u << 20> guest;
std::atomic<unsigned> rejected_calls;
std::atomic<unsigned> staged_calls;

bool CheckKernelBuffer(const void* data, size_t size) {
    const auto begin = reinterpret_cast<uintptr_t>(data);
    const auto guest_begin = reinterpret_cast<uintptr_t>(guest.data());
    if (size && begin < guest_begin + guest.size() && begin + size > guest_begin) {
        ++rejected_calls;
        errno = EFAULT;
        return false;
    }
    // This models the Horizon filesystem buffer contract, not a particular
    // number of calls or a particular staging-buffer implementation.
    assert(begin % 4096 == 0 && size <= kTransferLimit);
    ++staged_calls;
    return true;
}

std::vector<unsigned char> Payload(unsigned seed) {
    std::vector<unsigned char> bytes(kPayloadSize);
    for (size_t i = 0; i < bytes.size(); ++i) bytes[i] = (i * 37 + seed * 19) & 255;
    return bytes;
}

FILE* Fixture(const std::vector<unsigned char>& bytes) {
    FILE* file = tmpfile();
    assert(file);
    assert(__real_fwrite(bytes.data(), 1, bytes.size(), file) == bytes.size());
    assert(fflush(file) == 0);
    rewind(file);
    return file;
}

unsigned char* GuestAt(size_t offset, size_t count) {
    assert(offset > 0 && offset + count < guest.size());
    std::memset(guest.data() + offset - 1, 0xD6, count + 2);
    return guest.data() + offset;
}

void AssertCanaries(const unsigned char* data, size_t size) {
    assert(data[-1] == 0xD6 && data[size] == 0xD6);
}

void TestBaselineRejection() {
    auto bytes = Payload(1);
    FILE* file = Fixture(bytes);
    auto* data = GuestAt(1, bytes.size());
    errno = 0;
    assert(fread(data, 1, 17, file) == 0 && errno == EFAULT);
    errno = 0;
    assert(fwrite(data, 1, 17, file) == 0 && errno == EFAULT);
    errno = 0;
    assert(read(fileno(file), data, 17) == -1 && errno == EFAULT);
    assert(ftell(file) == 0 && rejected_calls == 3);
    assert(fclose(file) == 0);
}

void TestReadAndEof() {
    auto bytes = Payload(3);
    FILE* file = Fixture(bytes);
    constexpr size_t extra = 97;
    auto* data = GuestAt(1, bytes.size() + extra);
    assert(nxposix::Read(file, data, bytes.size() + extra) == bytes.size());
    assert(std::memcmp(data, bytes.data(), bytes.size()) == 0);
    for (size_t i = bytes.size(); i < bytes.size() + extra; ++i) assert(data[i] == 0xD6);
    AssertCanaries(data, bytes.size() + extra);
    assert(feof(file) && !ferror(file));
    assert(nxposix::Read(file, data, 1) == 0);
    assert(fclose(file) == 0);
    assert(nxposix::Read(nullptr, nullptr, 0) == 0);
}

void TestWrite() {
    auto bytes = Payload(7);
    FILE* file = tmpfile();
    assert(file);
    auto* data = GuestAt(3, bytes.size());
    std::memcpy(data, bytes.data(), bytes.size());
    assert(nxposix::Write(file, data, bytes.size()) == bytes.size());
    assert(fflush(file) == 0 && ftell(file) == long(bytes.size()));
    AssertCanaries(data, bytes.size());
    assert(std::memcmp(data, bytes.data(), bytes.size()) == 0);
    rewind(file);
    std::vector<unsigned char> result(bytes.size());
    assert(__real_fread(result.data(), 1, result.size(), file) == result.size());
    assert(result == bytes);
    assert(fclose(file) == 0);
    assert(nxposix::Write(nullptr, nullptr, 0) == 0);
}

void TestRealErrors() {
    FILE* reader = tmpfile();
    assert(reader);
    assert(setvbuf(reader, nullptr, _IONBF, 0) == 0);
    assert(close(fileno(reader)) == 0);
    auto* data = GuestAt(5, 31);
    errno = 0;
    assert(nxposix::Read(reader, data, 31) == 0);
    assert(ferror(reader) && errno == EBADF);
    for (size_t i = 0; i < 31; ++i) assert(data[i] == 0xD6);
    AssertCanaries(data, 31);
    fclose(reader);

    FILE* writer = tmpfile();
    assert(writer);
    assert(setvbuf(writer, nullptr, _IONBF, 0) == 0);
    assert(close(fileno(writer)) == 0);
    errno = 0;
    assert(nxposix::Write(writer, data, 31) == 0);
    assert(ferror(writer) && errno == EBADF);
    AssertCanaries(data, 31);
    fclose(writer);
}

void TestConcurrentFiles() {
    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 4; ++worker) workers.emplace_back([worker] {
        auto bytes = Payload(worker + 11);
        FILE* input = Fixture(bytes);
        FILE* output = tmpfile();
        assert(output);
        auto* data = GuestAt(0x100001 + worker * 0x100000, bytes.size());
        for (unsigned pass = 0; pass < 3; ++pass) {
            rewind(input);
            rewind(output);
            assert(nxposix::Read(input, data, bytes.size()) == bytes.size());
            assert(std::memcmp(data, bytes.data(), bytes.size()) == 0);
            assert(nxposix::Write(output, data, bytes.size()) == bytes.size());
            assert(fflush(output) == 0);
            rewind(output);
            std::vector<unsigned char> actual(bytes.size());
            assert(__real_fread(actual.data(), 1, actual.size(), output) == actual.size());
            assert(actual == bytes);
            AssertCanaries(data, bytes.size());
        }
        assert(fclose(input) == 0 && fclose(output) == 0);
    });
    for (auto& worker : workers) worker.join();
}

void TestPread() {
    auto bytes = Payload(23);
    FILE* fixture = Fixture(bytes);
    int fd = fileno(fixture);
    assert(lseek(fd, 7, SEEK_SET) == 7);
    auto* data = GuestAt(7, bytes.size() + 29);
    assert(nxposix::Pread(fd, data, bytes.size() + 29, 0) == ssize_t(bytes.size()));
    assert(std::memcmp(data, bytes.data(), bytes.size()) == 0);
    for (size_t i = bytes.size(); i < bytes.size() + 29; ++i) assert(data[i] == 0xD6);
    AssertCanaries(data, bytes.size() + 29);
    assert(lseek(fd, 0, SEEK_CUR) == 7);
    errno = 0;
    assert(nxposix::Pread(fd, data, 1, -1) == -1 && errno == EINVAL);
    assert(lseek(fd, 0, SEEK_CUR) == 7);
    errno = 0;
    assert(nxposix::Pread(-1, data, 1, 0) == -1 && errno == EBADF);
    assert(nxposix::Pread(fd, data, 17, off_t(bytes.size())) == 0);
    assert(lseek(fd, 0, SEEK_CUR) == 7);

    char write_only_path[] = "/tmp/shaolin-pread-error-XXXXXX";
    int temporary = mkstemp(write_only_path);
    assert(temporary >= 0 && close(temporary) == 0);
    int write_only = open(write_only_path, O_WRONLY);
    assert(write_only >= 0 && unlink(write_only_path) == 0);
    assert(lseek(write_only, 7, SEEK_SET) == 7);
    errno = 0;
    assert(nxposix::Pread(write_only, data, 31, 0) == -1 && errno == EBADF);
    assert(lseek(write_only, 0, SEEK_CUR) == 7);
    assert(close(write_only) == 0);

    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 4; ++worker) workers.emplace_back([&, worker] {
        auto* destination = GuestAt(0x100003 + worker * 0x100000, bytes.size());
        for (unsigned pass = 0; pass < 8; ++pass) {
            size_t at = (worker * 71 + pass * 193) % 1009;
            size_t count = bytes.size() - at;
            assert(nxposix::Pread(fd, destination, count, off_t(at)) == ssize_t(count));
            assert(std::memcmp(destination, bytes.data() + at, count) == 0);
            AssertCanaries(destination, bytes.size());
        }
    });
    for (auto& worker : workers) worker.join();
    assert(lseek(fd, 0, SEEK_CUR) == 7);
    assert(fclose(fixture) == 0);
}
}

extern "C" size_t __wrap_fread(void* data, size_t size, size_t count, FILE* file) {
    assert(!size || count <= size_t(-1) / size);
    return CheckKernelBuffer(data, size * count) ? __real_fread(data, size, count, file) : 0;
}
extern "C" size_t __wrap_fwrite(const void* data, size_t size, size_t count, FILE* file) {
    assert(!size || count <= size_t(-1) / size);
    return CheckKernelBuffer(data, size * count) ? __real_fwrite(data, size, count, file) : 0;
}
extern "C" ssize_t __wrap_read(int fd, void* data, size_t count) {
    return CheckKernelBuffer(data, count) ? __real_read(fd, data, count) : -1;
}

int main() {
    TestBaselineRejection();
    TestReadAndEof();
    TestWrite();
    TestRealErrors();
    TestConcurrentFiles();
    TestPread();
    assert(rejected_calls == 3 && staged_calls > 0);
    std::printf("file IO regression passed: direct guest pointers rejected; native staging accepted; "
                "large unaligned reads/writes, EOF/errors, concurrency and positioned reads preserved\n");
}
