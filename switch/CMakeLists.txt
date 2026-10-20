cmake_minimum_required(VERSION 3.25)
project(ShaolinMonksRecompSwitch VERSION 0.2.0 LANGUAGES C CXX)
if(NOT NINTENDO_SWITCH)
    message(FATAL_ERROR "Use the devkitPro Switch.cmake toolchain")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
get_filename_component(ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(SHAOLIN_SDK "${ROOT}/third_party/XboxXBERecomp" CACHE PATH "Pinned SDK")
set(SHAOLIN_GENERATED "${ROOT}/build/switch/generated" CACHE PATH "Generated game translation")
set(SHAOLIN_ADAPTED "${ROOT}/build/switch/adapted" CACHE PATH "Prepared libnx runtime")
set(SHAOLIN_MESA "" CACHE PATH "Verified switch-mesa SDK")
set(SHAOLIN_GENERATED_OPT "-O2" CACHE STRING "Generated code optimization")
set(GENERATED "${SHAOLIN_GENERATED}/x86_func_mapping.cpp")
foreach(part RANGE 127)
    list(APPEND GENERATED "${SHAOLIN_GENERATED}/x86_recomp.${part}.cpp")
endforeach()
foreach(source IN LISTS GENERATED)
    if(NOT EXISTS "${source}")
        message(FATAL_ERROR "Missing generated game source ${source}; run tools/build_switch.py")
    endif()
endforeach()
add_library(shaolin_guest STATIC ${GENERATED})
target_include_directories(shaolin_guest PRIVATE "${SHAOLIN_ADAPTED}/include" "${SHAOLIN_SDK}/runtime" "${SHAOLIN_GENERATED}")
target_compile_options(shaolin_guest PRIVATE "${SHAOLIN_GENERATED_OPT}" -fno-strict-aliasing -fwrapv -ffp-contract=off -w)

# A separate target permits ARM64 code generation checks before the host's
# Vulkan/audio/platform integration is linked. This archive is not a game NRO.
add_library(shaolin_switch_core STATIC
    "${SHAOLIN_ADAPTED}/dispatch.cpp" "${SHAOLIN_ADAPTED}/memory.cpp" "${SHAOLIN_ADAPTED}/threads.cpp"
    "${ROOT}/src/switch/guest_memory.cpp" "${ROOT}/src/switch/audio_output.cpp")
target_include_directories(shaolin_switch_core PRIVATE "${ROOT}/src" "${SHAOLIN_ADAPTED}/include" "${SHAOLIN_SDK}/runtime" "${SHAOLIN_GENERATED}")
target_compile_options(shaolin_switch_core PRIVATE -O2 -fno-strict-aliasing -fwrapv)

option(SHAOLIN_BUILD_NRO "Link the experimental game NRO" OFF)
set(SHAOLIN_GAME_XBE "" CACHE FILEPATH "External original XBE matching the translation")
set(SHAOLIN_ICON "" CACHE FILEPATH "External JPEG icon for the NRO")
if(SHAOLIN_BUILD_NRO)
    if(NOT EXISTS "${SHAOLIN_MESA}/lib/libvulkan.a" OR NOT EXISTS "${SHAOLIN_GAME_XBE}")
        message(FATAL_ERROR "Set SHAOLIN_MESA and SHAOLIN_GAME_XBE for the NRO")
    endif()
    function(add_switch_glslang)
        # libnx supplies POSIX threads; upstream glslang does not recognize
        # NintendoSwitch as a platform when selecting its POSIX OS adapter.
        set(UNIX TRUE)
        # Generated PCH includes contain MSYS paths which native devkitA64
        # cannot resolve inside a header. Ordinary compiler arguments convert.
        set(CMAKE_DISABLE_PRECOMPILE_HEADERS ON)
        set(GLSLANG_TESTS OFF CACHE BOOL "" FORCE)
        set(GLSLANG_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
        set(ENABLE_GLSLANG_BINARIES OFF CACHE BOOL "" FORCE)
        set(ENABLE_SPVREMAPPER OFF CACHE BOOL "" FORCE)
        set(ENABLE_OPT OFF CACHE BOOL "" FORCE)
        set(ENABLE_HLSL ON CACHE BOOL "" FORCE)
        set(BUILD_EXTERNAL OFF CACHE BOOL "" FORCE)
        add_subdirectory("${ROOT}/third_party/glslang" glslang EXCLUDE_FROM_ALL)
    endfunction()
    add_switch_glslang()
    set(IMGUI "${ROOT}/third_party/imgui")
    set(RUNTIME "${SHAOLIN_SDK}/runtime")
    file(SHA1 "${SHAOLIN_GAME_XBE}" IMAGE_SHA1)
    file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/image_hash.cpp" CONTENT
        "extern const char kImageSha1[] = \"${IMAGE_SHA1}\";\n")
    file(GLOB_RECURSE BUILD_INPUTS CONFIGURE_DEPENDS "${ROOT}/src/switch/*" "${SHAOLIN_ADAPTED}/*.cpp"
        "${SHAOLIN_ADAPTED}/include/*.h" "${ROOT}/src/linux/vk/*")
    list(APPEND BUILD_INPUTS "${CMAKE_CURRENT_LIST_FILE}" "${ROOT}/tools/prepare_switch_runtime.py"
        "${ROOT}/tools/prepare_recomp_runtime.py" "${ROOT}/config/game.toml" "${SHAOLIN_MESA}/manifest.json")
    if(SHAOLIN_ICON)
        if(NOT EXISTS "${SHAOLIN_ICON}")
            message(FATAL_ERROR "SHAOLIN_ICON does not exist")
        endif()
        list(APPEND BUILD_INPUTS "${SHAOLIN_ICON}")
    endif()
    set(BUILD_HASHES "${IMAGE_SHA1}")
    foreach(input IN LISTS BUILD_INPUTS)
        file(SHA256 "${input}" input_hash)
        string(APPEND BUILD_HASHES "${input_hash}")
    endforeach()
    string(SHA256 BUILD_HASH "${BUILD_HASHES}")
    string(SUBSTRING "${BUILD_HASH}" 0 16 BUILD_ID)
    file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/build_id.txt" CONTENT "${BUILD_ID}\n")
    file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/build_info.cpp" CONTENT
        "extern const char kSwitchBuildId[] = \"${BUILD_ID}\";\n")
    add_executable(ShaolinMonksRecomp
        "${ROOT}/src/switch/main.cpp" "${ROOT}/src/switch/input.cpp" "${ROOT}/src/switch/vulkan_platform.cpp"
        "${ROOT}/src/switch/posix_io.cpp"
        "${ROOT}/src/switch/thread_detach.cpp"
        "${ROOT}/src/raster_compat.cpp" "${ROOT}/src/linux/vk/vk_shader.cpp" "${ROOT}/src/linux/vk/bc_decode.cpp"
        "${SHAOLIN_ADAPTED}/vk_api.cpp" "${SHAOLIN_ADAPTED}/vk_backend.cpp"
        "${SHAOLIN_ADAPTED}/render_thread.cpp" "${SHAOLIN_ADAPTED}/boot.cpp"
        "${SHAOLIN_ADAPTED}/d3d8.cpp" "${SHAOLIN_ADAPTED}/xapi.cpp" "${SHAOLIN_ADAPTED}/dsound.cpp"
        "${SHAOLIN_ADAPTED}/vertex.cpp" "${SHAOLIN_ADAPTED}/shaders.cpp"
        "${RUNTIME}/core/log.cpp" "${RUNTIME}/gpu/texture.cpp"
        "${SHAOLIN_ADAPTED}/files.cpp" "${SHAOLIN_ADAPTED}/disc.cpp" "${RUNTIME}/kernel/crypto.cpp"
        "${RUNTIME}/kernel/objects.cpp" "${RUNTIME}/kernel/ordinals.cpp" "${RUNTIME}/kernel/rtl.cpp"
        "${SHAOLIN_ADAPTED}/xboxkrnl.cpp" "${RUNTIME}/xdk/crt_math.cpp"
        "${IMGUI}/imgui.cpp" "${IMGUI}/imgui_draw.cpp" "${IMGUI}/imgui_tables.cpp" "${IMGUI}/imgui_widgets.cpp"
        "${IMGUI}/backends/imgui_impl_vulkan.cpp" "${CMAKE_CURRENT_BINARY_DIR}/image_hash.cpp"
        "${CMAKE_CURRENT_BINARY_DIR}/build_info.cpp")
    target_include_directories(ShaolinMonksRecomp PRIVATE "${ROOT}/src" "${ROOT}/src/linux/compat"
        "${SHAOLIN_ADAPTED}/include" "${RUNTIME}" "${SHAOLIN_GENERATED}" "${IMGUI}" "${SHAOLIN_MESA}/include")
    target_compile_definitions(ShaolinMonksRecomp PRIVATE VK_NO_PROTOTYPES VK_USE_PLATFORM_VI_NN
        IMGUI_IMPL_VULKAN_NO_PROTOTYPES)
    target_compile_options(ShaolinMonksRecomp PRIVATE -O2 -fno-strict-aliasing -fwrapv)
    # Static registration objects and strong HLE overrides must survive archive
    # extraction regardless of which weak guest function the linker sees first.
    target_link_libraries(ShaolinMonksRecomp PRIVATE -Wl,--start-group shaolin_switch_core shaolin_guest
        glslang::glslang glslang::glslang-default-resource-limits
        "${SHAOLIN_MESA}/lib/libvulkan.a" "${SHAOLIN_MESA}/lib/libexpat.a"
        "${SHAOLIN_MESA}/lib/libzstd.a" "${SHAOLIN_MESA}/lib/libz.a" nx m -Wl,--end-group)
    target_link_options(ShaolinMonksRecomp PRIVATE "-Wl,-Map,${CMAKE_CURRENT_BINARY_DIR}/ShaolinMonksRecomp.map")
    nx_generate_nacp(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ShaolinMonksRecomp.nacp"
        NAME "Shaolin Monks Recomp" AUTHOR "ArdRecomp" VERSION "0.2.0-arm64")
    set(NRO_ICON_ARGS "")
    if(SHAOLIN_ICON)
        list(APPEND NRO_ICON_ARGS ICON "${SHAOLIN_ICON}")
    endif()
    nx_create_nro(ShaolinMonksRecomp NACP "${CMAKE_CURRENT_BINARY_DIR}/ShaolinMonksRecomp.nacp" ${NRO_ICON_ARGS})
endif()
