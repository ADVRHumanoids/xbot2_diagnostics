option(INSTALL_PYTHON_PKG_EDITABLE "Install the Python package in editable mode" ON)

if(INSTALL_PYTHON_PKG_EDITABLE)
    set(PIP_INSTALL_ARGS "-e")
endif()

add_custom_target(install_python_pkg ALL
    COMMAND python -m pip show py${PROJECT_NAME} || python -m pip install ${PIP_INSTALL_ARGS} ${CMAKE_CURRENT_SOURCE_DIR}
    COMMENT "Installing Python package in editable mode"
)