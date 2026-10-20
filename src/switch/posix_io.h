#pragma once
#include <sys/types.h>
#include <cstddef>
#include <cstdio>
namespace nxposix {
ssize_t Pread(int fd, void* data, size_t count, off_t offset);
size_t Read(FILE* file, void* data, size_t count);
size_t Write(FILE* file, const void* data, size_t count);
}
