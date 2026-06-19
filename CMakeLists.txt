cmake_minimum_required(VERSION 3.20)
project(xbot2_diagnostics VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

#
include(cmake/ExportProject.cmake)

#
find_package(cppzmq REQUIRED)
find_package(diagnostic_msgs QUIET)

# ── header-only interface library ──────────────────────────────────────────
add_library(xbot2_diagnostics INTERFACE)
target_include_directories(xbot2_diagnostics INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
target_link_libraries(xbot2_diagnostics INTERFACE
    cppzmq
)
install(TARGETS xbot2_diagnostics
    EXPORT ${PROJECT_NAME}Targets
    DESTINATION lib
)


# ros library
if(${diagnostic_msgs_FOUND})
    message(STATUS "diagnostic_msgs found, building ROS2 publisher")
    add_library(xbot2_diagnostics_ros INTERFACE)
    target_include_directories(xbot2_diagnostics_ros INTERFACE
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    )
    target_link_libraries(xbot2_diagnostics_ros INTERFACE
        diagnostic_msgs::diagnostic_msgs__rosidl_typesupport_cpp
    )
    install(TARGETS xbot2_diagnostics_ros
        EXPORT ${PROJECT_NAME}Targets
        DESTINATION lib
    )
endif()

# export 
export_project()

# install
install(
    DIRECTORY include/ DESTINATION include
)

install(
    DIRECTORY config launch
    DESTINATION share/${PROJECT_NAME}
)

# python package
add_subdirectory(python)

# ── tests ──────────────────────────────────────────────────────────────────
include(CTest)
if(BUILD_TESTING)
    find_package(GTest REQUIRED)
    find_package(nlohmann_json REQUIRED)

    # tests for stats_accumulator
    add_executable(test_stats_accumulator
        test/test_stats_accumulator.cpp
    )
    target_link_libraries(test_stats_accumulator PRIVATE
        xbot2_diagnostics
    nlohmann_json::nlohmann_json
        GTest::GTest
        GTest::Main
    )
    add_test(NAME test_stats_accumulator COMMAND test_stats_accumulator)

    # tests for publisher
    add_executable(test_publisher
        test/test_publisher.cpp
    )
    target_link_libraries(test_publisher PRIVATE
        xbot2_diagnostics
        nlohmann_json::nlohmann_json
        GTest::GTest
        GTest::Main
    )
    add_test(NAME test_publisher COMMAND test_publisher)

    # ── profiling executable ───────────────────────────────────────────────────
    add_executable(profile_stats_accumulator
        src/profile_stats_accumulator.cpp
    )
    target_link_libraries(profile_stats_accumulator PRIVATE xbot2_diagnostics nlohmann_json::nlohmann_json)
    target_compile_options(profile_stats_accumulator PRIVATE -O3 -DNDEBUG)

    add_executable(profile_publish
        src/profile_publish.cpp
    )
    target_link_libraries(profile_publish PRIVATE xbot2_diagnostics nlohmann_json::nlohmann_json)
    target_compile_options(profile_publish PRIVATE -O3 -DNDEBUG)

endif()
