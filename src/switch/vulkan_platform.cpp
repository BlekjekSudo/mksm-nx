#include "switch/vulkan_platform.h"
#include <switch.h>
#include <cstdio>

extern "C" PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance instance, const char* name);

namespace nxvk {
namespace { char error[96] = {}; }
const char* Error() { return error; }
PFN_vkGetInstanceProcAddr ProcAddress() { return &vkGetInstanceProcAddr; }
const char* const* Extensions(uint32_t* count) {
    static const char* names[] = {VK_KHR_SURFACE_EXTENSION_NAME, VK_NN_VI_SURFACE_EXTENSION_NAME};
    *count = 2;
    return names;
}
bool CreateSurface(void*, VkInstance instance, const VkAllocationCallbacks*, VkSurfaceKHR* surface) {
    auto create = reinterpret_cast<PFN_vkCreateViSurfaceNN>(vkGetInstanceProcAddr(instance, "vkCreateViSurfaceNN"));
    if (!create) { std::snprintf(error, sizeof error, "VK_NN_vi_surface unavailable"); return false; }
    VkViSurfaceCreateInfoNN info{VK_STRUCTURE_TYPE_VI_SURFACE_CREATE_INFO_NN};
    info.window = nwindowGetDefault();
    VkResult result = create(instance, &info, nullptr, surface);
    if (result != VK_SUCCESS) std::snprintf(error, sizeof error, "vkCreateViSurfaceNN: %d", int(result));
    return result == VK_SUCCESS;
}
bool WindowSize(void*, int* width, int* height) {
    u32 w = 0, h = 0;
    Result result = nwindowGetDimensions(nwindowGetDefault(), &w, &h);
    if (R_FAILED(result)) {
        std::snprintf(error, sizeof error, "nwindowGetDimensions: %08X", result);
        return false;
    }
    *width = int(w);
    *height = int(h);
    return *width > 0 && *height > 0;
}
}
