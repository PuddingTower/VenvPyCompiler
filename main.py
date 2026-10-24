#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""venvpy — 把 Python 脚本装进干净的虚拟环境并打包成独立可执行文件。

	 python main.py                        # 打包当前目录下所有 .py
	 python main.py app.py tool/           # 指定文件 / 目录
	 python main.py src -o dist --mode onedir --python 3.12
	 python main.py --init                 # 生成 <脚本>.pack.toml 模板
	 python main.py --dry-run              # 只打印计划，不执行

需要 Python 3.11+（内置 tomllib），没有别的第三方依赖。
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
	import tomllib
except ModuleNotFoundError:  # pragma: no cover
	sys.exit("[错误] 需要 Python 3.11 或更高版本（内置 tomllib 解析配置）。")


__version__ = "2.0.0"
PROG = "venvpy"

if not sys.stdout.isatty():  # 管道/重定向时统一成 utf-8，免得中文日志乱码
	try:
		sys.stdout.reconfigure(encoding="utf-8", errors="replace")
		sys.stderr.reconfigure(encoding="utf-8", errors="replace")
	except (AttributeError, OSError):
		pass

DEFAULT_VENV = ".venvpy"
DEFAULT_DIST = "dist_packaged"
BUILD_DIR = ".venvpy-build"

SKIP_DIRS = {
	".git", ".hg", ".svn", "__pycache__", "node_modules", "site-packages",
	"build", "dist", "dist_packaged", "venv", "env", ".venv", ".venvpy", BUILD_DIR,
}

# 导入名 ≠ PyPI 包名的常见情况。其余靠当前解释器里已装的包反查。
IMPORT_TO_PACKAGE = {
	"cv2": "opencv-python",
	"PIL": "Pillow",
	"yaml": "PyYAML",
	"bs4": "beautifulsoup4",
	"sklearn": "scikit-learn",
	"skimage": "scikit-image",
	"attr": "attrs",
	"dateutil": "python-dateutil",
	"dotenv": "python-dotenv",
	"jwt": "PyJWT",
	"jose": "python-jose",
	"nacl": "PyNaCl",
	"Crypto": "pycryptodome",
	"OpenSSL": "pyOpenSSL",
	"serial": "pyserial",
	"usb": "pyusb",
	"win32api": "pywin32",
	"win32con": "pywin32",
	"win32gui": "pywin32",
	"win32com": "pywin32",
	"pythoncom": "pywin32",
	"pkg_resources": "setuptools",
	"google.protobuf": "protobuf",
	"docx": "python-docx",
	"pptx": "python-pptx",
	"fitz": "PyMuPDF",
	"magic": "python-magic",
	"slugify": "python-slugify",
	"multipart": "python-multipart",
	"socketio": "python-socketio",
	"engineio": "python-engineio",
	"telegram": "python-telegram-bot",
	"Levenshtein": "python-Levenshtein",
	"gi": "PyGObject",
	"wx": "wxPython",
	"ffmpeg": "ffmpeg-python",
	"grpc": "grpcio",
	"zmq": "pyzmq",
	"MySQLdb": "mysqlclient",
	"psycopg2": "psycopg2-binary",
	"pymysql": "PyMySQL",
	"dns": "dnspython",
	"websocket": "websocket-client",
	"snappy": "python-snappy",
	"ldap": "python-ldap",
	"git": "GitPython",
	"babel": "Babel",
	"markdown": "Markdown",
	"ruamel": "ruamel.yaml",
}

LOG_LEVEL = "INFO"
START_TIME = time.time()


def log(message: str, level: str = "info") -> None:
	if level == "debug" and LOG_LEVEL != "DEBUG":
		return
	if level == "info" and LOG_LEVEL == "QUIET":
		return
	if LOG_LEVEL == "QUIET" and level not in ("warn", "error"):
		return
	tag = {"debug": "调试", "info": "信息", "warn": "警告", "error": "错误"}.get(level, level)
	print(f"[{tag} {time.time() - START_TIME:6.2f}s] {message}", flush=True)


class ToolError(RuntimeError):
	pass


# ---------------------------------------------------------------- 子进程

def _child_env(extra: dict | None) -> dict:
	env = os.environ.copy()
	env.setdefault("PYTHONIOENCODING", "utf-8")
	env.setdefault("PYTHONUTF8", "1")
	env.pop("PYTHONHOME", None)
	if extra:
		env.update(extra)
	return env


def run(cmd: list, cwd: Path | None = None, check: bool = True, extra_env: dict | None = None):
	log("$ " + " ".join(shlex.quote(str(c)) for c in cmd), "debug")
	try:
		proc = subprocess.run(
			[str(c) for c in cmd],
			cwd=str(cwd) if cwd else None,
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
			env=_child_env(extra_env),
		)
	except FileNotFoundError as exc:
		raise ToolError(f"找不到可执行文件: {cmd[0]}") from exc
	if proc.returncode != 0 and check:
		detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
		raise ToolError(f"命令失败（退出码 {proc.returncode}）: {cmd[0]}\n{detail[-3000:]}")
	return proc


def run_stream(cmd: list, cwd: Path | None = None) -> tuple[int, list[str]]:
	"""边跑边收集输出，供打包时打印日志。"""
	log("$ " + " ".join(shlex.quote(str(c)) for c in cmd), "debug")
	try:
		proc = subprocess.Popen(
			[str(c) for c in cmd],
			cwd=str(cwd) if cwd else None,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			encoding="utf-8",
			errors="replace",
			bufsize=1,
			env=_child_env(None),
		)
	except FileNotFoundError as exc:
		raise ToolError(f"找不到可执行文件: {cmd[0]}") from exc
	lines: list[str] = []
	assert proc.stdout is not None
	for line in proc.stdout:
		lines.append(line.rstrip("\r\n"))
	proc.wait()
	return proc.returncode, lines


# ---------------------------------------------------------------- 配置

def _default_config() -> dict:
	return {
		"packaging": {
			"mode": "onefile",
			"name": "",
			"icon": "",
			"log_level": "INFO",
			"windowed": False,
			"upx_dir": "",
			"python": "",
			"backend": "auto",
			"venv": DEFAULT_VENV,
			"dist": DEFAULT_DIST,
			"jobs": 0,
			"extra_pyinstaller_args": [],
		},
		"dependencies": {
			"pip_install": [],
			"requirements": [],
		},
		"pyinstaller": {
			"hidden_imports": [],
			"add_data": [],
			"add_binary": [],
			"exclude_modules": [],
			"collect_all": [],
		},
	}


def _deep_merge(base: dict, override: dict) -> dict:
	merged = {}
	for key, value in base.items():
		if key in override:
			new_value = override[key]
			if isinstance(value, dict) and isinstance(new_value, dict):
				merged[key] = _deep_merge(value, new_value)
			else:
				merged[key] = new_value
		else:
			merged[key] = value
	for key, value in override.items():
		if key not in merged:
			merged[key] = value
	return merged


def read_toml(path: Path) -> dict | None:
	if not path.is_file():
		return None
	try:
		with open(path, "rb") as fh:
			return tomllib.load(fh)
	except tomllib.TOMLDecodeError as exc:
		log(f"配置文件 {path.name} 解析失败，已忽略: {exc}", "warn")
		return None
	except OSError as exc:
		log(f"配置文件 {path.name} 读取失败，已忽略: {exc}", "warn")
		return None


def resolve_config(script: Path, root: Path, cli: dict) -> dict:
	"""默认值 <- 根目录 venvpy.toml <- <脚本>.pack.toml <- 命令行。"""
	cfg = _default_config()
	for path in (root / "venvpy.toml", script.with_suffix(".pack.toml")):
		data = read_toml(path)
		if data:
			log(f"加载配置: {path}", "debug")
			cfg = _deep_merge(cfg, data)
	return _deep_merge(cfg, cli)


def config_template(script: Path, deps: list[str]) -> str:
	dep_lines = "".join(f'\t"{d}",\n' for d in deps)
	if dep_lines:
		dep_body = f"pip_install = [\n{dep_lines}]"
	else:
		dep_body = "pip_install = []"
	return f"""# {script.name} 的 venvpy 配置（命令行参数优先级更高）

[packaging]
mode = "onefile"          # onefile | onedir
# name = "MyApp"          # 输出名字，默认取脚本名
# icon = "app.ico"
windowed = false          # 带 GUI、不想弹黑框时改成 true
# upx_dir = "C:/tools/upx"  # 留空 = 自动在 PATH 里找 upx
# python = "3.12"         # 建环境用的 Python 版本
# backend = "auto"        # auto | uv | pip

[dependencies]
{dep_body}

[pyinstaller]
hidden_imports = []
# add_data = ["config.json:.", {{ src = "assets", dest = "assets" }}]
add_binary = []
exclude_modules = []
collect_all = []          # 等价于 --collect-all
"""


# ---------------------------------------------------------------- 发现脚本

def pick_config_root(targets: list[str]) -> Path:
	"""项目配置（venvpy.toml / requirements / pyproject）去哪里找。

	只给了一个目录就把它当项目根，否则用当前目录。
	"""
	dirs = [
		Path(t).resolve() for t in targets
		if not any(ch in t for ch in "*?[") and Path(t).is_dir()
	]
	return dirs[0] if len(dirs) == 1 else Path.cwd().resolve()


def _is_self(path: Path) -> bool:
	try:
		if path.resolve() == Path(__file__).resolve():
			return True
	except OSError:
		pass
	if getattr(sys, "frozen", False):
		try:
			return path.resolve() == Path(sys.executable).resolve()
		except OSError:
			pass
	return False


def _excluded(path: Path, root: Path, patterns: list[str]) -> bool:
	rel = path.relative_to(root).as_posix() if root in path.parents else path.as_posix()
	return any(
		fnmatch.fnmatch(path.name, p) or fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(str(path), p)
		for p in patterns
	)


def discover(targets: list[str], excludes: list[str], skip_paths: set[Path]) -> list[Path]:
	files: list[Path] = []
	seen: set[Path] = set()

	def add(path: Path) -> None:
		resolved = path.resolve()
		if resolved in seen or _is_self(path):
			return
		if any(resolved == s or s in resolved.parents for s in skip_paths):
			return
		seen.add(resolved)
		files.append(path)

	for raw in targets:
		pattern_hit = any(ch in raw for ch in "*?[")
		candidates: list[Path] = []
		if pattern_hit:
			candidates = sorted(Path().glob(raw))
		else:
			candidates = [Path(raw)]

		for candidate in candidates:
			if candidate.is_dir():
				root = candidate.resolve()
				for current, dirs, names in os.walk(candidate):
					cur = Path(current)
					dirs[:] = sorted(
						d for d in dirs
						if d not in SKIP_DIRS and not d.startswith(".") and not _excluded(cur / d, root, excludes)
					)
					for name in sorted(names):
						if name.endswith(".py") and not _excluded(cur / name, root, excludes):
							add(cur / name)
			elif candidate.is_file():
				if candidate.suffix != ".py":
					raise ToolError(f"不是 Python 文件: {candidate}")
				if not _excluded(candidate, candidate.parent, excludes):
					add(candidate)
			elif pattern_hit:
				continue
			else:
				raise ToolError(f"路径不存在: {candidate}")

	return sorted(files)


# ---------------------------------------------------------------- 依赖推断

def _stdlib_names() -> set[str]:
	return set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _installed_module_map() -> dict[str, str]:
	"""用当前解释器已装的包，反查 导入名 -> 发行包名。"""
	try:
		from importlib.metadata import packages_distributions

		return {mod: dists[0] for mod, dists in packages_distributions().items() if dists}
	except Exception:
		return {}


def scan_imports(files: list[Path]) -> set[str]:
	modules: set[str] = set()
	for path in files:
		try:
			source = path.read_text(encoding="utf-8", errors="replace")
			tree = ast.parse(source, filename=str(path))
		except SyntaxError as exc:
			log(f"{path.name} 有语法错误，跳过依赖分析: {exc}", "warn")
			continue
		except OSError as exc:
			log(f"{path.name} 读取失败，跳过依赖分析: {exc}", "warn")
			continue
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				modules.update(alias.name.split(".")[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
				modules.add(node.module.split(".")[0])
	log(f"AST 扫描到导入: {', '.join(sorted(modules)) or '无'}", "debug")
	return modules


def local_module_names(files: list[Path]) -> set[str]:
	names = {path.stem for path in files}
	for path in files:
		if path.stem == "__init__":
			names.add(path.parent.name)
		# 只打包单个文件时，同目录的兄弟模块也算本地模块
		try:
			for sibling in path.parent.glob("*.py"):
				names.add(sibling.stem)
			for child in path.parent.iterdir():
				if child.is_dir() and (child / "__init__.py").is_file():
					names.add(child.name)
		except OSError:
			continue
	names.discard("__init__")
	return names


def _project_dependencies(root: Path) -> list[str]:
	pyproject = root / "pyproject.toml"
	data = read_toml(pyproject)
	if not data:
		return []
	deps = data.get("project", {}).get("dependencies", [])
	return [str(d) for d in deps]


def _requirement_files(root: Path, configured: list[str]) -> list[Path]:
	found: list[Path] = []
	for raw in configured:
		path = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
		if not path.is_file():
			raise ToolError(f"requirements 文件不存在: {raw}")
		found.append(path)
	auto = root / "requirements.txt"
	if auto.is_file() and auto.resolve() not in found:
		found.append(auto.resolve())
	return found


def infer_dependencies(
	files: list[Path], cfg: dict, root: Path, siblings: list[Path] | None = None
) -> tuple[list[str], list[Path]]:
	"""返回 (要 pip install 的包列表, requirements 文件列表)。

	files 是本次要分析导入的脚本，siblings 是整个项目里能找到的脚本，
	用来识别「自己项目内的模块」，避免把 utils 当成要 pip 安装的包。
	"""
	stdlib = _stdlib_names()
	locals_ = local_module_names(siblings if siblings is not None else files)
	installed = _installed_module_map()

	packages: list[str] = []
	seen: set[str] = set()

	def package_key(spec: str) -> str:
		name = spec.strip()
		for char in "<>=!~[;":
			name = name.split(char)[0]
		return name.strip().lower()

	def add_package(spec: str) -> None:
		key = package_key(spec)
		if key and key not in seen:
			seen.add(key)
			packages.append(spec.strip())

	for spec in cfg["dependencies"].get("pip_install", []):
		if isinstance(spec, str) and spec.strip():
			add_package(spec)

	for module in sorted(scan_imports(files)):
		if module in stdlib or module in locals_:
			continue
		package = IMPORT_TO_PACKAGE.get(module) or installed.get(module) or module
		add_package(package)

	for dep in _project_dependencies(root):
		add_package(dep)

	return packages, _requirement_files(root, cfg["dependencies"].get("requirements", []))


# ---------------------------------------------------------------- 环境

def find_uv() -> str | None:
	return shutil.which("uv")


def venv_python(venv_dir: Path) -> Path:
	return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv(venv_dir: Path, python_spec: str, backend: str, recreate: bool) -> Path:
	python_exe = venv_python(venv_dir)
	if recreate and venv_dir.exists():
		log(f"删除旧虚拟环境: {venv_dir}", "warn")
		shutil.rmtree(venv_dir)
	if python_exe.is_file():
		log(f"复用虚拟环境: {venv_dir}")
		return python_exe
	if venv_dir.exists():
		if not (venv_dir / "pyvenv.cfg").is_file():
			raise ToolError(f"{venv_dir} 已存在但不是虚拟环境，换个 --venv 路径或先手动清理。")
		shutil.rmtree(venv_dir)
		log(f"重建损坏的虚拟环境: {venv_dir}", "warn")

	uv = find_uv()
	use_uv = backend == "uv" or (backend == "auto" and uv)
	if backend == "uv" and not uv:
		raise ToolError("指定了 --backend uv，但 PATH 里找不到 uv。")

	log(f"创建虚拟环境: {venv_dir}（{'uv' if use_uv else 'venv'}）")
	if use_uv:
		cmd = [uv, "venv", str(venv_dir)]
		if python_spec:
			cmd += ["--python", python_spec]
		run(cmd)
	else:
		current = f"{sys.version_info.major}.{sys.version_info.minor}"
		if python_spec and not current.startswith(python_spec):
			log(f"pip 后端只能用当前解释器 {current}，忽略 --python {python_spec}", "warn")
		run([sys.executable, "-m", "venv", str(venv_dir)])

	if not python_exe.is_file():
		raise ToolError(f"虚拟环境创建失败，找不到 {python_exe}")
	return python_exe


def install(python_exe: Path, backend: str, packages: list[str], req_files: list[Path]) -> None:
	if not packages and not req_files:
		log("没有额外依赖需要安装")
		return
	uv = find_uv()
	use_uv = backend == "uv" or (backend == "auto" and uv)
	base = ([uv, "pip", "install", "--python", str(python_exe)] if use_uv
	        else [str(python_exe), "-m", "pip", "install"])

	args = list(base)
	for req in req_files:
		args += ["-r", str(req)]
	args += packages
	log(f"安装依赖: {', '.join(packages) or '（仅 requirements）'}")
	proc = run(args, check=False)
	if proc.returncode == 0:
		return

	log("一次性安装失败，改为逐个重试", "warn")
	failed: list[str] = []
	for package in packages:
		retry = base + [package]
		if run(retry, check=False).returncode != 0:
			failed.append(package)
	if failed:
		log(f"这些包没装上: {', '.join(failed)}；打包可能失败", "warn")


def install_pyinstaller(python_exe: Path, backend: str) -> None:
	uv = find_uv()
	use_uv = backend == "uv" or (backend == "auto" and uv)
	if use_uv:
		cmd = [uv, "pip", "install", "--python", str(python_exe), "pyinstaller"]
	else:
		run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=False)
		cmd = [str(python_exe), "-m", "pip", "install", "pyinstaller"]
	log("准备打包工具 PyInstaller")
	run(cmd)


# ---------------------------------------------------------------- PyInstaller

def _resolve_src(src: str, bases: list[Path]) -> str:
	path = Path(src)
	if path.is_absolute():
		return str(path)
	for base in bases:
		candidate = base / path
		if candidate.exists():
			return str(candidate.resolve())
	return src


def _data_spec(spec, bases: list[Path]) -> str:
	"""把 'src:dest' 或 {src=..., dest=...} 转成 PyInstaller 需要的 src<os.pathsep>dest。

	相对路径先在脚本所在目录找，再在当前目录找。
	"""
	if isinstance(spec, dict):
		src = spec.get("src") or spec.get("source")
		dest = spec.get("dest") or spec.get("destination") or "."
		if not src:
			raise ToolError(f"add_data/add_binary 缺少 src: {spec}")
		return f"{_resolve_src(str(src), bases)}{os.pathsep}{dest}"
	spec = str(spec)
	sep = os.pathsep if os.pathsep in spec else ":"
	if sep not in spec:
		raise ToolError(f"add_data/add_binary 格式应为 src:dest，收到: {spec}")
	src, dest = spec.rsplit(sep, 1)
	return f"{_resolve_src(src, bases)}{os.pathsep}{dest}"


def resolve_upx(cfg: dict, no_upx: bool) -> Path | None:
	if no_upx:
		return None
	raw = cfg["packaging"].get("upx_dir") or ""
	if raw:
		path = Path(raw).expanduser()
		if path.is_file():
			path = path.parent
		if not path.is_dir():
			log(f"UPX 路径不存在，已禁用: {raw}", "warn")
			return None
		return path
	found = shutil.which("upx")
	return Path(found).parent if found else None


def build_command(
	python_exe: Path, script: Path, name: str, cfg: dict,
	dist_dir: Path, work_dir: Path, spec_dir: Path, upx: Path | None,
) -> list:
	pack = cfg["packaging"]
	pyi = cfg["pyinstaller"]
	args = [str(python_exe), "-m", "PyInstaller", f"--{pack['mode']}", "--noconfirm", "--clean"]
	args += ["--name", name, "--distpath", str(dist_dir), "--workpath", str(work_dir),
	         "--specpath", str(spec_dir), f"--log-level={str(pack.get('log_level', 'INFO')).upper()}"]

	if pack.get("windowed"):
		args.append("--windowed")
	if pack.get("icon"):
		args += ["--icon", str(pack["icon"])]
	if upx:
		args += ["--upx-dir", str(upx)]
	bases = [script.parent.resolve(), Path.cwd().resolve()]
	for module in pyi.get("hidden_imports", []):
		args += ["--hidden-import", str(module)]
	for spec in pyi.get("add_data", []):
		args += ["--add-data", _data_spec(spec, bases)]
	for spec in pyi.get("add_binary", []):
		args += ["--add-binary", _data_spec(spec, bases)]
	for module in pyi.get("exclude_modules", []):
		args += ["--exclude-module", str(module)]
	for module in pyi.get("collect_all", []):
		args += ["--collect-all", str(module)]
	args += [str(a) for a in pack.get("extra_pyinstaller_args", [])]
	args.append(str(script))
	return args


def build_one(task: dict) -> dict:
	name = task["name"]
	log(f"开始打包 {task['script'].name} -> {name}")
	code, lines = run_stream(task["cmd"], cwd=task["cwd"])
	dist, name = task["dist_dir"], task["name"]
	if task["mode"] == "onedir":
		artifacts = [dist / name, dist / f"{name}.exe"]
	else:
		artifacts = [dist / f"{name}.exe", dist / name]
	ok = code == 0 and any(a.exists() for a in artifacts)
	if code == 0 and not ok:
		log(f"{name} 打包命令成功但没找到产物 {artifacts[0]}", "warn")
	return {"name": name, "script": task["script"], "ok": ok, "code": code, "lines": lines}


# ---------------------------------------------------------------- 主流程

def build_cli_overrides(args: argparse.Namespace) -> dict:
	over: dict = {"packaging": {}, "dependencies": {}, "pyinstaller": {}}
	pack = over["packaging"]
	if args.mode:
		pack["mode"] = args.mode
	if args.name:
		pack["name"] = args.name
	if args.icon:
		pack["icon"] = args.icon
	if args.windowed:
		pack["windowed"] = True
	if args.python:
		pack["python"] = args.python
	if args.backend:
		pack["backend"] = args.backend
	if args.venv:
		pack["venv"] = args.venv
	if args.output:
		pack["dist"] = args.output
	if args.jobs is not None:
		pack["jobs"] = args.jobs
	if args.upx_dir:
		pack["upx_dir"] = args.upx_dir
	if args.extra_arg:
		pack["extra_pyinstaller_args"] = args.extra_arg

	deps = over["dependencies"]
	if args.requirements:
		deps["requirements"] = args.requirements
	if args.pip:
		deps["pip_install"] = args.pip

	pyi = over["pyinstaller"]
	if args.hidden_import:
		pyi["hidden_imports"] = args.hidden_import
	if args.add_data:
		pyi["add_data"] = args.add_data
	if args.add_binary:
		pyi["add_binary"] = args.add_binary
	if args.collect_all:
		pyi["collect_all"] = args.collect_all
	return over


def print_plan(plans: list[dict], python_spec: str, backend: str, venv_dir: Path, dist_dir: Path) -> None:
	print(f"\n{PROG} {__version__} 计划")
	print(f"  环境      : {venv_dir}（后端 {backend}，Python {python_spec or '默认'}）")
	print(f"  输出      : {dist_dir}")
	for item in plans:
		cfg = item["config"]
		print(f"  - {item['script']}  ->  {item['name']}  [{cfg['packaging']['mode']}]")
		if item["packages"]:
			print(f"      依赖: {', '.join(item['packages'])}")
		for req in item["req_files"]:
			print(f"      requirements: {req}")
	if not plans:
		print("  （没有找到要打包的脚本）")
	print()


def main(argv: list[str] | None = None) -> int:
	global LOG_LEVEL
	parser = argparse.ArgumentParser(
		prog=PROG,
		description="扫描 Python 脚本，自动建虚拟环境、装依赖、用 PyInstaller 打包。",
		epilog="配置文件：./venvpy.toml 全局生效，<脚本名>.pack.toml 只管对应脚本。命令行参数优先级最高。",
	)
	parser.add_argument("targets", nargs="*", help="要打包的文件、目录或通配符，默认当前目录")
	parser.add_argument("-o", "--output", help=f"输出目录（默认 {DEFAULT_DIST}）")
	parser.add_argument("--venv", help=f"虚拟环境目录（默认 {DEFAULT_VENV}）")
	parser.add_argument("--python", help="建环境用的 Python 版本，例如 3.12（uv 后端生效）")
	parser.add_argument("--backend", choices=["auto", "uv", "pip"], help="装包后端，默认 auto（有 uv 用 uv）")
	parser.add_argument("--mode", choices=["onefile", "onedir"], help="单文件还是单目录")
	parser.add_argument("--name", help="输出名字，默认取脚本名")
	parser.add_argument("--icon", help="图标文件")
	parser.add_argument("--windowed", action="store_true", help="GUI 程序，不带控制台窗口")
	parser.add_argument("--jobs", type=int, help="同时打包几个，默认按 CPU 自动")
	parser.add_argument("--upx-dir", help="UPX 所在目录，默认在 PATH 里找")
	parser.add_argument("--no-upx", action="store_true", help="禁用 UPX 压缩")
	parser.add_argument("--requirements", action="append", help="额外 requirements 文件，可重复")
	parser.add_argument("--pip", action="append", help="手动补充要安装的包，可重复")
	parser.add_argument("--hidden-import", action="append", help="PyInstaller 隐藏导入，可重复")
	parser.add_argument("--add-data", action="append", help="src:dest，可重复")
	parser.add_argument("--add-binary", action="append", help="src:dest，可重复")
	parser.add_argument("--collect-all", action="append", help="收集整个包的资源，可重复")
	parser.add_argument("--extra-arg", action="append", help="原样传给 PyInstaller 的参数，可重复")
	parser.add_argument("--exclude", action="append", default=[], help="跳过匹配的文件/目录，可重复")
	parser.add_argument("--init", action="store_true", help="生成 <脚本>.pack.toml 模板后退出")
	parser.add_argument("--dry-run", action="store_true", help="只打印计划，不创建环境也不打包")
	parser.add_argument("--recreate", action="store_true", help="删除并重建虚拟环境")
	parser.add_argument("--clean", action="store_true", help="打包前删除虚拟环境、构建缓存和输出目录")
	parser.add_argument("--skip-deps", action="store_true", help="跳过用户依赖安装，只装 PyInstaller")
	parser.add_argument("--log-level", default="INFO", choices=["QUIET", "INFO", "DEBUG"], help="日志级别")
	parser.add_argument("-v", "--verbose", action="store_true", help="等同 --log-level DEBUG")
	parser.add_argument("-q", "--quiet", action="store_true", help="只看警告和错误")
	parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
	args = parser.parse_args(argv)

	LOG_LEVEL = "DEBUG" if args.verbose else ("QUIET" if args.quiet else args.log_level)
	root = Path.cwd()
	targets = args.targets or ["."]
	config_root = pick_config_root(targets)
	cli_over = build_cli_overrides(args)

	# --clean 要先知道路径，所以先粗读一份全局配置
	preview = _deep_merge(_default_config(), read_toml(config_root / "venvpy.toml") or {})
	preview = _deep_merge(preview, cli_over)
	venv_dir = (root / preview["packaging"]["venv"]).resolve()
	dist_dir = (root / preview["packaging"]["dist"]).resolve()
	build_root = (root / BUILD_DIR).resolve()

	try:
		files = discover(targets, args.exclude, {venv_dir, dist_dir, build_root})
	except ToolError as exc:
		log(str(exc), "error")
		return 2

	if not files:
		log("没找到要打包的 .py 文件", "warn")
		return 1

	if args.init:
		for script in files:
			cfg = resolve_config(script, config_root, {"packaging": {}, "dependencies": {}, "pyinstaller": {}})
			packages, _ = infer_dependencies([script], cfg, config_root, siblings=files)
			target = script.with_suffix(".pack.toml")
			if target.exists():
				log(f"已存在，跳过: {target}", "warn")
				continue
			target.write_text(config_template(script, packages), encoding="utf-8")
			log(f"已生成: {target}")
		return 0

	plans: list[dict] = []
	seen_names: dict[str, int] = {}
	try:
		for script in files:
			cfg = resolve_config(script, config_root, cli_over)
			pack = cfg["packaging"]
			name = pack.get("name") or script.stem
			if name in seen_names:
				if not pack.get("name"):
					name = f"{script.parent.name}_{script.stem}"
				log(f"输出名重复，改用 {name}", "warn")
			seen_names[name] = 1
			packages, req_files = infer_dependencies([script], cfg, config_root, siblings=files)
			plans.append({
				"script": script,
				"name": name,
				"config": cfg,
				"packages": [] if args.skip_deps else packages,
				"req_files": [] if args.skip_deps else req_files,
			})
	except ToolError as exc:
		log(str(exc), "error")
		return 2

	pack0 = plans[0]["config"]["packaging"]
	venv_dir = (root / (args.venv or pack0["venv"])).resolve()
	dist_dir = (root / (args.output or pack0["dist"])).resolve()
	backend = (args.backend or pack0["backend"] or "auto")
	python_spec = args.python or pack0.get("python") or ""

	if LOG_LEVEL != "QUIET":
		print_plan(plans, python_spec, backend, venv_dir, dist_dir)
	if args.dry_run:
		log("dry-run 结束，没有做任何改动")
		return 0

	if args.clean:
		for path in (venv_dir, build_root, dist_dir):
			if path.exists():
				log(f"清理 {path}", "warn")
				shutil.rmtree(path, ignore_errors=True)

	try:
		python_exe = ensure_venv(venv_dir, python_spec, backend, args.recreate or args.clean)
		install_pyinstaller(python_exe, backend)
		if not args.skip_deps:
			all_packages: list[str] = []
			all_reqs: list[Path] = []
			for item in plans:
				for pkg in item["packages"]:
					if pkg.lower() not in {p.lower() for p in all_packages}:
						all_packages.append(pkg)
				for req in item["req_files"]:
					if req not in all_reqs:
						all_reqs.append(req)
			install(python_exe, backend, all_packages, all_reqs)
	except ToolError as exc:
		log(str(exc), "error")
		return 1

	upx = resolve_upx(plans[0]["config"], args.no_upx)
	if upx:
		log(f"使用 UPX: {upx}")

	dist_dir.mkdir(parents=True, exist_ok=True)
	tasks = []
	for item in plans:
		work_dir = build_root / item["name"]
		work_dir.mkdir(parents=True, exist_ok=True)
		cmd = build_command(
			python_exe, item["script"], item["name"], item["config"],
			dist_dir, work_dir, work_dir, upx,
		)
		tasks.append({
			"cmd": cmd, "name": item["name"], "script": item["script"],
			"dist_dir": dist_dir, "mode": item["config"]["packaging"]["mode"], "cwd": root,
		})

	jobs = args.jobs or plans[0]["config"]["packaging"].get("jobs") or 0
	if not jobs or jobs < 1:
		jobs = min(4, os.cpu_count() or 1)
	jobs = max(1, min(jobs, len(tasks)))
	log(f"并行打包 {len(tasks)} 个脚本（{jobs} 个任务）")

	results: list[dict] = []
	if jobs == 1:
		for task in tasks:
			try:
				results.append(build_one(task))
			except ToolError as exc:
				log(str(exc), "error")
				results.append({"name": task["name"], "script": task["script"], "ok": False, "code": -1, "lines": []})
	else:
		with ThreadPoolExecutor(max_workers=jobs) as pool:
			futures = {pool.submit(build_one, task): task for task in tasks}
			for future in as_completed(futures):
				task = futures[future]
				try:
					results.append(future.result())
				except Exception as exc:  # noqa: BLE001 - 保证每个任务都有结果
					log(f"{task['name']} 打包异常: {exc}", "error")
					results.append({"name": task["name"], "script": task["script"], "ok": False, "code": -1, "lines": []})

	failed = [r for r in results if not r["ok"]]
	for result in results:
		if result["ok"]:
			log(f"完成 {result['script'].name} -> {result['name']}", "info")
		else:
			log(f"失败 {result['script'].name}（退出码 {result['code']}）", "error")
			for line in result["lines"][-30:]:
				print(f"    {line}")
	log(f"成功 {len(results) - len(failed)} / {len(results)}，产物在 {dist_dir}")
	return 0 if not failed else 1


if __name__ == "__main__":
	try:
		sys.exit(main())
	except KeyboardInterrupt:
		print("\n已中断", file=sys.stderr)
		sys.exit(130)
