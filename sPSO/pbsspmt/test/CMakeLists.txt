# Enable testing for this directory
enable_testing()

find_package(secureJoin REQUIRED)
# secureJoin's Config.cmake forgets to include Targets, so IMPORTED target
# visa::secureJoin is not visible here. Pull it in manually.
include("${CMAKE_CURRENT_LIST_DIR}/../../external/install/lib/cmake/secureJoin/secureJoinTargets.cmake")

# ---- Build test executables ----
add_executable(test_oprf_gf128 test_galois128.cpp)
add_executable(test_oprf_okvs_basic test_okvs_basic.cpp)
add_executable(test_oprf test_oprf.cpp)
add_executable(test_cuckoo3 test_cuckoo3.cpp)
add_executable(test_workflow test_flow.cpp common.cpp)
add_executable(test_psi test_psi.cpp common.cpp)
add_executable(test_psu test_psu.cpp common.cpp)
add_executable(test_card test_card.cpp common.cpp)
add_executable(test_psum test_psum.cpp common.cpp)
add_executable(test_ss_psi test_ss_psi.cpp common.cpp)

# ---- Link libraries ----
target_link_libraries(test_oprf_gf128 PRIVATE pbsspmt)
target_link_libraries(test_oprf_okvs_basic PRIVATE pbsspmt)
target_link_libraries(test_oprf PRIVATE pbsspmt)
target_link_libraries(test_cuckoo3 PRIVATE pbsspmt)
target_link_libraries(test_workflow PRIVATE pbsspmt visa::secureJoin)
target_link_libraries(test_psi PRIVATE pbsspmt visa::secureJoin)
target_link_libraries(test_psu PRIVATE pbsspmt visa::secureJoin)
target_link_libraries(test_card PRIVATE pbsspmt visa::secureJoin)
target_link_libraries(test_psum PRIVATE pbsspmt visa::secureJoin)
target_link_libraries(test_ss_psi PRIVATE pbsspmt visa::secureJoin)

# ---- Register tests ----
add_test(NAME oprf_gf128_test COMMAND test_oprf_gf128)
add_test(NAME oprf_okvs_basic_test COMMAND test_oprf_okvs_basic)
add_test(NAME oprf_test COMMAND test_oprf)
add_test(NAME cuckoo3_test COMMAND test_cuckoo3)
add_test(NAME workflow_test COMMAND test_workflow)
add_test(NAME psi_test COMMAND test_psi)
add_test(NAME psu_test COMMAND test_psu)
add_test(NAME psi_card_test COMMAND test_card)
add_test(NAME psi_sum_test    COMMAND test_psum)
add_test(NAME psi_ss_psi_test COMMAND test_ss_psi)

