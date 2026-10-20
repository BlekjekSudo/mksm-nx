#pragma once
#include <cstdint>
#include <vulkan/vulkan.h>

namespace nxvk {
const char* Error();
const char* const* Extensions(uint32_t* count);
PFN_vkGetInstanceProcAddr ProcAddress();
bool CreateSurface(void* window, VkInstance instance, const VkAllocationCallbacks*, VkSurfaceKHR* surface);
bool WindowSize(void* window, int* width, int* height);
}
