"""思路 2（零件专属先验模型）单元测试。

对应 ADR-020 第 2.8 节测试方案 / app/image_to_3d/part_prior/encoder.py 等。

覆盖（3 用例）：
1. test_vae_forward_shapes — PartPriorVAE 前向输出 shape
   - recon  (2, 1, 64, 64, 64)
   - mu     (2, 256)
   - logvar (2, 256)
2. test_vae_output_range — Sigmoid 输出值域 [0, 1]
3. test_completer_accepts_sparse_points — PartPriorCompleter 接受稀疏点云
   - 500 点 → (64, 64, 64) 稠密体素

学术诚信对齐：
- 固定随机种子 torch.manual_seed(42)，保证可复现（D-2 硬约束）
- torch 不可用时通过 pytest.importorskip 自然跳过，不注入桩模块伪装通过
- 不依赖外部预训练权重，仅验证前向链路形状与值域
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip_ci


# 固定随机种子（D-2 学术诚信硬约束：torch.manual_seed + cudnn.deterministic）
# 在每个 torch 用例内部独立设置，避免模块加载期依赖 torch。


SEED = 42


def test_vae_forward_shapes():
    """PartPriorVAE 编码器/解码器前向输出 shape 校验。

    编码器：4 层 Conv3d(stride=2) 64→32→16→8→4，展平后 fc_mu/fc_logvar 输出 latent_dim。
    解码器：5 层 ConvTranspose3d 1→4→8→16→32→64，Sigmoid 输出。
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.encoder import PartPriorVAE

    vae = PartPriorVAE(voxel_dim=64, latent_dim=256, base_channels=32)
    vae.eval()  # 推理模式，关闭 dropout（如有）

    x = torch.randn(2, 1, 64, 64, 64)
    with torch.no_grad():
        recon, mu, logvar = vae(x)

    assert recon.shape == (2, 1, 64, 64, 64), f"recon shape 不匹配：期望 (2,1,64,64,64)，实际 {tuple(recon.shape)}"
    assert mu.shape == (2, 256), f"mu shape 不匹配：期望 (2,256)，实际 {tuple(mu.shape)}"
    assert logvar.shape == (2, 256), f"logvar shape 不匹配：期望 (2,256)，实际 {tuple(logvar.shape)}"


def test_vae_output_range():
    """PartPriorVAE 解码器末端 Sigmoid 保证输出值域 [0, 1]。

    体素占据概率必须在 [0, 1]，否则下游 marching cubes 阈值化会异常。
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.encoder import PartPriorVAE

    vae = PartPriorVAE(voxel_dim=64, latent_dim=256, base_channels=32)
    vae.eval()

    x = torch.randn(1, 1, 64, 64, 64)
    with torch.no_grad():
        recon, _, _ = vae(x)

    assert recon.min().item() >= 0.0, f"recon 最小值越界：{recon.min().item()} < 0.0"
    assert recon.max().item() <= 1.0, f"recon 最大值越界：{recon.max().item()} > 1.0"


def test_completer_accepts_sparse_points():
    """PartPriorCompleter 接受稀疏点云并输出稠密体素网格。

    链路：500 点稀疏点云 → 点云体素化（64³）→ VAE 编码解码 → (64,64,64) 稠密体素。
    bbox=(100,50,20) mm 用于点云归一化到 [0, voxel_dim) 区间。
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.encoder import PartPriorCompleter, PartPriorVAE

    vae = PartPriorVAE(voxel_dim=64, latent_dim=256, base_channels=32)
    completer = PartPriorCompleter(vae, voxel_dim=64)

    # 500 个稀疏点，分布在 100×50×20 mm 的包围盒内
    points = torch.rand(500, 3) * torch.tensor([100.0, 50.0, 20.0])
    bbox = (100.0, 50.0, 20.0)

    dense_voxel = completer.complete(points, bbox)

    assert dense_voxel.shape == (64, 64, 64), f"稠密体素 shape 不匹配：期望 (64,64,64)，实际 {tuple(dense_voxel.shape)}"
    # 补全后的体素值应在 [0, 1]（VAE Sigmoid 输出）
    assert dense_voxel.min().item() >= 0.0 and dense_voxel.max().item() <= 1.0, "稠密体素值域越界 [0, 1]"
