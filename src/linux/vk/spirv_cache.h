#pragma once

#include <array>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <vector>

namespace vkshader::cache {

// Bump the directory version when changing the pinned compiler or its options.
inline std::filesystem::path Path(const std::filesystem::path& root, uint64_t key) {
    char name[32];
    std::snprintf(name, sizeof(name), "%016llx.spv", static_cast<unsigned long long>(key));
    return root / "vulkan_spirv_v1" / name;
}

constexpr uint32_t kMaxWords = 262144;
using Header = std::array<uint32_t, 7>;

inline uint64_t Checksum(const std::vector<uint32_t>& words) {
    uint64_t result = 14695981039346656037ull;
    for (uint32_t word : words) {
        for (unsigned shift = 0; shift < 32; shift += 8)
            result = (result ^ uint8_t(word >> shift)) * 1099511628211ull;
    }
    return result;
}

inline bool Valid(const std::vector<uint32_t>& words) {
    if (words.size() < 5 || words.size() > kMaxWords || words[0] != 0x07230203 ||
        words[1] != 0x00010500 || !words[3] || words[4]) return false;
    for (size_t offset = 5; offset < words.size();) {
        uint32_t count = words[offset] >> 16;
        if (!count || count > words.size() - offset) return false;
        offset += count;
    }
    return true;
}

inline bool Load(const std::filesystem::path& root, uint64_t key, std::vector<uint32_t>& words) {
    words.clear();
    std::ifstream in(Path(root, key), std::ios::binary | std::ios::ate);
    if (!in) return false;
    const auto bytes = in.tellg();
    if (bytes < std::streamoff(sizeof(Header)) ||
        bytes > std::streamoff(sizeof(Header) + kMaxWords * sizeof(uint32_t))) return false;
    in.seekg(0);
    Header h{};
    in.read(reinterpret_cast<char*>(h.data()), sizeof(h));
    if (!in || h[0] != 0x43565053 || h[1] != 1 || h[2] != uint32_t(key) || h[3] != uint32_t(key >> 32) ||
        h[4] < 5 || h[4] > kMaxWords || bytes != std::streamoff(sizeof(h) + h[4] * sizeof(uint32_t))) return false;
    std::vector<uint32_t> data(h[4]);
    in.read(reinterpret_cast<char*>(data.data()), std::streamsize(data.size() * sizeof(uint32_t)));
    if (!in || !Valid(data) || Checksum(data) != (uint64_t(h[6]) << 32 | h[5])) return false;
    words.swap(data);
    return true;
}

inline bool Save(const std::filesystem::path& root, uint64_t key, const std::vector<uint32_t>& words) {
    if (!Valid(words)) return false;
    std::error_code ec;
    const auto path = Path(root, key);
    std::filesystem::create_directories(path.parent_path(), ec);
    if (ec) return false;
    auto temporary = path;
    temporary += ".tmp";
    const uint64_t checksum = Checksum(words);
    const Header h{0x43565053, 1, uint32_t(key), uint32_t(key >> 32), uint32_t(words.size()),
                   uint32_t(checksum), uint32_t(checksum >> 32)};
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        out.write(reinterpret_cast<const char*>(h.data()), sizeof(h));
        out.write(reinterpret_cast<const char*>(words.data()), std::streamsize(words.size() * sizeof(uint32_t)));
        out.close();
        if (!out) return false;
    }
    std::filesystem::rename(temporary, path, ec);
    std::error_code check_error;
    if (ec && std::filesystem::is_regular_file(path, check_error)) {
        // Some filesystems refuse replacing an existing cache entry by rename.
        std::filesystem::remove(path, ec);
        if (!ec) std::filesystem::rename(temporary, path, ec);
    }
    return !ec;
}

}  // namespace vkshader::cache
