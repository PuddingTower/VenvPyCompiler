# VenvPyCompiler

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

把 Python 脚本扔进去，自动建虚拟环境、装依赖、用 PyInstaller 打包成可执行文件。

一个 `main.py`，没有第三方依赖（Python 3.11+ 内置 `tomllib`），拷到哪儿都能跑。

## 它做什么

1. 扫描指定目录 / 文件里的 `.py`（可递归），跳过 `.venv`、`build`、`__pycache__` 之类
2. 用 AST 分析 `import`，结合导入名→包名映射，推断出真正要装的第三方包
3. 建一个干净虚拟环境（有 `uv` 用 `uv`，没有就用 `python -m venv`）
4. 装 PyInstaller 和项目依赖，并行调用 PyInstaller 打包
5. 产物落在 `dist_packaged/`，每个脚本一个

## 快速开始

```bash
# 打包当前目录下所有脚本
python main.py

# 只打一个，指定 Python 版本和输出目录
python main.py app.py --python 3.12 -o dist

# 打包整个子目录，单目录模式（启动快）
python main.py src/ --mode onedir

# 先看看它准备干什么
python main.py --dry-run

# 给脚本生成配置文件模板
python main.py app.py --init
```

单文件，不用安装。把 `main.py` 放到项目里，或者加到 `PATH` 起个别名：

```bash
# ~/.bashrc / PowerShell profile
alias venvpy='python /path/to/main.py'
```

## 环境要求

| 项目 | 说明 |
| --- | --- |
| Python | 3.11+（要 `tomllib`） |
| uv | 可选。装了就用，装包快很多；没装退回 `pip` |
| UPX | 可选。默认在 `PATH` 里找 `upx`，找到就启用压缩 |
| 网络 | 首次建环境要能装 PyInstaller 和项目依赖 |

## 常用参数

```
python main.py [目标...] [选项]
```

| 参数 | 作用 |
| --- | --- |
| `目标` | 文件、目录或 `*.py` 通配符，默认当前目录 |
| `-o, --output` | 输出目录，默认 `dist_packaged` |
| `--venv` | 虚拟环境目录，默认 `.venvpy` |
| `--python 3.12` | 建环境用的 Python 版本（uv 后端） |
| `--backend auto\|uv\|pip` | 装包后端，默认 `auto` |
| `--mode onefile\|onedir` | 单文件 / 单目录，默认 `onefile` |
| `--windowed` | GUI 程序，不弹控制台 |
| `--icon app.ico` | 图标 |
| `--jobs N` | 同时打包几个，默认按 CPU |
| `--upx-dir PATH` / `--no-upx` | UPX 目录 / 直接关掉 |
| `--pip 包名` | 手动补依赖，可重复 |
| `--requirements 文件` | 额外 requirements，可重复 |
| `--hidden-import` `--add-data` `--add-binary` `--collect-all` `--extra-arg` | 原样透传给 PyInstaller |
| `--exclude 模式` | 跳过匹配的文件/目录，可重复 |
| `--init` | 生成 `<脚本>.pack.toml` 后退出 |
| `--dry-run` | 只打印计划 |
| `--recreate` / `--clean` | 重建环境 / 连环境带产物一起删 |
| `--skip-deps` | 只装 PyInstaller，不装项目依赖 |
| `-v` / `-q` | 详细日志 / 只看警告 |

完整列表见 `python main.py --help`。

## 配置文件

两层，命令行优先级最高：

```
venvpy.toml           # 项目级，放在项目根目录（只给一个目录参数时就是那个目录）
<脚本名>.pack.toml     # 只对同名脚本生效，放在脚本旁边
```

`python main.py app.py --init` 会按脚本实际导入生成模板：

```toml
[packaging]
mode = "onefile"          # onefile | onedir
# name = "MyApp"          # 输出名，默认脚本名
# icon = "app.ico"
windowed = false
# upx_dir = "C:/tools/upx"   # 留空 = 在 PATH 里找；填了无效路径会警告并禁用
# python = "3.12"
# backend = "auto"

[dependencies]
pip_install = [
    "requests",
]
# requirements = ["requirements-dev.txt"]

[pyinstaller]
hidden_imports = []
# add_data 两种写法都行，相对路径先找脚本目录，再找当前目录
# add_data = ["config.json:.", { src = "assets", dest = "assets" }]
add_binary = []
exclude_modules = []
collect_all = []
extra_pyinstaller_args = []
```

## 依赖是怎么推断的

对每个脚本做 AST 分析，拿到顶层导入名，然后：

- 命中标准库（`sys.stdlib_module_names`）→ 跳过
- 命中项目里的本地模块（同目录的 `foo.py`、带 `__init__.py` 的包）→ 跳过
- 命中内置映射表（`cv2`→`opencv-python`、`PIL`→`Pillow`、`sklearn`→`scikit-learn` 等）→ 用映射后的包名
- 其余用当前解释器已装包的 `importlib.metadata.packages_distributions()` 反查
- 最后叠加 `pip_install`、`requirements.txt`、项目 `pyproject.toml` 的 `[project].dependencies`

推断不出来的情况（纯动态导入、`importlib.import_module("xxx")` 之类）用 `--pip` 或 `hidden_imports` 补。

## 和 1.x 的区别

- 不再需要改代码里的 `UPX_DIR`，命令行或配置指定，默认自动找
- 不再是“只能打当前目录”，可以指定文件、目录、通配符，支持递归
- 去掉 `stdlib_list` 依赖，改用内置标准库名单
- 导入名→包名映射，`cv2`、`PIL` 这类不会再装错或装不上
- 支持 `uv`，建环境和装依赖快一个数量级
- `--init` / `--dry-run` / `--clean` / `--skip-deps` 等日常会用到的开关
- 修复 `add_data` 在 Windows 上把盘符 `C:` 也替换掉的 bug
- 并发打包用线程池 + 独立 `workpath`，日志不再互相穿插

## License

MIT
