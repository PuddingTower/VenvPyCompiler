import os
import subprocess
import sys
import ast
import platform
from stdlib_list import stdlib_list

# 获取当前Python版本
PY_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"

if getattr(sys, 'frozen', False):
    current_dir = os.path.dirname(sys.executable)
    python_path = os.path.join(sys._MEIPASS, 'python.exe')  # 打包后路径
else:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = sys.executable

def get_pip_path(venv_name):
    pip_path = os.path.join(current_dir, venv_name, 'Scripts', 'pip.exe') if os.name == 'nt' else os.path.join(current_dir, venv_name, 'bin', 'pip')
    if not os.path.exists(pip_path):
        raise FileNotFoundError(f"Pip not found at {pip_path}")
    return pip_path

def create_virtualenv(venv_name):
    try:
        subprocess.run([python_path, "-m", "venv", os.path.join(current_dir, venv_name)], 
                      check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"创建虚拟环境失败: {e.stderr}")
        sys.exit(1)

def install_pyinstaller(venv_name):
    try:
        pip_path = get_pip_path(venv_name)
        subprocess.run([pip_path, "install", "-U", "pyinstaller", "stdlib_list"],
                      check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"安装PyInstaller失败: {e.stderr}")
        sys.exit(1)

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
    return module_name in stdlib_list(PY_VERSION)  # 动态获取版本 [[0]](#__0)

module_name_mapping = {
    'docx': 'python-docx',
    'PIL': 'Pillow',
    'sklearn': 'scikit-learn',
    'cv2': 'opencv-python',
    'bs4': 'beautifulsoup4',      # 新增
    'yaml': 'PyYAML',             # 新增
    'MySQLdb': 'mysqlclient',     # 新增
    'dateutil': 'python-dateutil' # 新增
}

def get_installed_packages(pip_path):
    try:
        result = subprocess.run([pip_path, "list", "--format=freeze"],
                               capture_output=True, text=True, check=True)
        return [line.split('==')[0].lower() for line in result.stdout.splitlines()]
    except subprocess.CalledProcessError:
        return []

def install_modules(venv_name, modules):
    pip_path = get_pip_path(venv_name)
    installed = get_installed_packages(pip_path)
    
    for module in modules:
        if is_standard_module(module):
            continue
            
        module_install_name = module_name_mapping.get(module, module)
        if module_install_name.lower() in installed:
            print(f"{module_install_name} 已安装，跳过")
            continue

        print(f"正在安装: {module_install_name}")
        try:
            subprocess.run([pip_path, "install", module_install_name],
                          check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"安装失败: {e.stderr}")
            sys.exit(1)

def package_py_to_exe(venv_name, py_file, output_dir):
    pyinstaller_path = os.path.join(current_dir, venv_name, 'Scripts', 'pyinstaller.exe') if os.name == 'nt' else os.path.join(current_dir, venv_name, 'bin', 'pyinstaller')
    
    command = [
        pyinstaller_path,
        "--onefile",
        "--clean",
        "--upx-dir=/usr/local/bin/upx",  # 需配置UPX路径
        "--exclude-module=tests",
        "--distpath", output_dir
    ]
    
    # 非GUI程序隐藏控制台
    if not py_file.endswith("_gui.py"):
        command.append("--noconsole")
    
    command.append(py_file)
    
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
        print(f"打包成功: {os.path.basename(py_file)}")
        print("输出大小:", 
              round(os.path.getsize(os.path.join(output_dir, os.path.basename(py_file)[:-3]+('.exe' if os.name=='nt' else '')))/1024/1024, 2), "MB")
    except subprocess.CalledProcessError as e:
        print(f"打包失败: {e.stderr}")
        sys.exit(1)

def main():
    venv_name = "auto_package_env"  # 更明确的虚拟环境名称
    try:
        print("创建虚拟环境...")
        create_virtualenv(venv_name)
        print("安装基础依赖...")
        install_pyinstaller(venv_name)
        
        py_files = [f for f in os.listdir(current_dir) 
                   if f.endswith('.py') and f != os.path.basename(__file__)]
        
        for py_file in py_files:
            py_path = os.path.join(current_dir, py_file)
            print(f"\n处理文件: {py_file}")
            modules = get_imported_modules(py_path)
            print("检测到依赖:", modules)
            install_modules(venv_name, modules)
            package_py_to_exe(venv_name, py_path, current_dir)
            
    except Exception as e:
        print(f"发生未捕获异常: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
