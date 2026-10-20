#include "linux/vk/spirv_cache.h"
#include <cassert>
#include <chrono>

int main() {
    namespace cache = vkshader::cache;
    const auto root = std::filesystem::temp_directory_path() /
        ("shaolin-spirv-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    constexpr uint64_t key = 0x123456789abcdef0ull;
    // SPIR-V 1.5 header and a complete OpCapability instruction. Semantic
    // shader validation belongs to the compiler/driver; this tests disk integrity.
    const std::vector<uint32_t> source{0x07230203, 0x00010500, 0, 2, 0, 0x00020011, 1};
    std::vector<uint32_t> loaded{42};
    assert(!cache::Load(root, key, loaded) && loaded.empty());
    assert(!std::filesystem::exists(root));
    assert(cache::Save(root, key, source));
    assert(cache::Load(root, key, loaded) && loaded == source);
    assert(!cache::Load(root, key + 1, loaded) && loaded.empty());
    const auto path = cache::Path(root, key);
    {
        std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
        file.seekp(sizeof(cache::Header) + 4);
        uint32_t bad_version = 0x00010600;
        file.write(reinterpret_cast<const char*>(&bad_version), 4);
    }
    assert(!cache::Load(root, key, loaded));
    assert(cache::Save(root, key, source));
    assert(cache::Load(root, key, loaded) && loaded == source);
    std::filesystem::copy_file(path, cache::Path(root, key + 1));
    assert(!cache::Load(root, key + 1, loaded));
    {
        std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
        file.seekp(-4, std::ios::end);
        uint32_t corrupted = 17;
        file.write(reinterpret_cast<const char*>(&corrupted), 4);
    }
    assert(!cache::Load(root, key, loaded));
    assert(cache::Save(root, key, source));
    std::filesystem::resize_file(path, sizeof(cache::Header) + 4);
    assert(!cache::Load(root, key, loaded));
    std::vector<uint32_t> malformed = source;
    malformed[5] = 0;
    assert(!cache::Save(root, key, malformed));
    malformed[5] = 0x00030011;
    assert(!cache::Save(root, key, malformed));
    malformed.resize(cache::kMaxWords + 1);
    assert(!cache::Save(root, key, malformed));
    std::filesystem::remove_all(root);
}
