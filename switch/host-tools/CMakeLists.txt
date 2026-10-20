cmake_minimum_required(VERSION 3.25)
project(ShaolinSwitchTools LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(SHAOLIN_SDK "" CACHE PATH "Pinned XboxXBERecomp checkout")
set(SHAOLIN_ZYDIS "" CACHE PATH "Zydis v4.1.1 checkout")
set(SHAOLIN_TOML "" CACHE PATH "toml++ v3.4.0 checkout")
foreach(dep SHAOLIN_SDK SHAOLIN_ZYDIS SHAOLIN_TOML)
    if(NOT IS_DIRECTORY "${${dep}}")
        message(FATAL_ERROR "Missing ${dep}")
    endif()
endforeach()
set(ZYDIS_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(ZYDIS_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(ZYDIS_BUILD_DOXYGEN OFF CACHE BOOL "" FORCE)
add_subdirectory("${SHAOLIN_ZYDIS}" zydis EXCLUDE_FROM_ALL)
add_subdirectory("${SHAOLIN_TOML}" toml EXCLUDE_FROM_ALL)
file(GLOB RECOMP_SOURCES CONFIGURE_DEPENDS "${SHAOLIN_SDK}/tools/recomp/*.cpp")
add_executable(xbe_recomp ${RECOMP_SOURCES})
target_include_directories(xbe_recomp PRIVATE "${SHAOLIN_SDK}/runtime" "${SHAOLIN_SDK}/tools/recomp")
target_link_libraries(xbe_recomp PRIVATE Zydis tomlplusplus::tomlplusplus)
if(CYGWIN OR MSYS)
    # Cygwin defines TOML_WINDOWS but its libstdc++ streams use POSIX paths.
    file(COPY "${SHAOLIN_TOML}/include/toml++" DESTINATION "${CMAKE_BINARY_DIR}/compat")
    set(parser "${CMAKE_BINARY_DIR}/compat/toml++/impl/parser.inl")
    file(READ "${parser}" parser_text)
    string(REPLACE "#if TOML_WINDOWS\n\t\tfile.open" "#if TOML_WINDOWS && !defined(__CYGWIN__)\n\t\tfile.open" parser_text "${parser_text}")
    file(WRITE "${parser}" "${parser_text}")
    target_include_directories(xbe_recomp BEFORE PRIVATE "${CMAKE_BINARY_DIR}/compat")
endif()
set(SHAOLIN_TEST_PYTHON "" CACHE FILEPATH "Host Python with Unicorn and pyelftools")
set(SHAOLIN_ARM64_COMPILER "" CACHE FILEPATH "devkitA64 C++ compiler")
set(SHAOLIN_FIXTURE_XBE "" CACHE FILEPATH "Optional audited external XBE fixture")
if(SHAOLIN_TEST_PYTHON AND SHAOLIN_ARM64_COMPILER)
    enable_testing()
    add_test(NAME switch_memory_abi COMMAND "${SHAOLIN_TEST_PYTHON}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_memory_abi_test.py" --compiler "${SHAOLIN_ARM64_COMPILER}")
    if(SHAOLIN_FIXTURE_XBE)
        add_test(NAME switch_movie_kernel COMMAND "${SHAOLIN_TEST_PYTHON}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_movie_kernel_test.py"
            --compiler "${SHAOLIN_ARM64_COMPILER}" --xbe "${SHAOLIN_FIXTURE_XBE}")
    endif()
    add_executable(switch_posix_io_test "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_posix_io_test.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../src/switch/posix_io.cpp")
    target_include_directories(switch_posix_io_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
    target_compile_options(switch_posix_io_test PRIVATE -UNDEBUG)
    find_package(Threads REQUIRED)
    target_link_libraries(switch_posix_io_test PRIVATE Threads::Threads)
    add_test(NAME switch_posix_io COMMAND switch_posix_io_test)
    add_executable(switch_spirv_cache_test "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_spirv_cache_test.cpp")
    target_include_directories(switch_spirv_cache_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
    target_compile_options(switch_spirv_cache_test PRIVATE -UNDEBUG)
    add_test(NAME switch_spirv_cache COMMAND switch_spirv_cache_test)
    if(UNIX OR CYGWIN OR MSYS)
        add_executable(switch_file_io_test
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_file_io_test.cpp"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../src/switch/posix_io.cpp")
        target_include_directories(switch_file_io_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
        target_compile_options(switch_file_io_test PRIVATE -UNDEBUG -fno-builtin-fread -fno-builtin-fwrite)
        target_link_options(switch_file_io_test PRIVATE -Wl,--wrap=fread -Wl,--wrap=fwrite -Wl,--wrap=read)
        target_link_libraries(switch_file_io_test PRIVATE Threads::Threads)
        add_test(NAME switch_file_io COMMAND switch_file_io_test)
        add_executable(switch_guest_memory_test
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_guest_memory_test.cpp"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../src/switch/guest_memory.cpp")
        target_include_directories(switch_guest_memory_test BEFORE PRIVATE
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_mocks"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
        target_compile_options(switch_guest_memory_test PRIVATE -UNDEBUG)
        target_link_options(switch_guest_memory_test PRIVATE -Wl,--wrap=memalign -Wl,--wrap=free)
        target_link_libraries(switch_guest_memory_test PRIVATE Threads::Threads)
        if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../../build/switch/adapted/memory.cpp")
            add_executable(switch_files_metadata_test
                "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_files_metadata_test.cpp"
                "${CMAKE_CURRENT_SOURCE_DIR}/../../src/switch/posix_io.cpp")
            target_include_directories(switch_files_metadata_test BEFORE PRIVATE
                "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_mocks"
                "${CMAKE_CURRENT_SOURCE_DIR}/../../src" "${SHAOLIN_SDK}/runtime")
            target_compile_definitions(switch_files_metadata_test PRIVATE
                SHAOLIN_SWITCH_FILES_TEST_SOURCE="${CMAKE_CURRENT_SOURCE_DIR}/../../build/switch/adapted/files.cpp")
            target_compile_options(switch_files_metadata_test PRIVATE -UNDEBUG)
            target_link_options(switch_files_metadata_test PRIVATE -static-libstdc++
                -Wl,--wrap=stat -Wl,--wrap=fstat -Wl,--wrap=fopen -Wl,--wrap=fclose -Wl,--wrap=ftruncate)
            target_link_libraries(switch_files_metadata_test PRIVATE Threads::Threads)
            add_test(NAME switch_files_metadata COMMAND switch_files_metadata_test)
            add_executable(switch_memory_failure_test
                "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_memory_failure_test.cpp"
                "${CMAKE_CURRENT_SOURCE_DIR}/../../build/switch/adapted/memory.cpp")
            target_include_directories(switch_memory_failure_test BEFORE PRIVATE
                "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/switch_mocks"
                "${CMAKE_CURRENT_SOURCE_DIR}/../../src"
                "${SHAOLIN_SDK}/runtime")
            target_compile_options(switch_memory_failure_test PRIVATE -UNDEBUG)
            target_link_libraries(switch_memory_failure_test PRIVATE Threads::Threads)
            add_test(NAME switch_memory_failure COMMAND switch_memory_failure_test)
        endif()
        foreach(scenario image sparse overlap retry-map retry-permission boundary mirrors
                reserve-map reserve-permission reserve-mirror reserve-query hints)
            add_test(NAME "switch_guest_memory_${scenario}" COMMAND switch_guest_memory_test "${scenario}")
        endforeach()
    endif()
endif()
