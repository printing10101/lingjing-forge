"""Manually authored by the implementing assistant, NOT independently reviewed."""
from dataclasses import asdict

from model import Requirement, make_atlas


PROMPTS = [
    ('把标出的过渡片中心向上调 1 mm。安装接口那整片保持交付时的样子，其他过渡片可以跟着调整；可以细化表示，但先争取不增加控制网规模。',
     '这一版不许增加控制点。把目标片中心抬高 1 mm，检测截面轮廓仍要与原件重合；过渡带可以随动。',
     '只修目标片内部，中心增高 1 mm。周围各片一概不动，标注的检测截面也不能变化；需要时可增加曲面自由度。'),
    ('目标片中点上移 1 mm，装配接口整片保持原状。过渡区可联动，既有折线别磨掉；先避免增加控制点，再考虑缩小修改面数。',
     '控制网数量已冻结。中点上调 1 mm，指定检测截面继续作为原轮廓检具，其他过渡片可以动；保持原来的折线。',
     '中点提高 1 mm，只允许在目标片内消化修改；邻面和检测截面保持交付形状。可以加密，折线两侧的原切向必须保留。'),
    ('五片过渡面中，只把标注片的中心抬高 1 mm；接口片不参与调整，其他片可以联动。优先沿用现有控制网，必要时再细化。',
     '沿用现有控制网，把目标片中心调高 1 mm。检测截面不变，其余过渡片可联动，不要求最小修改面数。',
     '目标中心提高 1 mm，变化必须止于目标片边界。检测截面不能偏离，允许内部细化，不许靠改邻面来完成。'),
    ('中心加高 1 mm，安装接口与既有折线保持原样。过渡片可调整，优先不要加控制点；做不到时才考虑细化。',
     '保持控制网规模，把中心向上移 1 mm，同时让图中的基准检测截面完全保留原位置和轮廓。允许过渡面联动。',
     '中心上调 1 mm，限于目标片内部改形；所有邻面、原折线和检测截面保持不动。允许加密，不允许默默取消任何一项要求。'),
    ('四片拼接区域的目标片中心提高 1 mm。接口片保持原形，其他片可联动；可增加自由度，但优先复用现有控制网。',
     '四片区域继续使用原控制网。目标中心上移 1 mm，指定检测截面轮廓不变，允许周边面一起调整。',
     '目标片中心提高 1 mm，另外三片原样保留。指定检测截面仍要通过原来的检具，可以增加目标片自由度。'),
    ('把目标片的中部抬高 1 mm。接口片整体不变，其他过渡面可随动；保留图中折线，先不增加控制网规模。',
     '控制网不能扩容。中点抬高 1 mm，同时保持检测截面原样；过渡面可动，所有要求都必须保留。',
     '只改目标片，中点提高 1 mm。邻面、折线和基准检测截面保留原状，需要时允许加密。'),
    ('九片区域中，目标片中点增加 1 mm 高度。装配接口那片完全不动，其他过渡片允许联动；优先使用原来的控制网。',
     '不要新增控制点，把目标中心抬高 1 mm。保留标注截面的原轮廓，可以联动调整其他过渡片。',
     '九片区域里只调整目标片，中心提高 1 mm。八个邻域面都保持原样，检测截面也保持原样；可以细化目标片。'),
    ('环形区域的目标片中心提高 1 mm，安装接口整体不变。过渡带可随动，原折线保持；优先不增加控制点。',
     '环形区域控制网已冻结。中心调高 1 mm，检测截面保留原形，允许过渡带联动；孔口仍按公共边界要求固定。',
     '孔周其他曲面不参与修改，只把目标片中点提高 1 mm。检测截面与折线仍按原样保留，可以增加目标片自由度。'),
    ('孔周目标片的中心上移 1 mm。接口片保持原形，其余过渡片允许联动；先沿用已有控制网，不够时再细化。',
     '不新增控制点，目标中心提高 1 mm，检测截面保持原轮廓。允许其他过渡片配合调整，孔口位置不变。',
     '只在孔周的目标片内部增加 1 mm 中心高度。其余片和检测截面不变，允许细化目标片。'),
    ('环形过渡区中心提高 1 mm。固定安装接口和原有折线，其他面可随动；先减少新增控制点，再减少影响面数。',
     '环形控制网保持原规模。标注片的中点提高 1 mm，指定检测截面轮廓保持原样；必要时联动其他过渡片。',
     '标注片中点抬高 1 mm，修改不得传播到邻面。检测截面和折线不能变，可以加密目标片但不能省略约束。'),
]

COMMON_CONTEXT = ('所有外边界（包括孔口）保持原位置。未标为锐边的接缝按匹配物理参数 C2 验收；'
                  '锐边的位置和两侧原切向保留。修改沿全局 z 方向。允许细化时最多新增 105 个控制点。')


def public_geometry(atlas):
    tile = atlas.tiles[atlas.target]
    return dict(case_id=atlas.case_id, family=atlas.family,
                faces=[dict(face_id=t.face_id, cell=list(t.cell)) for t in atlas.tiles],
                target_face=tile.face_id, installation_interface_face=atlas.tiles[atlas.interface].face_id,
                datum=dict(face_id=tile.face_id, parameter_axis='u' if atlas.datum_axis == 0 else 'v',
                           parameter_value=atlas.datum_parameter,
                           physical_x_mm=float(tile.point(atlas.datum_parameter, .5)[0])),
                target_point_xyz_mm=tile.point(.5, .5).tolist(),
                sharp_edges=[[atlas.tiles[i].face_id, atlas.tiles[j].face_id] for i, j in sorted(atlas.sharp_seams)],
                common_context=COMMON_CONTEXT)


def task_set():
    tasks = []
    for index, prompts in enumerate(PROMPTS):
        atlas = make_atlas(index)
        for variant, prompt in enumerate(prompts):
            requirement = Requirement(1., variant != 1, variant != 2, variant == 0,
                                      variant != 0, 'complexity' if variant != 2 else 'locality',
                                      0 if variant == 1 else 105)
            tasks.append(dict(task_id=f'{atlas.case_id}_{"ABC"[variant]}', case_id=atlas.case_id,
                              input_text=prompt, oracle_requirement=asdict(requirement),
                              annotation_basis=dict(target='prompt + target_face in public geometry',
                                  lock_interface='whole installation_interface_face for A',
                                  datum='public datum trace for B/C, not a hidden coordinate',
                                  continuity='common_context, with sharp-edge exception'),
                              independent_human_review='pending',
                              authored_by='implementing_assistant'))
    return tasks
