#pragma once
#include <cstdint>
namespace nxthreads {
struct DetachedStats { uint64_t accepted, cleaned; };
DetachedStats DetachedSnapshot();
}
