import os
import subprocess
import sys
import ast
from stdlib_list import stdlib_list

if getattr(sys, 'frozen', False):
    current_dir = os.path.dirname(sys.executable)
else:
    current_dir = os.path.dirname(os.path.abspath(__file__))

def get_pip_path(venv_name):
    return os.path.join(current_dir, venv_name, 'Scripts', 'pip.exe') if os.name == 'nt' else os.path.join(current_dir, venv_name, 'bin', 'pip')

def create_virtualenv(venv_name):
    subprocess.run([sys.executable, "-m", "venv", os.path.join(current_dir, venv_name)], check=True)

def install_pyinstaller(venv_name):
    pip_path = get_pip_path(venv_name)
    subprocess.run([pip_path, "install", "pyinstaller", "stdlib_list"], check=True)

def get_imported_modules(py_file):
    with open(py_file, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read(), filename=py_file)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                modules.add(n.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split('.')[0])
    return modules

def is_standard_module(module_name):
    stdlib_modules = stdlib_list("3.12")  # 请替换为您的Python版本
    return module_name in stdlib_modules

module_name_mapping = {
    'docx': 'python-docx',
    'PIL': 'Pillow',
    'sklearn': 'scikit-learn',
    # 添加更多需要特殊处理的模块
}

def is_conda_environment():
    return 'CONDA_PREFIX' in os.environ

def get_package_manager():
    if is_conda_environment():
        return 'conda'
    else:
        return get_pip_path(venv_name)

def install_modules(venv_name, modules):
    package_manager = get_package_manager()
    for module in modules:
        if not is_standard_module(module):
            print(f"Installing module: {module}")
            module_install_name = module_name_mapping.get(module, module)
            if package_manager == 'conda':
                subprocess.run(['conda', "install", "-y", module_install_name], check=True)
            else:
                subprocess.run([package_manager, "install", module_install_name], check=True)

def package_py_to_exe(venv_name, py_file, output_dir):
    pyinstaller_path = os.path.join(current_dir, venv_name, 'Scripts', 'pyinstaller.exe') if os.name == 'nt' else os.path.join(current_dir, venv_name, 'bin', 'pyinstaller')
    result = subprocess.run([
        pyinstaller_path,
        "--onefile",
        "--distpath", output_dir,
        py_file
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error packaging {py_file}:")
        print(result.stderr)
    else:
        print(f"Successfully packaged {py_file}")
        print(result.stdout)

def main():
    global venv_name  # 添加global声明
    venv_name = "venv"
    print("Creating virtual environment...")
    create_virtualenv(venv_name)
    print("Installing PyInstaller and stdlib_list in virtual environment...")
    install_pyinstaller(venv_name)
    
    py_files = [f for f in os.listdir(current_dir) if f.endswith('.py') and f != os.path.basename(__file__)]
    for py_file in py_files:
        print(f"\nProcessing {py_file}...")
        py_file_path = os.path.join(current_dir, py_file)
        modules = get_imported_modules(py_file_path)
        print(f"Detected modules: {modules}")
        install_modules(venv_name, modules)
        print(f"Packaging {py_file} into executable...")
        package_py_to_exe(venv_name, py_file_path, current_dir)
        print(f"Executable for {py_file} has been created.\n")

if __name__ == "__main__":
    main()
