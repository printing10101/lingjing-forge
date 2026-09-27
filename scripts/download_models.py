#!/usr/bin/env python3
"""
AI 模型下载工具 - 支持魔搭社区（ModelScope）和国内镜像

使用方法：
    # 从魔搭社区下载模型（推荐）
    python scripts/download_models.py --source modelscope --model qwen2.5-coder:7b

    # 从 HuggingFace 镜像站下载
    python scripts/download_models.py --source hf-mirror --model qwen2.5-coder:7b

    # 下载所有必需模型
    python scripts/download_models.py --all
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path


def check_modelscope_installed():
    """检查是否安装了 modelscope"""
    try:
        import modelscope

        return True
    except ImportError:
        print("未安装 modelscope，请先执行：pip install modelscope")
        print("   或使用国内镜像：pip install modelscope -i https://mirrors.aliyun.com/pypi/simple/")
        return False


def download_from_modelscope(model_name: str, output_dir: str):
    """从魔搭社区下载模型"""
    if not check_modelscope_installed():
        return False

    print(f"从魔搭社区下载模型: {model_name}")
    print(f"保存到目录: {output_dir}")

    try:
        from modelscope import snapshot_download

        # 魔搭社区模型 ID 映射
        model_mapping = {
            "qwen2.5-coder:7b": "qwen/Qwen2.5-Coder-7B-Instruct",
            "qwen2.5:7b": "qwen/Qwen2.5-7B-Instruct",
            "deepseek-coder:6.7b": "deepseek-ai/deepseek-coder-6.7b-instruct",
            "codegeex4:9b": "THUDM/codegeex4-9b",
        }

        model_id = model_mapping.get(model_name, model_name)

        # 下载模型
        model_dir = snapshot_download(model_id, cache_dir=output_dir, revision="master")

        print(f"模型下载成功: {model_dir}")
        return True

    except Exception as e:
        print(f"下载失败: {e}")
        return False


def download_from_hf_mirror(model_name: str, output_dir: str):
    """从 HuggingFace 镜像站下载"""
    print(f"从 HuggingFace 镜像站下载模型: {model_name}")

    # 设置 HuggingFace 镜像
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    try:
        from huggingface_hub import snapshot_download

        model_mapping = {
            "qwen2.5-coder:7b": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "qwen2.5:7b": "Qwen/Qwen2.5-7B-Instruct",
            "deepseek-coder:6.7b": "deepseek-ai/deepseek-coder-6.7b-instruct",
        }

        model_id = model_mapping.get(model_name, model_name)

        model_dir = snapshot_download(
            model_id,
            cache_dir=output_dir,
            local_dir=output_dir,
        )

        print(f"模型下载成功: {model_dir}")
        return True

    except ImportError:
        print("未安装 huggingface_hub，请先执行：pip install huggingface_hub")
        return False
    except Exception as e:
        print(f"下载失败: {e}")
        return False


def download_all_models(output_dir: str):
    """下载所有必需模型"""
    models = [
        "qwen2.5-coder:7b",
        "qwen2.5:7b",
    ]

    success_count = 0
    for model in models:
        print(f"\n{'=' * 60}")
        print(f"下载模型 {success_count + 1}/{len(models)}: {model}")
        print(f"{'=' * 60}")

        if download_from_modelscope(model, output_dir):
            success_count += 1

    print(f"\n{'=' * 60}")
    print(f"下载完成: {success_count}/{len(models)} 个模型成功")
    print(f"{'=' * 60}")

    return success_count == len(models)


def main():
    parser = argparse.ArgumentParser(description="AI 模型下载工具（国内镜像）")
    parser.add_argument(
        "--source",
        choices=["modelscope", "hf-mirror"],
        default="modelscope",
        help="下载源：modelscope（魔搭社区）或 hf-mirror（HuggingFace 镜像）",
    )
    parser.add_argument("--model", default="qwen2.5-coder:7b", help="模型名称（如 qwen2.5-coder:7b）")
    parser.add_argument("--output", default="./models", help="模型保存目录")
    parser.add_argument("--all", action="store_true", help="下载所有必需模型")

    args = parser.parse_args()

    # 创建输出目录
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("灵境制造 AI 模型下载工具（国内版）")
    print("=" * 60)
    print(f"下载源: {args.source}")
    print(f"输出目录: {output_path.absolute()}")
    print()

    if args.all:
        success = download_all_models(str(output_path))
    else:
        if args.source == "modelscope":
            success = download_from_modelscope(args.model, str(output_path))
        else:
            success = download_from_hf_mirror(args.model, str(output_path))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
