#pragma once
// Cygwin's malloc.h and C++ stdlib.h disagree about exception annotations.
// The libnx source only needs the POSIX-aligned allocator declaration.
#include <cstdlib>
extern "C" void* memalign(size_t alignment, size_t size);
