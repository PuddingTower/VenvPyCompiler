# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import ast
import time
import multiprocessing
import platform  # 用于 freeze_support 和潜在的平台特定逻辑
from pathlib import Path # 使用 pathlib 更方便地处理路径

# 尝试导入 tomllib (Python 3.11+) 或回退到 toml
try:
    import tomllib
except ImportError:
    try:
        # 将 'toml' 库作为 'tomllib' 使用
        import toml as tomllib
        print("[信息] 正在使用 'toml' 库解析配置 (适用于 Python < 3.11)。请确保已安装 (`pip install toml`)。")
    except ImportError:
        print("[错误] 未找到 TOML 解析库。请安装 (`pip install toml`) 或使用 Python 3.11+。")
        sys.exit(1)

# --- 配置常量 ---
# !! 重要：请在此处设置你的 UPX 可执行文件的完整路径 !!
# 示例:
# UPX_DIR = r"D:\upx-4.2.4-win64\upx.exe" # Windows 示例
# UPX_DIR = r"C:\Program Files\upx\upx.exe" # Windows 默认安装路径示例
UPX_DIR = r"/usr/local/bin/upx"         # Linux/Mac 示例 (如果安装在这里)
# 如果不想使用 UPX，设置 UPX_DIR = None
# UPX_DIR = None

# 默认设置 (可在 .pack.toml 中覆盖)
DEFAULT_PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}" # 使用当前Python版本进行标准库检查
DEFAULT_PACK_MODE = "onefile"  # 'onefile' (单文件) 或 'onedir' (单目录)
DEFAULT_LOG_LEVEL = "INFO"     # PyInstaller 日志级别 (DEBUG, INFO, WARN, ERROR)
VENV_NAME = "packager_venv_cn" # 虚拟环境目录名

# --- 全局变量 ---
script_start_time = time.time() # 记录脚本开始时间
# 确定项目根目录 (脚本所在目录)
project_root_dir = Path(__file__).parent if getattr(sys, 'frozen', False) is False else Path(sys.executable).parent

# --- 辅助函数 ---
def print_debug(message, level="调试"):
    """打印带时间戳的格式化调试/信息消息。"""
    elapsed_time = time.time() - script_start_time
    print(f"[{level} {elapsed_time:.2f}秒] {message}")

def get_python_executable(venv_dir):
    """获取虚拟环境内的 python 可执行文件路径。"""
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    else:
        return venv_dir / "bin" / "python"

def get_pip_executable(venv_dir):
    """获取虚拟环境内的 pip 可执行文件路径。"""
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "pip.exe"
    else:
        return venv_dir / "bin" / "pip"

def run_subprocess(cmd_list, check=True, timeout=None, capture=True, cwd=None, extra_env=None):
    """辅助运行子进程，捕获输出，并提供更好的调试信息。"""
    cmd_str = ' '.join(map(str, cmd_list))
    print_debug(f"执行命令: {cmd_str}", level="命令")
    try:
        process = subprocess.run(
            cmd_list,
            check=check,            # 如果为 True，返回码非0时抛出异常
            capture_output=capture, # 是否捕获标准输出和标准错误
            text=True,              # 使用文本模式（自动解码）
            encoding='utf-8',       # 指定编码
            errors='ignore',        # 忽略解码错误
            timeout=timeout,        # 命令超时时间（秒）
            cwd=cwd,                # 命令执行的工作目录
            env={**os.environ, **(extra_env or {})}, # 合并环境变量
        )
        if capture:
            # 打印输出的最后几行以供参考
            output_lines = process.stdout.strip().splitlines() if process.stdout else []
            if output_lines:
                print_debug(f"命令标准输出 (最后几行):\n..." + "\n...".join(output_lines[-5:]), level="命令输出")
            if process.stderr:
                 print_debug(f"命令标准错误:\n{process.stderr.strip()}", level="命令错误")

        return process
    except subprocess.CalledProcessError as e:
        print_debug(f"命令执行失败，返回码 {e.returncode}", level="错误")
        if capture:
            print_debug(f"失败命令的标准输出:\n{e.stdout}", level="错误")
            print_debug(f"失败命令的标准错误:\n{e.stderr}", level="错误")
        if check:
            raise # 如果 check=True，重新抛出异常
        return None # 如果 check=False 且执行失败，返回 None
    except subprocess.TimeoutExpired as e:
        print_debug(f"命令执行超时 ({timeout} 秒)。", level="错误")
        if capture:
            print_debug(f"超时命令的标准输出:\n{e.stdout}", level="错误")
            print_debug(f"超时命令的标准错误:\n{e.stderr}", level="错误")
        if check:
            raise
        return None
    except Exception as e:
        print_debug(f"运行子进程时发生意外错误: {e}", level="严重错误")
        raise


# --- 配置加载 ---
def load_pack_config(py_file_path):
    """加载与 .py 文件关联的 .pack.toml 配置文件。"""
    config_file = py_file_path.with_suffix(".pack.toml")
    # 默认配置结构
    default_config = {
        "packaging": {"mode": DEFAULT_PACK_MODE, "log_level": DEFAULT_LOG_LEVEL},
        "dependencies": {"pip_install": []},
        "pyinstaller": {
            "hidden_imports": [],
            "add_data": [],
            "add_binary": [],
            "exclude_modules": [],
            "extra_args": [],
        },
    }
    if config_file.is_file():
        print_debug(f"加载配置文件: {config_file.name}")
        try:
            with open(config_file, "rb") as f: # 以二进制模式读取以供 tomllib 处理
                loaded_config = tomllib.load(f)

            # 将加载的配置与默认配置合并（简单合并，列表会被覆盖）
            # 更健壮的合并可能需要递归更新字典和扩展列表
            merged_config = default_config.copy()
            for section, settings in loaded_config.items():
                if section in merged_config and isinstance(merged_config[section], dict):
                    merged_config[section].update(settings)
                else:
                     merged_config[section] = settings # 添加或覆盖整个部分
            return merged_config
        except tomllib.TOMLDecodeError as e:
            print_debug(f"解析 TOML 文件 {config_file.name} 时出错: {e}", level="警告")
            return default_config # 解析错误时返回默认配置
        except Exception as e:
            print_debug(f"读取配置文件 {config_file.name} 时出错: {e}", level="警告")
            return default_config
    else:
        print_debug(f"未找到 {py_file_path.name} 的配置文件，使用默认设置。")
        return default_config

# --- 核心功能函数 ---
def create_virtualenv(venv_dir):
    """创建或验证虚拟环境。"""
    step_start_time = time.time()
    print_debug(f"确保虚拟环境存在: {venv_dir}", level="信息")
    if not venv_dir.exists():
        print_debug(f"正在创建虚拟环境...")
        run_subprocess([sys.executable, "-m", "venv", str(venv_dir)])
        print_debug(f"虚拟环境已创建。")
    else:
        print_debug(f"使用现有虚拟环境。")
    step_elapsed = time.time() - step_start_time
    print_debug(f"虚拟环境设置完成，耗时 {step_elapsed:.2f} 秒", level="信息")

def install_core_dependencies(venv_dir):
    """在虚拟环境中安装核心打包器依赖 (PyInstaller, stdlib_list)。"""
    step_start_time = time.time()
    print_debug("安装核心打包器依赖 (PyInstaller, stdlib_list)...", level="信息")
    pip_exe = get_pip_executable(venv_dir)

    # 首先升级 pip
    print_debug("正在升级 pip...")
    run_subprocess([str(pip_exe), "install", "--upgrade", "pip"], check=False) # pip 升级失败不应中断脚本

    # 安装核心包
    print_debug("正在安装 pyinstaller, stdlib_list...")
    # 注意：如果此脚本自身需要 stdlib_list 但在全局环境运行，这里可能找不到。
    # 最好是从创建好的虚拟环境中运行此打包脚本，或者全局安装 stdlib_list。
    # 为简单起见，假设 stdlib_list 可访问。
    run_subprocess([str(pip_exe), "install", "pyinstaller", "stdlib_list"])

    step_elapsed = time.time() - step_start_time
    print_debug(f"核心依赖安装完成，耗时 {step_elapsed:.2f} 秒", level="信息")


def get_script_dependencies(py_file_path):
    """使用 AST 分析 Python 脚本以查找静态导入。"""
    print_debug(f"正在 AST 分析导入: {py_file_path.name}")
    modules = set()
    try:
        source = py_file_path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(py_file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # 获取导入路径的第一部分作为潜在的包名
                    modules.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                # 只处理绝对导入 (level=0)，并确保 module 存在
                if node.level == 0 and node.module:
                    modules.add(node.module.split('.')[0])
        print_debug(f"AST 找到模块: {modules if modules else '{}'}")
        return modules
    except FileNotFoundError:
        print_debug(f"文件未找到: {py_file_path}", level="警告")
        return set()
    except SyntaxError as e:
        print_debug(f"{py_file_path.name} 中存在语法错误: {e}", level="错误")
        return set() # 发生错误时返回空集合
    except Exception as e:
        print_debug(f"分析 {py_file_path.name} 时出错: {e}", level="错误")
        return set()

def install_project_dependencies(venv_dir, all_modules_to_install):
    """在虚拟环境中安装所有收集到的项目依赖。"""
    if not all_modules_to_install:
        print_debug("没有需要安装的项目特定依赖。", level="信息")
        return

    step_start_time = time.time()
    # 过滤掉可能的空字符串或None
    valid_modules = {m for m in all_modules_to_install if m}
    if not valid_modules:
        print_debug("过滤后没有有效的项目依赖项需要安装。", level="信息")
        return

    print_debug(f"正在安装项目依赖: {', '.join(valid_modules)}", level="信息")
    pip_exe = get_pip_executable(venv_dir)

    # 尝试一次性安装所有依赖，可能更快，但出错不易定位
    cmd = [str(pip_exe), "install"] + list(valid_modules)
    process = run_subprocess(cmd, check=False) # 先不检查退出码

    if process is None or process.returncode != 0:
         print_debug(f"批量安装依赖失败。将尝试逐一安装...", level="警告")
         success_count = 0
         failed_modules = []
         for module in valid_modules:
             try:
                 print_debug(f"尝试单独安装: {module}")
                 run_subprocess([str(pip_exe), "install", module], check=True)
                 success_count += 1
             except Exception as inner_e:
                 print_debug(f"单独安装依赖失败: {module}。错误: {inner_e}", level="错误")
                 failed_modules.append(module)

         step_elapsed = time.time() - step_start_time
         print_debug(f"逐一安装尝试完成。成功: {success_count}/{len(valid_modules)}。耗时: {step_elapsed:.2f} 秒", level="信息")
         if failed_modules:
              print_debug(f"以下依赖未能安装: {', '.join(failed_modules)}。后续打包可能会失败。", level="警告")
         # 根据需求决定是否在这里中断脚本
         # if failed_modules: sys.exit(1)
    else:
        step_elapsed = time.time() - step_start_time
        print_debug(f"项目依赖批量安装完成，耗时 {step_elapsed:.2f} 秒", level="信息")


    # 可选：列出虚拟环境中已安装的包
    print_debug("列出虚拟环境中的已安装包:")
    run_subprocess([str(pip_exe), "list"], check=False)


def package_py_to_exe(venv_dir, py_file_path, output_dir, config):
    """使用 PyInstaller 打包单个 Python 文件，并应用配置。"""
    step_start_time = time.time()
    script_name = py_file_path.name
    print_debug(f"开始为 {script_name} 进行打包", level="信息")

    # 定位 PyInstaller 可执行文件
    if platform.system() == "Windows":
        pyinstaller_exe = get_python_executable(venv_dir).parent / "pyinstaller.exe"
    else:
        pyinstaller_exe = get_python_executable(venv_dir).parent / "pyinstaller"

    # --- 构建 PyInstaller 参数列表 ---
    pyinstaller_args = [str(pyinstaller_exe)]

    # 打包模式 (onefile/onedir)
    pack_mode = config.get("packaging", {}).get("mode", DEFAULT_PACK_MODE)
    pyinstaller_args.append(f"--{pack_mode}")

    # UPX 压缩
    upx_path = UPX_DIR # 从全局常量获取
    if upx_path and Path(upx_path).exists():
        # PyInstaller 需要的是 UPX 所在的 *目录*
        # 如果 UPX_DIR 直接指向了 exe 文件，取其父目录
        upx_dir_path = Path(upx_path)
        if upx_dir_path.is_file():
             upx_dir_path = upx_dir_path.parent
        pyinstaller_args.extend(["--upx-dir", str(upx_dir_path)])
        print_debug(f"UPX 已启用，路径: {upx_dir_path}")
    elif upx_path:
        print_debug(f"UPX 路径 '{upx_path}' 无效或不存在，UPX 已禁用。", level="警告")
    else:
        print_debug("UPX 未配置 (UPX_DIR 为 None)，UPX 已禁用。", level="信息")


    # 隐藏导入
    for imp in config.get("pyinstaller", {}).get("hidden_imports", []):
        pyinstaller_args.extend(["--hidden-import", imp])

    # 添加数据文件
    for data_spec in config.get("pyinstaller", {}).get("add_data", []):
        # PyInstaller 需要的格式是 "源路径:目标路径" (Windows上用';',其他系统用':')
        separator = ';' if platform.system() == "Windows" else ':'
        # 确保我们使用正确的路径分隔符
        formatted_spec = data_spec.replace(':', separator)
        pyinstaller_args.extend(["--add-data", formatted_spec])

    # 添加二进制文件
    for binary_spec in config.get("pyinstaller", {}).get("add_binary", []):
        separator = ';' if platform.system() == "Windows" else ':'
        formatted_spec = binary_spec.replace(':', separator)
        pyinstaller_args.extend(["--add-binary", formatted_spec])

    # 排除模块 (谨慎使用!)
    for mod in config.get("pyinstaller", {}).get("exclude_modules", []):
        pyinstaller_args.extend(["--exclude-module", mod])

    # 基础参数
    pyinstaller_args.extend([
        "--distpath", str(output_dir), # 输出目录
        "--noconfirm",                 # 不经确认覆盖输出
        "--clean",                     # 构建前清理缓存
    ])

    # 日志级别
    log_level = config.get("packaging", {}).get("log_level", DEFAULT_LOG_LEVEL)
    pyinstaller_args.extend([f"--log-level={log_level.upper()}"])

    # 来自配置文件的额外参数
    pyinstaller_args.extend(config.get("pyinstaller", {}).get("extra_args", []))

    # 需要打包的脚本文件路径 (必须是最后一个参数)
    pyinstaller_args.append(str(py_file_path))
    # --- 参数构建结束 ---


    print_debug(f"准备为 {script_name} 执行 PyInstaller...", level="信息")
    # 打印将要执行的命令，对带空格的参数加上引号，方便复制调试
    cmd_str = ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in pyinstaller_args)
    print(f"[命令 {time.time() - script_start_time:.2f}秒] {cmd_str}")

    print("\n" + f"--- {script_name} 的 PyInstaller 输出 ---")
    return_code = 1 # 默认为失败状态
    try:
        # 使用 Popen 实时获取和打印输出
        process = subprocess.Popen(
            pyinstaller_args,
            stdout=subprocess.PIPE,       # 捕获标准输出
            stderr=subprocess.STDOUT,      # 将标准错误重定向到标准输出
            text=True,                   # 使用文本模式
            encoding='utf-8',            # 指定编码
            errors='ignore',             # 忽略解码错误
            bufsize=1,                   # 行缓冲模式
            cwd=project_root_dir,        # 在项目根目录执行 PyInstaller
            # shell=False is default and recommended
        )

        # 逐行读取并打印 PyInstaller 的输出
        while True:
            line = process.stdout.readline()
            if not line: # 当输出结束时 readline 返回空字符串
                break
            current_elapsed = time.time() - script_start_time
            # 可以在这里添加过滤逻辑，去除不关心的日志行
            # if "some noisy pattern" not in line:
            print(f"[{current_elapsed:.2f}秒 PyI] {line.strip()}") # PyI = PyInstaller 输出标记

        process.wait() # 等待子进程结束
        return_code = process.returncode # 获取最终的返回码
        print(f"--- {script_name} 的 PyInstaller 执行完毕 (返回码: {return_code}) ---")

    except FileNotFoundError:
        print_debug(f"严重错误：未在以下路径找到 PyInstaller 可执行文件: {pyinstaller_exe}", level="严重错误")
        raise # 抛出严重错误
    except Exception as e:
        print_debug(f"在为 {script_name} 执行 PyInstaller 时发生意外错误: {e}", level="严重错误")
        # 可选：在此处记录完整的 traceback
        raise # 重新抛出未预料到的错误

    step_elapsed = time.time() - step_start_time
    if return_code == 0:
        print_debug(f"成功打包 {script_name}，耗时 {step_elapsed:.2f} 秒", level="成功")
        return True
    else:
        print_debug(f"打包 {script_name} 失败，耗时 {step_elapsed:.2f} 秒", level="错误")
        return False


# --- 并行执行包装器 ---
def package_task_wrapper(args):
    """用于在多进程池中运行 package_py_to_exe 的包装函数。"""
    venv_dir, py_file_path, output_dir, config, global_start_time = args
    # 注意：全局开始时间无法简单地跨进程共享给 print_debug 使用。
    # 子进程中的 print_debug 计时将相对于子进程的启动时间。
    # 这里主要关心的是任务成功与否的返回值。
    process_id = os.getpid()
    script_name = py_file_path.name
    print(f"[进程 {process_id}] 开始处理任务: {script_name}")
    start = time.time()
    try:
        # 调用实际的打包函数
        success = package_py_to_exe(venv_dir, py_file_path, output_dir, config)
        end = time.time()
        print(f"[进程 {process_id}] 完成任务: {script_name}，耗时 {end-start:.2f} 秒。成功: {success}")
        return success # 返回布尔值表示成功或失败
    except Exception as e:
        end = time.time()
        import traceback
        # 打印详细错误信息，包括堆栈跟踪
        print(f"[进程 {process_id}] 严重错误：处理 {script_name} 任务时发生异常，耗时 {end-start:.2f} 秒: {e}\n{traceback.format_exc()}")
        return False # 确保报告失败

# --- 主程序 ---
def main():
    print_debug(f"打包脚本启动。根目录: {project_root_dir}", level="信息")
    venv_dir = project_root_dir / VENV_NAME      # 虚拟环境目录路径
    output_dir = project_root_dir / "dist_packaged" # 单独的输出目录

    # 确保输出目录存在
    output_dir.mkdir(exist_ok=True)
    print_debug(f"输出目录: {output_dir}", level="信息")

    try:
        # === 阶段 1: 分析脚本和加载配置 ===
        print_debug("--- 阶段 1：分析脚本并加载配置 ---", level="阶段")
        scripts_to_package = [] # 存储找到的 .py 文件 Path 对象
        all_configs = {}        # 存储每个脚本的配置字典 {Path: config_dict}
        ast_discovered_modules = set() # 存储所有脚本通过 AST 分析找到的模块

        # 查找当前目录下的 .py 文件，排除脚本自身
        self_script_name = Path(__file__).name
        for item in project_root_dir.iterdir():
            # 必须是文件、以 .py 结尾、且不是本脚本自身
            if item.is_file() and item.suffix == ".py" and item.name != self_script_name:
                scripts_to_package.append(item)
                # 加载或获取默认配置
                config = load_pack_config(item)
                all_configs[item] = config
                # 分析脚本依赖
                ast_discovered_modules.update(get_script_dependencies(item))

        if not scripts_to_package:
            print_debug("未在本目录找到需要打包的 Python 脚本 (已排除打包脚本自身)。退出。", level="警告")
            return

        print_debug(f"找到 {len(scripts_to_package)} 个待打包脚本: {[p.name for p in scripts_to_package]}", level="信息")

        # === 阶段 2: 设置虚拟环境和核心依赖 ===
        print_debug("--- 阶段 2：设置环境 ---", level="阶段")
        create_virtualenv(venv_dir)
        install_core_dependencies(venv_dir) # 安装 PyInstaller 等

        # === 阶段 3: 安装合并的项目依赖 ===
        print_debug("--- 阶段 3：安装合并的项目依赖 ---", level="阶段")
        # 合并 AST 发现的模块和所有配置文件中手动指定的 pip 依赖
        manual_pip_installs = set()
        for config in all_configs.values():
            manual_pip_installs.update(config.get("dependencies", {}).get("pip_install", []))

        # 确定需要安装的第三方依赖（过滤掉标准库）
        try:
            # 尝试从虚拟环境中导入 stdlib_list (它应该在 Phase 2 中被安装)
            # 这需要确保 Python 能找到 venv 中的包路径
            # 一个简单的方法是修改 sys.path，或者更推荐从 venv 运行此脚本
            sys.path.insert(0, str(venv_dir / ('Lib' if platform.system() == 'Windows' else f'lib/python{DEFAULT_PYTHON_VERSION}') / 'site-packages'))
            from stdlib_list import stdlib_list as sl_list
            standard_libs = set(sl_list(DEFAULT_PYTHON_VERSION))
            print_debug(f"已加载 Python {DEFAULT_PYTHON_VERSION} 的标准库列表")
            sys.path.pop(0) # 恢复 sys.path
        except ImportError:
            print_debug("无法导入 stdlib_list。标准库检查将被跳过。", level="警告")
            standard_libs = set() # 无法检查时，保守地认为所有模块都不是标准库
        except Exception as e:
            print_debug(f"加载标准库列表时出错: {e}", level="警告")
            standard_libs = set()

        modules_to_install = set()
        # 合并 AST 找到的模块和手动指定的模块
        combined_discovered = ast_discovered_modules | manual_pip_installs
        for module_or_specifier in combined_discovered:
            # 做一些基本判断来过滤掉标准库
            # 注意：这个判断逻辑可以进一步完善，例如处理带版本号的 specifier
            module_base = module_or_specifier.split('==')[0].split('<')[0].split('>')[0].split('[')[0].split('.')[0].strip()
            if module_base and module_base not in standard_libs:
                 modules_to_install.add(module_or_specifier) # 添加原始 specifier 或模块名


        # 安装所有收集到的依赖
        install_project_dependencies(venv_dir, modules_to_install)

        # === 阶段 4: 并行打包 ===
        print_debug("--- 阶段 4：开始并行打包 ---", level="阶段")
        # 设置工作进程数，可以调整，例如留一个核心给系统
        num_workers = max(1, os.cpu_count() - 1) if os.cpu_count() else 1
        print_debug(f"将使用最多 {num_workers} 个工作进程进行打包。")

        # 准备传递给每个打包任务的参数列表
        tasks = []
        for script_path in scripts_to_package:
            # 每个任务是一个元组，包含所有需要的信息
            tasks.append((venv_dir, script_path, output_dir, all_configs[script_path], script_start_time))

        results = [] # 存储每个任务的返回结果 (True/False)
        try:
            # 创建进程池并执行任务
            # map 会阻塞，直到所有任务完成
            with multiprocessing.Pool(processes=num_workers) as pool:
                results = pool.map(package_task_wrapper, tasks)
        except Exception as pool_error:
             print_debug(f"多进程池执行期间发生错误: {pool_error}", level="严重错误")
             # 可能需要更健壮的清理或报告机制

        # === 阶段 5: 报告结果 ===
        print_debug("--- 阶段 5：打包完成 ---", level="阶段")
        successful_packs = sum(1 for r in results if r is True)
        failed_packs = len(results) - successful_packs
        print_debug(f"处理的总脚本数: {len(scripts_to_package)}", level="信息")
        print_debug(f"成功打包数量: {successful_packs}", level="成功" if failed_packs == 0 else "信息")
        if failed_packs > 0:
            print_debug(f"失败打包数量: {failed_packs}", level="错误")
            # 可以考虑列出失败的脚本名称，需要稍微修改结果跟踪方式

    except Exception as e:
        # 捕获主流程中的其他意外错误
        print_debug(f"主执行流程中发生意外错误: {e}", level="严重错误")
        import traceback
        print("------ 错误追踪 ------")
        traceback.print_exc()
        print("-----------------------")
        sys.exit(1) # 异常退出
    finally:
        # 无论成功或失败，都打印总耗时
        total_elapsed = time.time() - script_start_time
        print_debug(f"脚本执行完毕。总耗时: {total_elapsed:.2f} 秒。", level="信息")


if __name__ == "__main__":
    # 在 Windows 上使用 multiprocessing 打包成 exe 时需要此调用
    multiprocessing.freeze_support()
    main()
