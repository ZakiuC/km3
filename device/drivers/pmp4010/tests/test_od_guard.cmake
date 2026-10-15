if(NOT PMP4010_CXX_COMPILER OR NOT PMP4010_REPO_ROOT)
  message(FATAL_ERROR "Compiler and repository root are required")
endif()

set(compile_args
  -std=c++17 -fsyntax-only
  "-I${PMP4010_REPO_ROOT}/device/include"
  "-I${PMP4010_REPO_ROOT}/common/include"
  "${PMP4010_REPO_ROOT}/device/drivers/pmp4010/driver/pmp4010_driver.cpp"
)

execute_process(
  COMMAND "${PMP4010_CXX_COMPILER}" ${compile_args}
  RESULT_VARIABLE guard_result
  OUTPUT_VARIABLE guard_output
  ERROR_VARIABLE guard_error
  TIMEOUT 20
)
if("${guard_result}" STREQUAL "0")
  message(FATAL_ERROR "Production driver compiled with an incomplete OD")
endif()
string(FIND "${guard_output}${guard_error}" "对象字典未验证" guard_message)
if(guard_message EQUAL -1)
  message(FATAL_ERROR "Compile failed for a different reason: ${guard_result}\n${guard_error}")
endif()

execute_process(
  COMMAND "${PMP4010_CXX_COMPILER}" -DKM3_ALLOW_UNVERIFIED_OD=1 ${compile_args}
  RESULT_VARIABLE mock_result
  OUTPUT_VARIABLE mock_output
  ERROR_VARIABLE mock_error
  TIMEOUT 20
)
if(NOT "${mock_result}" STREQUAL "0")
  message(FATAL_ERROR "Mock driver did not compile: ${mock_result}\n${mock_output}${mock_error}")
endif()
message(STATUS "Production OD guard rejected compilation; isolated mock compilation succeeded")

execute_process(
  COMMAND "${PMP4010_CXX_COMPILER}" -DKM3_ALLOW_UNVERIFIED_OD=0 ${compile_args}
  RESULT_VARIABLE zero_result
  OUTPUT_VARIABLE zero_output
  ERROR_VARIABLE zero_error
  TIMEOUT 20
)
if("${zero_result}" STREQUAL "0")
  message(FATAL_ERROR "A zero-valued OD bypass unexpectedly permitted compilation")
endif()
string(FIND "${zero_output}${zero_error}" "对象字典未验证" zero_message)
if(zero_message EQUAL -1)
  message(FATAL_ERROR "Zero-valued bypass failed for a different reason: ${zero_result}\n${zero_error}")
endif()
