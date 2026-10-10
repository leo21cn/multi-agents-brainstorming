import pathlib
import sys

# 保证从 backend/ 目录运行 pytest 时可导入 app 包
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
