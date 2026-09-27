# 本文件故意存在但保持为空:把 tests/ 从命名空间包变成常规包。
#
# 为什么需要:site-packages 里被第三方包装了一个顶层 tests 常规包
# (Lib/site-packages/tests,含 cpp/ python/),而常规包会压过
# 命名空间包的路径合并,导致 `from tests.utils.xxx import ...`
# 全部炸出 ModuleNotFoundError('tests.utils')。
# 本包在 conftest 注入的 sys.path[0](engineering/python)上,
# 常规包优先生效,从而压回 site-packages 的同名包。
# 曾是本地未入库文件,2026-09 重建仓库时丢失导致 11 个测试文件
# collection error;这次直接入库,防止再次丢失。
