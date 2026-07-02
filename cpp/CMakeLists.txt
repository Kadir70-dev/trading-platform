cmake_minimum_required(VERSION 3.16)
project(execution_gateway CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O2 -Wall -Wextra")

add_executable(execution_gateway
    execution_engine/main.cpp
)

target_include_directories(execution_gateway PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/spread_monitor
    ${CMAKE_CURRENT_SOURCE_DIR}/latency_monitor
    ${CMAKE_CURRENT_SOURCE_DIR}/order_router
    ${CMAKE_CURRENT_SOURCE_DIR}/position_manager
)

find_package(Threads REQUIRED)
target_link_libraries(execution_gateway PRIVATE Threads::Threads)

install(TARGETS execution_gateway RUNTIME DESTINATION bin)
