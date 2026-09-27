"""
Xmaker 文件传输集成模块

负责：
1. G-code 文件上传到 Xmaker 平台
2. 加工状态监控
3. 加工参数下发
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class MachineStatus(Enum):
    """机床状态"""

    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 加工中
    PAUSED = "paused"  # 暂停
    ERROR = "error"  # 错误
    OFFLINE = "offline"  # 离线


@dataclass
class UploadResult:
    """上传结果"""

    success: bool
    file_id: str = ""
    file_url: str = ""
    error_message: str = ""
    upload_time_ms: int = 0


@dataclass
class MachineStatusInfo:
    """机床状态信息"""

    status: MachineStatus = MachineStatus.OFFLINE
    current_job_id: str = ""
    progress_percent: float = 0.0
    elapsed_time_sec: int = 0
    remaining_time_sec: int = 0
    current_line: int = 0
    total_lines: int = 0
    error_code: str = ""
    error_message: str = ""


class XmakerIntegration:
    """Xmaker 集成客户端

    用于与 Xmaker 平台交互，支持：
    - G-code 文件上传
    - 加工任务管理
    - 实时状态监控
    """

    def __init__(
        self,
        # 默认 endpoint 通过环境变量 XMAKER_API_ENDPOINT 覆盖；
        # 统一使用 127.0.0.1（项目约定），避免 localhost 解析差异
        api_endpoint: str = "",
        api_key: str = "",
        timeout_sec: float = 30.0,
        max_retries: int = 3,
    ):
        if not api_endpoint:
            api_endpoint = os.getenv("XMAKER_API_ENDPOINT", "http://127.0.0.1:8080/api")
        self.api_endpoint = api_endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._session: requests.Session | None = None

    # 内部辅助

    def _get_session(self) -> requests.Session:
        """获取（或创建）带重试策略和认证头的 HTTP Session"""
        if self._session is not None:
            return self._session

        session = requests.Session()

        # 认证
        if self.api_key:
            session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "X-API-Key": self.api_key,
                }
            )

        # 重试策略：对 5xx / 连接错误进行幂等重试
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        self._session = session
        return session

    def close(self) -> None:
        """关闭 HTTP Session，释放连接池资源"""
        if self._session is not None:
            self._session.close()
            self._session = None
            logger.debug("Xmaker HTTP session 已关闭")

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        stream: bool = False,
        extra_timeout: float | None = None,
    ) -> requests.Response:
        """统一的 HTTP 请求入口，带日志与异常转换"""
        session = self._get_session()
        url = f"{self.api_endpoint}{path}"
        timeout = extra_timeout or self.timeout_sec

        logger.debug(
            "Xmaker API %s %s  params=%s  json=%s",
            method,
            url,
            params,
            json_body,
        )

        resp = session.request(
            method=method,
            url=url,
            json=json_body,
            params=params,
            files=files,
            data=data,
            timeout=timeout,
            stream=stream,
        )

        logger.debug(
            "Xmaker API 响应: %s %s -> %d  (%d bytes)",
            method,
            url,
            resp.status_code,
            len(resp.content) if not stream else 0,
        )

        resp.raise_for_status()
        return resp

    def upload_gcode(
        self,
        file_path: str | Path,
        job_name: str | None = None,
        metadata: dict | None = None,
    ) -> UploadResult:
        """上传 G-code 文件到 Xmaker 平台

        Args:
            file_path: G-code 文件路径
            job_name: 加工任务名称（可选）
            metadata: 附加元数据（可选）

        Returns:
            UploadResult: 上传结果
        """
        t0 = time.perf_counter()
        path = Path(file_path)

        if not path.exists():
            return UploadResult(
                success=False,
                error_message=f"文件不存在: {file_path}",
            )

        if path.suffix.lower() not in (".gcode", ".nc", ".tap"):
            return UploadResult(
                success=False,
                error_message=f"不支持的文件格式: {path.suffix}",
            )

        # 调用 Xmaker REST API 上传文件
        try:
            upload_name = job_name or path.stem
            # 值类型混合（元组表单值 / 文件 3 元组 / JSON 字符串），显式 Any
            form_data: dict[str, Any] = {"job_name": (None, upload_name)}
            if metadata:
                form_data["metadata"] = (None, json.dumps(metadata, ensure_ascii=False))

            with open(path, "rb") as f:
                form_data["file"] = (path.name, f, "application/octet-stream")
                resp = self._request(
                    "POST",
                    "/files/upload",
                    files=form_data,
                    extra_timeout=max(self.timeout_sec, path.stat().st_size / (1024 * 1024) * 2),
                )

            body = resp.json()
            file_id = body.get("file_id", body.get("id", ""))
            file_url = body.get("file_url", body.get("url", ""))
            if not file_url and file_id:
                file_url = f"{self.api_endpoint}/files/{file_id}"

            latency_ms = int((time.perf_counter() - t0) * 1000)

            logger.info(
                "G-code 上传成功: %s -> %s (%d ms)",
                file_path,
                file_id,
                latency_ms,
            )

            return UploadResult(
                success=True,
                file_id=file_id,
                file_url=file_url,
                upload_time_ms=latency_ms,
            )

        except requests.exceptions.Timeout:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.error("G-code 上传超时: %s (timeout=%ss)", file_path, self.timeout_sec)
            return UploadResult(
                success=False,
                error_message=f"上传超时（{self.timeout_sec}s）",
                upload_time_ms=latency_ms,
            )
        except requests.exceptions.ConnectionError as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.error("G-code 上传连接失败: %s", e)
            return UploadResult(
                success=False,
                error_message=f"连接失败: {e}",
                upload_time_ms=latency_ms,
            )
        except requests.exceptions.HTTPError as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            status = e.response.status_code if e.response is not None else "N/A"
            logger.error("G-code 上传 HTTP 错误 %s: %s", status, e)
            return UploadResult(
                success=False,
                error_message=f"HTTP {status}: {e}",
                upload_time_ms=latency_ms,
            )
        except (requests.exceptions.RequestException, IOError, json.JSONDecodeError, OSError) as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.error("G-code 上传失败: %s", e, exc_info=True)
            return UploadResult(
                success=False,
                error_message="G-code 上传失败，请检查网络或机床连接",
                upload_time_ms=latency_ms,
            )

    def get_machine_status(self, machine_id: str = "default") -> MachineStatusInfo:
        """获取机床状态

        Args:
            machine_id: 机床 ID

        Returns:
            MachineStatusInfo: 机床状态信息
        """
        try:
            resp = self._request(
                "GET",
                f"/machines/{machine_id}/status",
            )

            body = resp.json()

            # 映射 API 响应到 MachineStatus 枚举
            status_map = {
                "idle": MachineStatus.IDLE,
                "running": MachineStatus.RUNNING,
                "paused": MachineStatus.PAUSED,
                "error": MachineStatus.ERROR,
                "offline": MachineStatus.OFFLINE,
            }

            status_str = body.get("status", "offline").lower()
            status = status_map.get(status_str, MachineStatus.OFFLINE)

            logger.debug("机床状态: machine_id=%s, status=%s", machine_id, status.value)

            return MachineStatusInfo(
                status=status,
                current_job_id=body.get("current_job_id", ""),
                progress_percent=float(body.get("progress_percent", 0.0)),
                elapsed_time_sec=int(body.get("elapsed_time_sec", 0)),
                remaining_time_sec=int(body.get("remaining_time_sec", 0)),
                current_line=int(body.get("current_line", 0)),
                total_lines=int(body.get("total_lines", 0)),
                error_code=body.get("error_code", ""),
                error_message=body.get("error_message", ""),
            )

        except requests.exceptions.Timeout:
            logger.error("获取机床状态超时: machine_id=%s (timeout=%ss)", machine_id, self.timeout_sec)
            return MachineStatusInfo(
                status=MachineStatus.OFFLINE,
                error_message=f"查询超时（{self.timeout_sec}s）",
            )
        except requests.exceptions.ConnectionError as e:
            logger.error("获取机床状态连接失败: machine_id=%s, error=%s", machine_id, e)
            return MachineStatusInfo(
                status=MachineStatus.OFFLINE,
                error_message=f"连接失败: {e}",
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("获取机床状态 HTTP 错误: machine_id=%s, status=%s", machine_id, status_code)
            return MachineStatusInfo(
                status=MachineStatus.OFFLINE,
                error_message=f"HTTP {status_code}: {e}",
            )
        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError, OSError) as e:
            logger.error("获取机床状态失败: machine_id=%s, error=%s", machine_id, e, exc_info=True)
            return MachineStatusInfo(
                status=MachineStatus.OFFLINE,
                error_message="机床状态查询失败，请检查网络或机床连接",
            )

    def start_job(
        self,
        file_id: str,
        machine_id: str = "default",
        parameters: dict | None = None,
    ) -> bool:
        """启动加工任务

        Args:
            file_id: 文件 ID
            machine_id: 机床 ID
            parameters: 加工参数（可选）

        Returns:
            bool: 是否启动成功
        """
        try:
            # 值类型混合（str 字段 / 嵌套 dict parameters），显式 Any
            payload: dict[str, Any] = {
                "file_id": file_id,
                "machine_id": machine_id,
            }
            if parameters:
                payload["parameters"] = parameters

            logger.info("启动加工任务: file_id=%s, machine_id=%s", file_id, machine_id)

            resp = self._request(
                "POST",
                "/jobs/start",
                json_body=payload,
            )

            body = resp.json()
            success = body.get("success", resp.status_code in (200, 201, 202))

            if success:
                logger.info("加工任务启动成功: file_id=%s, machine_id=%s", file_id, machine_id)
            else:
                error_msg = body.get("error_message", body.get("message", "未知错误"))
                logger.warning("加工任务启动失败: file_id=%s, error=%s", file_id, error_msg)

            return success

        except requests.exceptions.Timeout:
            logger.error("启动加工任务超时: file_id=%s (timeout=%ss)", file_id, self.timeout_sec)
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("启动加工任务连接失败: file_id=%s, error=%s", file_id, e)
            return False
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("启动加工任务 HTTP 错误: file_id=%s, status=%s", file_id, status_code)
            return False
        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
            logger.error("启动加工任务失败: file_id=%s, error=%s", file_id, e, exc_info=True)
            return False

    def pause_job(self, machine_id: str = "default") -> bool:
        """暂停加工任务

        Args:
            machine_id: 机床 ID

        Returns:
            bool: 是否暂停成功
        """
        try:
            logger.info("暂停加工任务: machine_id=%s", machine_id)

            resp = self._request(
                "POST",
                f"/machines/{machine_id}/pause",
            )

            body = resp.json()
            success = body.get("success", resp.status_code in (200, 201, 202))

            if success:
                logger.info("加工任务暂停成功: machine_id=%s", machine_id)
            else:
                error_msg = body.get("error_message", body.get("message", "未知错误"))
                logger.warning("加工任务暂停失败: machine_id=%s, error=%s", machine_id, error_msg)

            return success

        except requests.exceptions.Timeout:
            logger.error("暂停加工任务超时: machine_id=%s (timeout=%ss)", machine_id, self.timeout_sec)
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("暂停加工任务连接失败: machine_id=%s, error=%s", machine_id, e)
            return False
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("暂停加工任务 HTTP 错误: machine_id=%s, status=%s", machine_id, status_code)
            return False
        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
            logger.error("暂停加工任务失败: machine_id=%s, error=%s", machine_id, e, exc_info=True)
            return False

    def resume_job(self, machine_id: str = "default") -> bool:
        """恢复加工任务

        Args:
            machine_id: 机床 ID

        Returns:
            bool: 是否恢复成功
        """
        try:
            logger.info("恢复加工任务: machine_id=%s", machine_id)

            resp = self._request(
                "POST",
                f"/machines/{machine_id}/resume",
            )

            body = resp.json()
            success = body.get("success", resp.status_code in (200, 201, 202))

            if success:
                logger.info("加工任务恢复成功: machine_id=%s", machine_id)
            else:
                error_msg = body.get("error_message", body.get("message", "未知错误"))
                logger.warning("加工任务恢复失败: machine_id=%s, error=%s", machine_id, error_msg)

            return success

        except requests.exceptions.Timeout:
            logger.error("恢复加工任务超时: machine_id=%s (timeout=%ss)", machine_id, self.timeout_sec)
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("恢复加工任务连接失败: machine_id=%s, error=%s", machine_id, e)
            return False
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("恢复加工任务 HTTP 错误: machine_id=%s, status=%s", machine_id, status_code)
            return False
        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
            logger.error("恢复加工任务失败: machine_id=%s, error=%s", machine_id, e, exc_info=True)
            return False

    def stop_job(self, machine_id: str = "default") -> bool:
        """停止加工任务

        Args:
            machine_id: 机床 ID

        Returns:
            bool: 是否停止成功
        """
        try:
            logger.info("停止加工任务: machine_id=%s", machine_id)

            resp = self._request(
                "POST",
                f"/machines/{machine_id}/stop",
            )

            body = resp.json()
            success = body.get("success", resp.status_code in (200, 201, 202))

            if success:
                logger.info("加工任务停止成功: machine_id=%s", machine_id)
            else:
                error_msg = body.get("error_message", body.get("message", "未知错误"))
                logger.warning("加工任务停止失败: machine_id=%s, error=%s", machine_id, error_msg)

            return success

        except requests.exceptions.Timeout:
            logger.error("停止加工任务超时: machine_id=%s (timeout=%ss)", machine_id, self.timeout_sec)
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("停止加工任务连接失败: machine_id=%s, error=%s", machine_id, e)
            return False
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("停止加工任务 HTTP 错误: machine_id=%s, status=%s", machine_id, status_code)
            return False
        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
            logger.error("停止加工任务失败: machine_id=%s, error=%s", machine_id, e, exc_info=True)
            return False

    def download_gcode(
        self,
        file_id: str,
        output_path: str | Path,
    ) -> bool:
        """从 Xmaker 平台下载 G-code 文件

        Args:
            file_id: 文件 ID
            output_path: 输出路径

        Returns:
            bool: 是否下载成功
        """
        try:
            logger.info("下载 G-code: file_id=%s -> %s", file_id, output_path)

            resp = self._request(
                "GET",
                f"/files/{file_id}/download",
                stream=True,
            )

            # 确保输出目录存在
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # 流式写入文件
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            logger.info("G-code 下载成功: file_id=%s -> %s", file_id, output_path)
            return True

        except requests.exceptions.Timeout:
            logger.error("下载 G-code 超时: file_id=%s (timeout=%ss)", file_id, self.timeout_sec)
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("下载 G-code 连接失败: file_id=%s, error=%s", file_id, e)
            return False
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error("下载 G-code HTTP 错误: file_id=%s, status=%s", file_id, status_code)
            return False
        except IOError as e:
            logger.error("下载 G-code 写入文件失败: file_id=%s, path=%s, error=%s", file_id, output_path, e)
            return False
        except (requests.exceptions.RequestException, OSError) as e:
            logger.error("下载 G-code 失败: file_id=%s, error=%s", file_id, e, exc_info=True)
            return False


__all__ = [
    "XmakerIntegration",
    "UploadResult",
    "MachineStatus",
    "MachineStatusInfo",
]
