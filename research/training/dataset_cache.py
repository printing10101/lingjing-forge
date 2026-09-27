"""
Dataset Cache Implementation

Implements a two-level caching system for LNN training datasets to avoid repeated
HDF5 parsing and feature extraction, significantly reducing training startup time.

Features:
- Cache key generation based on file path + mtime + file size
- Two-level cache: memory (dict) + disk (pickle files)
- Three-level cache validity checking
- LRU eviction policy for both memory and disk cache
- Thread-safe operations
- Comprehensive error handling
- Performance monitoring and statistics
"""

import os
import time
import json
import pickle
import hashlib
import hmac
import logging
import threading
from typing import Any, Dict, Optional, Tuple
from collections import OrderedDict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# 安全修复：缓存文件 HMAC 签名密钥。
# 防止攻击者篡改 .pkl 文件触发 pickle 反序列化 RCE。
#
# 密钥解析顺序：
# 1. 环境变量 ``LNN_CACHE_HMAC_KEY``（生产部署推荐；以 hex 或 base64 字符串形式提供，
# 长度需 ≥ 32 字节解码后）。设置后，磁盘缓存可在进程重启之间复用，提升命中率。
# 2. 未设置时回退到进程级随机密钥（基于 pid + 启动时间）。
# 此模式同样安全，但每次进程重启都会使旧 .pkl 文件 HMAC 校验失败而被清理，
# 缓存命中率下降。开发环境可保持回退；生产环境务必显式配置环境变量。
#
# 安全提示：
# - 密钥一旦变更，所有现有磁盘缓存将失效（HMAC 校验失败自动清理），属预期行为。
# - 切勿将真实密钥提交到版本控制；通过 .env 或部署平台密钥管理注入。
_LNN_CACHE_HMAC_KEY_ENV = os.environ.get("LNN_CACHE_HMAC_KEY", "").strip()


def _resolve_cache_hmac_key() -> bytes:
    """从环境变量解析 HMAC 密钥；失败时回退到进程级随机密钥。"""
    env_value = _LNN_CACHE_HMAC_KEY_ENV
    if env_value:
        # 优先尝试 hex 解码（推荐格式：64 字符 hex = 32 字节）
        try:
            decoded = bytes.fromhex(env_value)
            if len(decoded) >= 32:
                return decoded
            logger.warning(
                "LNN_CACHE_HMAC_KEY hex 解码成功但长度不足 32 字节（实际 %d），回退到进程级随机密钥。",
                len(decoded),
            )
        except ValueError:
            # 非 hex 字符串，尝试 base64 解码
            import base64

            try:
                decoded = base64.b64decode(env_value, validate=True)
                if len(decoded) >= 32:
                    return decoded
                logger.warning(
                    "LNN_CACHE_HMAC_KEY base64 解码成功但长度不足 32 字节（实际 %d），回退到进程级随机密钥。",
                    len(decoded),
                )
            except (ValueError, base64.binascii.Error):
                # 既非 hex 也非 base64，直接用原始字节（需 ≥ 32 字节）
                raw = env_value.encode("utf-8")
                if len(raw) >= 32:
                    # 派生定长密钥，避免直接使用可变长度密钥
                    return hashlib.sha256(raw).digest()
                logger.warning(
                    "LNN_CACHE_HMAC_KEY 既非有效 hex/base64，原始长度也不足 32 字节（实际 %d），回退到进程级随机密钥。",
                    len(raw),
                )
    # 回退：进程级随机密钥（开发模式安全默认）
    return hashlib.sha256(f"dataset_cache:{os.getpid()}:{time.time()}".encode()).digest()


_CACHE_HMAC_KEY = _resolve_cache_hmac_key()


@dataclass
class CacheEntry:
    """Represents a single cache entry with data, labels, and metadata."""

    cache_key: str
    data: Any
    labels: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    file_mtime: float = 0.0
    file_size: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    access_count: int = 0
    memory_size_bytes: int = 0


DEFAULT_CACHE_DIR = os.environ.get(
    "LNN_CACHE_DIR",
    "~/.lingjing/cache/datasets/",
)


class DatasetCache:
    """
    Dataset cache management class.

    Implements a two-level cache architecture:
    - Memory cache: stores recently used datasets (default max 1GB)
    - Disk cache: persistent storage for all historical caches (default max 5GB)

    Cache key generation algorithm:
    MD5(absolute file path + last modification timestamp + file size)

    Cache validity check (three levels):
    1. File path existence verification
    2. File modification timestamp comparison
    3. File size validation

    Cache is considered invalid if any check fails.
    """

    def __init__(
        self,
        cache_directory: str = DEFAULT_CACHE_DIR,
        max_cache_size: int = 5 * 1024 * 1024 * 1024,
        memory_cache_size: int = 1024 * 1024 * 1024,
        cache_eviction_policy: str = "lru",
    ):
        """
        Initialize dataset cache.

        Args:
            cache_directory: Disk cache directory path.
                Supports environment variable LNN_CACHE_DIR override.
                Defaults to ~/.lingjing/cache/datasets/
            max_cache_size: Maximum disk cache size in bytes (default: 5GB)
            memory_cache_size: Maximum memory cache size in bytes (default: 1GB)
            cache_eviction_policy: Cache eviction policy (default: LRU)
        """
        if max_cache_size < 1:
            raise ValueError(
                f"数据集缓存配置失败：'max_cache_size' 参数值必须大于等于 1，当前值: {max_cache_size}。该参数控制缓存中最多可存储的文件数量，请设置为合理的正整数（如 50）。"
            )
        if memory_cache_size < 1:
            raise ValueError(
                f"数据集缓存配置失败：'memory_cache_size' 参数值必须大于等于 1，当前值: {memory_cache_size}。该参数控制内存缓存的最大大小（单位: MB），请设置为合理的正整数（如 100）。"  # noqa: E501
            )
        if cache_eviction_policy.lower() not in ("lru",):
            raise ValueError(
                f"数据集缓存配置失败：不支持的缓存淘汰策略 '{cache_eviction_policy}'。当前支持的淘汰策略为：'lru'（最近最少使用）。请检查缓存配置中的 eviction_policy 参数。"
            )

        self._cache_directory = os.path.expanduser(cache_directory)
        self._max_cache_size = max_cache_size
        self._memory_cache_size = memory_cache_size
        self._eviction_policy = cache_eviction_policy.lower()

        self._lock = threading.Lock()
        self._memory_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._current_memory_usage = 0
        self._current_disk_usage = 0

        self._total_requests = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._total_load_time = 0.0

        self._initialize_cache_directory()
        self._load_disk_cache_metadata()

        logger.info(
            f"DatasetCache initialized: directory={self._cache_directory}, "
            f"max_disk_size={max_cache_size / (1024**3):.2f}GB, "
            f"max_memory_size={memory_cache_size / (1024**3):.2f}GB"
        )

    def _initialize_cache_directory(self) -> None:
        """初始化缓存目录，自动创建所有父目录"""
        try:
            os.makedirs(self._cache_directory, exist_ok=True)
            logger.debug("Cache directory ready: %s", self._cache_directory)
        except PermissionError as e:
            raise PermissionError(f"无法创建缓存目录: {self._cache_directory}") from e
        except OSError as e:
            raise OSError(f"创建缓存目录失败: {e}") from e

    def _load_disk_cache_metadata(self) -> None:
        """加载磁盘缓存元数据，计算当前磁盘占用"""
        try:
            if os.path.exists(self._cache_directory):
                for filename in os.listdir(self._cache_directory):
                    if filename.endswith(".pkl"):
                        filepath = os.path.join(self._cache_directory, filename)
                        try:
                            self._current_disk_usage += os.path.getsize(filepath)
                        except OSError as e:
                            # 静默处理：单个缓存文件元数据读取失败不影响整体加载流程
                            logger.debug(
                                f"Failed to read size for cache file {filepath}: {e}",
                                exc_info=True,
                            )
                logger.debug(f"Loaded disk cache metadata: {self._current_disk_usage} bytes")
        except (OSError, IOError, json.JSONDecodeError, ValueError, KeyError) as e:
            # 磁盘缓存元数据加载涉及文件 IO、JSON 解析、字段访问
            logger.warning(f"Failed to load disk cache metadata: {e}", exc_info=True)

    @staticmethod
    def generate_cache_key(file_path: str) -> Tuple[str, float, int]:
        """
        生成缓存键

        使用文件绝对路径、最后修改时间戳(mtime)和文件大小
        组合生成MD5哈希值作为缓存键

        Args:
            file_path: HDF5文件路径

        Returns:
            (cache_key, file_mtime, file_size) 元组

        注意：
        - 使用文件绝对路径确保同一文件的不同相对路径能命中同一缓存
        - mtime用于检测文件是否被修改
        - file_size作为额外校验，防止mtime精度不足的情况
        """
        abs_path = os.path.abspath(file_path)

        if not os.path.exists(abs_path):
            raise FileNotFoundError(
                f"数据集缓存加载失败：找不到文件 '{abs_path}'。可能原因：1) 文件路径配置错误；2) 文件已被删除或移动。请确认文件路径正确，或检查数据集是否已完整下载。"
            )

        file_stat = os.stat(abs_path)
        file_mtime = file_stat.st_mtime
        file_size = file_stat.st_size

        combined_key = f"{abs_path}:{file_mtime}:{file_size}"
        # 安全修复：使用 SHA256 替代 MD5，避免碰撞导致缓存投毒
        cache_key = hashlib.sha256(combined_key.encode("utf-8")).hexdigest()

        return cache_key, file_mtime, file_size

    def _validate_cache(self, file_path: str, expected_mtime: float, expected_size: int) -> bool:
        """
        三级缓存有效性检测

        检测顺序：
        1. 文件路径验证：检查源文件是否存在
        2. 文件修改时间戳比对：检查文件是否被修改
        3. 文件大小校验：额外验证，防止mtime精度不足

        Args:
            file_path: 源HDF5文件路径
            expected_mtime: 缓存记录的文件修改时间
            expected_size: 缓存记录的文件大小

        Returns:
            True表示缓存有效，False表示缓存失效
        """
        abs_path = os.path.abspath(file_path)

        if not os.path.exists(abs_path):
            logger.debug("Cache invalid: file not found: %s", abs_path)
            return False

        try:
            current_stat = os.stat(abs_path)
            current_mtime = current_stat.st_mtime
            current_size = current_stat.st_size

            if abs(current_mtime - expected_mtime) > 0.001:
                logger.debug(f"Cache invalid: mtime changed (expected={expected_mtime}, current={current_mtime})")
                return False

            if current_size != expected_size:
                logger.debug(f"Cache invalid: size changed (expected={expected_size}, current={current_size})")
                return False

            return True

        except OSError as e:
            logger.warning("Cache validation failed: %s", e)
            return False

    def get(
        self,
        file_path: str,
        force_refresh: bool = False,
    ) -> Optional[Tuple[Any, Any, Dict[str, Any]]]:
        """
        获取缓存的数据集

        缓存优先策略：
        1. 检查内存缓存
        2. 检查磁盘缓存
        3. 缓存未命中返回None

        Args:
            file_path: HDF5文件路径
            force_refresh: 是否强制刷新（跳过缓存检查）

        Returns:
            (data, labels, metadata) 元组，如果缓存未命中则返回None
        """
        with self._lock:
            self._total_requests += 1

            if force_refresh:
                self._cache_misses += 1
                logger.info("Cache miss (force refresh): %s", file_path)
                return None

            try:
                cache_key, file_mtime, file_size = self.generate_cache_key(file_path)
            except (FileNotFoundError, OSError) as e:
                logger.warning("Failed to generate cache key: %s", e)
                self._cache_misses += 1
                return None

        if self._validate_cache(file_path, file_mtime, file_size):
            with self._lock:
                memory_result = self._get_from_memory(cache_key)
                if memory_result is not None:
                    self._cache_hits += 1
                    # P2-批次2 修复：改用 %s 懒求值。缓存命中是热路径，
                    # 每次训练 batch 加载都会触发，info 级别关闭时避免插值开销。
                    logger.info("Cache hit (memory): %s, key=%s...", file_path, cache_key[:8])
                    return memory_result

                disk_result = self._get_from_disk(cache_key, file_path, file_mtime, file_size)
                if disk_result is not None:
                    self._cache_hits += 1
                    logger.info("Cache hit (disk): %s, key=%s...", file_path, cache_key[:8])
                    return disk_result

        with self._lock:
            self._cache_misses += 1
            logger.info("Cache miss: %s", file_path)
            return None

    def _get_from_memory(self, cache_key: str) -> Optional[Tuple[Any, Any, Dict[str, Any]]]:
        """
        从内存缓存获取数据

        Args:
            cache_key: 缓存键

        Returns:
            (data, labels, metadata) 或 None
        """
        if cache_key in self._memory_cache:
            entry = self._memory_cache.pop(cache_key)
            entry.last_accessed = time.time()
            entry.access_count += 1
            self._memory_cache[cache_key] = entry
            return entry.data, entry.labels, entry.metadata

        return None

    def _get_from_disk(
        self,
        cache_key: str,
        file_path: str,
        file_mtime: float,
        file_size: int,
    ) -> Optional[Tuple[Any, Any, Dict[str, Any]]]:
        """
        从磁盘缓存加载数据并提升到内存缓存

        Args:
            cache_key: 缓存键
            file_path: 源文件路径
            file_mtime: 文件修改时间
            file_size: 文件大小

        Returns:
            (data, labels, metadata) 或 None
        """
        cache_file = os.path.join(self._cache_directory, f"{cache_key}.pkl")

        if not os.path.exists(cache_file):
            return None

        try:
            with open(cache_file, "rb") as f:
                raw = f.read()
            # 安全修复：先验证 HMAC 签名，再反序列化 pickle，防止被篡改的 .pkl 触发 RCE
            if len(raw) < 32:
                logger.warning("Cache file too small, possibly corrupted: %s", cache_file)
                return None
            signature = raw[:32]
            payload = raw[32:]
            expected_sig = hmac.new(_CACHE_HMAC_KEY, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected_sig):
                logger.warning(
                    "Cache file HMAC signature mismatch, possible tampering: %s",
                    cache_file,
                )
                try:
                    os.remove(cache_file)
                except OSError as rm_err:
                    # 删除失败不阻塞返回 None（已判定签名不匹配），
                    # 记录便于排查：下次加载仍会触发签名校验失败
                    logger.debug("Failed to remove tampered cache file %s: %s", cache_file, rm_err)
                return None
            entry_data = pickle.loads(payload)

            entry = CacheEntry(
                cache_key=cache_key,
                data=entry_data.get("data"),
                labels=entry_data.get("labels"),
                metadata=entry_data.get("metadata", {}),
                file_path=file_path,
                file_mtime=file_mtime,
                file_size=file_size,
                created_at=entry_data.get("created_at", time.time()),
                last_accessed=time.time(),
                access_count=entry_data.get("access_count", 0) + 1,
                memory_size_bytes=entry_data.get("memory_size_bytes", 0),
            )

            self._add_to_memory(entry)

            return entry.data, entry.labels, entry.metadata

        except (pickle.UnpicklingError, EOFError, KeyError) as e:
            logger.warning(f"Failed to load cache from disk: {e}, removing corrupted file")
            try:
                os.remove(cache_file)
            except OSError as remove_err:
                # 损坏文件清理失败不应阻塞主流程，但需记录以便后续排查
                logger.warning(
                    f"Failed to remove corrupted cache file {cache_file}: {remove_err}",
                    exc_info=True,
                )
            return None
        except (OSError, IOError, ValueError, TypeError, KeyError) as e:
            # 兜底捕获：磁盘缓存加载可能涉及文件 IO、反序列化、字段缺失等未知错误
            logger.warning(f"Unexpected error loading cache from disk: {e}", exc_info=True)
            return None

    def put(
        self,
        file_path: str,
        data: Any,
        labels: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        存储数据集到缓存

        流程：
        1. 生成缓存键
        2. 创建缓存条目
        3. 写入内存缓存
        4. 持久化到磁盘缓存

        Args:
            file_path: HDF5文件路径
            data: 特征数据
            labels: 标签数据
            metadata: 附加元数据

        Returns:
            缓存键
        """
        with self._lock:
            try:
                cache_key, file_mtime, file_size = self.generate_cache_key(file_path)
            except (FileNotFoundError, OSError) as e:
                logger.error("Failed to generate cache key for put: %s", e)
                raise

            memory_size = self._estimate_memory_size(data, labels)

            entry = CacheEntry(
                cache_key=cache_key,
                data=data,
                labels=labels,
                metadata=metadata or {},
                file_path=os.path.abspath(file_path),
                file_mtime=file_mtime,
                file_size=file_size,
                created_at=time.time(),
                last_accessed=time.time(),
                access_count=1,
                memory_size_bytes=memory_size,
            )

            self._add_to_memory(entry)
            self._save_to_disk(entry)

            logger.info(f"Cache stored: {file_path}, key={cache_key[:8]}..., size={memory_size / 1024:.2f}KB")

            return cache_key

    def _add_to_memory(self, entry: CacheEntry) -> None:
        """
        添加数据到内存缓存，必要时触发LRU淘汰

        Args:
            entry: 缓存条目
        """
        if entry.cache_key in self._memory_cache:
            old_entry = self._memory_cache.pop(entry.cache_key)
            self._current_memory_usage -= old_entry.memory_size_bytes

        if entry.memory_size_bytes > self._memory_cache_size:
            logger.warning(
                f"Entry size {entry.memory_size_bytes} exceeds memory cache limit "
                f"{self._memory_cache_size}, skipping memory cache"
            )
            return

        while (
            self._current_memory_usage + entry.memory_size_bytes > self._memory_cache_size
            and len(self._memory_cache) > 0
        ):
            self._evict_from_memory()

        self._memory_cache[entry.cache_key] = entry
        self._current_memory_usage += entry.memory_size_bytes

    def _evict_from_memory(self) -> None:
        """
        从内存缓存淘汰最久未使用的条目（LRU）

        淘汰策略：
        - 使用OrderedDict的popitem(last=False)获取最久未使用的条目
        - 减少内存使用统计
        """
        if len(self._memory_cache) == 0:
            return

        evicted_key, evicted_entry = self._memory_cache.popitem(last=False)
        self._current_memory_usage -= evicted_entry.memory_size_bytes
        logger.debug(
            f"Memory cache evicted: key={evicted_key[:8]}..., freed={evicted_entry.memory_size_bytes / 1024:.2f}KB"
        )

    def _save_to_disk(self, entry: CacheEntry) -> None:
        """
        持久化缓存条目到磁盘

        Args:
            entry: 缓存条目
        """
        cache_file = os.path.join(self._cache_directory, f"{entry.cache_key}.pkl")

        try:
            disk_data = {
                "data": entry.data,
                "labels": entry.labels,
                "metadata": entry.metadata,
                "file_path": entry.file_path,
                "file_mtime": entry.file_mtime,
                "file_size": entry.file_size,
                "created_at": entry.created_at,
                "access_count": entry.access_count,
                "memory_size_bytes": entry.memory_size_bytes,
            }

            # 安全修复：写入时附加 HMAC 签名，读取时验证，防止篡改触发 RCE
            payload = pickle.dumps(disk_data, protocol=pickle.HIGHEST_PROTOCOL)
            signature = hmac.new(_CACHE_HMAC_KEY, payload, hashlib.sha256).digest()
            with open(cache_file, "wb") as f:
                f.write(signature)
                f.write(payload)

            file_size = os.path.getsize(cache_file)

            while self._current_disk_usage + file_size > self._max_cache_size and self._has_disk_cache():
                self._evict_from_disk()

            self._current_disk_usage += file_size

        except OSError as e:
            if "No space left on device" in str(e):
                logger.error("Disk cache failed: no space left on device")
                self._clear_disk_cache()
            else:
                logger.error("Failed to save cache to disk: %s", e)
        except (pickle.PickleError, ValueError, TypeError, AttributeError) as e:
            # 兜底捕获：磁盘缓存序列化可能因对象类型、属性访问等失败
            logger.error(f"Unexpected error saving cache to disk: {e}", exc_info=True)

    def _has_disk_cache(self) -> bool:
        """检查是否有磁盘缓存文件"""
        try:
            for filename in os.listdir(self._cache_directory):
                if filename.endswith(".pkl"):
                    return True
            return False
        except OSError:
            return False

    def _evict_from_disk(self) -> None:
        """
        从磁盘缓存淘汰最旧的文件（LRU策略）

        淘汰策略：
        - 遍历所有.pkl文件，找到最早创建的
        - 删除该文件并更新磁盘使用统计
        """
        try:
            oldest_file = None
            oldest_time = float("inf")

            for filename in os.listdir(self._cache_directory):
                if filename.endswith(".pkl"):
                    filepath = os.path.join(self._cache_directory, filename)
                    try:
                        mtime = os.path.getmtime(filepath)
                        if mtime < oldest_time:
                            oldest_time = mtime
                            oldest_file = filepath
                    except OSError:
                        continue

            if oldest_file:
                file_size = os.path.getsize(oldest_file)
                os.remove(oldest_file)
                self._current_disk_usage -= file_size
                logger.debug(
                    f"Disk cache evicted: file={os.path.basename(oldest_file)}, freed={file_size / 1024:.2f}KB"
                )

        except (OSError, ValueError, TypeError, AttributeError) as e:
            # 磁盘缓存清理涉及文件 IO、属性访问等
            logger.warning(f"Failed to evict from disk cache: {e}", exc_info=True)

    def _clear_disk_cache(self) -> None:
        """清空所有磁盘缓存"""
        try:
            for filename in os.listdir(self._cache_directory):
                if filename.endswith(".pkl"):
                    filepath = os.path.join(self._cache_directory, filename)
                    try:
                        os.remove(filepath)
                    except OSError as e:
                        # 单个缓存文件清理失败不影响整体清空流程
                        logger.warning(
                            f"Failed to remove cache file {filepath}: {e}",
                            exc_info=True,
                        )
            self._current_disk_usage = 0
            logger.info("Disk cache cleared")

        except (OSError, ValueError, TypeError, AttributeError) as e:
            # 兜底捕获：清空缓存涉及目录遍历、文件 IO 等
            logger.error(f"Failed to clear disk cache: {e}", exc_info=True)

    @staticmethod
    def _estimate_memory_size(data: Any, labels: Any) -> int:
        """
        估算数据占用的内存大小

        Args:
            data: 特征数据（numpy数组或其他）
            labels: 标签数据

        Returns:
            内存大小（字节）
        """
        size = 0

        try:
            if hasattr(data, "nbytes"):
                size += data.nbytes
            elif hasattr(data, "__len__"):
                size += len(data) * 8
        except (TypeError, AttributeError, ValueError) as e:
            # 内存估算失败时使用默认值，仅影响缓存淘汰判断精度
            logger.debug(
                f"Failed to estimate memory size for data: {e}",
                exc_info=True,
            )

        try:
            if hasattr(labels, "nbytes"):
                size += labels.nbytes
            elif hasattr(labels, "__len__"):
                size += len(labels) * 8
        except (TypeError, AttributeError, ValueError) as e:
            # 标签内存估算失败时使用默认值，仅影响缓存淘汰判断精度
            logger.debug(
                f"Failed to estimate memory size for labels: {e}",
                exc_info=True,
            )

        return max(size, 1024)

    def clear(self, level: str = "global") -> Tuple[int, int]:
        """
        清除缓存

        Args:
            level: 清除级别
                - "global": 清除所有缓存（内存+磁盘）
                - "memory": 仅清除内存缓存
                - "disk": 仅清除磁盘缓存

        Returns:
            (清除的缓存数量, 释放的内存大小字节)
        """
        with self._lock:
            count = 0
            freed = 0

            if level in ("global", "memory"):
                count += len(self._memory_cache)
                freed += self._current_memory_usage
                self._memory_cache.clear()
                self._current_memory_usage = 0
                logger.info("Memory cache cleared: %s entries, %s bytes", count, freed)

            if level in ("global", "disk"):
                disk_count = 0
                disk_freed = self._current_disk_usage
                try:
                    for filename in os.listdir(self._cache_directory):
                        if filename.endswith(".pkl"):
                            filepath = os.path.join(self._cache_directory, filename)
                            try:
                                os.remove(filepath)
                                disk_count += 1
                            except OSError as e:
                                # 单个磁盘缓存清理失败不影响整体计数
                                logger.warning(
                                    f"Failed to remove cache file {filepath}: {e}",
                                    exc_info=True,
                                )
                except (OSError, ValueError, TypeError, AttributeError) as e:
                    # 兜底捕获：批量清理磁盘缓存涉及目录遍历、文件 IO
                    logger.warning(f"Failed to clear disk cache: {e}", exc_info=True)

                self._current_disk_usage = 0
                count += disk_count
                freed += disk_freed
                logger.info(f"Disk cache cleared: {disk_count} files, {disk_freed} bytes")

            return count, freed

    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息

        Returns:
            包含缓存命中率、加载时间、缓存大小等指标的字典
        """
        with self._lock:
            hit_rate = self._cache_hits / self._total_requests if self._total_requests > 0 else 0.0

            avg_load_time = self._total_load_time / self._total_requests if self._total_requests > 0 else 0.0

            return {
                "total_requests": self._total_requests,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "hit_rate": round(hit_rate, 4),
                "average_load_time_ms": round(avg_load_time * 1000, 2),
                "memory_cache_entries": len(self._memory_cache),
                "memory_cache_usage_bytes": self._current_memory_usage,
                "memory_cache_usage_mb": round(self._current_memory_usage / (1024 * 1024), 2),
                "memory_cache_limit_bytes": self._memory_cache_size,
                "memory_cache_limit_mb": round(self._memory_cache_size / (1024 * 1024), 2),
                "disk_cache_usage_bytes": self._current_disk_usage,
                "disk_cache_usage_mb": round(self._current_disk_usage / (1024 * 1024), 2),
                "disk_cache_limit_bytes": self._max_cache_size,
                "disk_cache_limit_mb": round(self._max_cache_size / (1024 * 1024), 2),
                "eviction_policy": self._eviction_policy,
                "cache_directory": self._cache_directory,
            }

    def remove(self, file_path: str) -> bool:
        """
        移除指定文件的缓存

        Args:
            file_path: HDF5文件路径

        Returns:
            True表示成功移除，False表示缓存不存在
        """
        with self._lock:
            try:
                cache_key, _, _ = self.generate_cache_key(file_path)
            except (FileNotFoundError, OSError):
                return False

            removed = False

            if cache_key in self._memory_cache:
                entry = self._memory_cache.pop(cache_key)
                self._current_memory_usage -= entry.memory_size_bytes
                removed = True

            cache_file = os.path.join(self._cache_directory, f"{cache_key}.pkl")
            if os.path.exists(cache_file):
                try:
                    file_size = os.path.getsize(cache_file)
                    os.remove(cache_file)
                    self._current_disk_usage -= file_size
                    removed = True
                except OSError as e:
                    # 单个缓存条目移除失败不影响其他缓存清理
                    logger.warning(
                        f"Failed to remove cache entry {cache_file}: {e}",
                        exc_info=True,
                    )

            if removed:
                logger.info("Cache removed: %s", file_path)

            return removed

    def invalidate(self, file_path: str) -> bool:
        """
        使指定文件的缓存失效（等同于remove）

        Args:
            file_path: HDF5文件路径

        Returns:
            True表示成功失效，False表示缓存不存在
        """
        return self.remove(file_path)
