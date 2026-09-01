"""Anlas（积分）消耗预估。

公式逆向自 NovelAI 官方前端，V3 / V4 / V4.5 / V5 通用（来源：
novelai-sdk docs/for-ai/anlas-calculation.md，与本机实测观察吻合：
1024×1536 @ 23 步 = 26 Anlas；832×1216 @ 23 步 Opus 免费）。

这是尽力而为的估计，官方扣费以后端为准；界面会如此标注。
"""

import math

_AREA_COEF = 2.951823174884865e-6
_STEP_AREA_COEF = 5.753298233447344e-7
_SMEA_MULT = 1.2
_SMEA_DYN_MULT = 1.4
_PER_IMAGE_CAP = 140          # 官方前端单张上限，超过视为参数过大
_MIN_PER_IMAGE = 2            # 每张计费最低 2 Anlas
_OPUS_MAX_PIXELS = 1024 * 1024
_OPUS_MAX_STEPS = 28


def estimate_cost(width, height, steps, n_samples=1, *, smea=False, smea_dyn=False,
                  strength=1.0, opus=False) -> dict:
    """估算一次生成的 Anlas 消耗。

    返回 {total, per_image, billable, opus_free, over_cap}：
    - opus_free: Opus 订阅下首张免费是否生效（面积 ≤1024² 且步数 ≤28）
    - over_cap:  单张预估超过官方 140 Anlas 上限，参数过大不会出图
    """
    area = max(1, int(width)) * max(1, int(height))
    steps = max(1, int(steps))
    n_samples = max(1, int(n_samples))
    strength = float(strength) if strength and strength > 0 else 1.0

    base = math.ceil(_AREA_COEF * area + _STEP_AREA_COEF * area * steps)
    mult = 1.4 if (smea and smea_dyn) else (1.2 if smea else 1.0)
    raw = base * mult * strength
    over_cap = raw > _PER_IMAGE_CAP
    per_image = max(math.ceil(raw), _MIN_PER_IMAGE)

    opus_free = bool(opus) and area <= _OPUS_MAX_PIXELS and steps <= _OPUS_MAX_STEPS
    billable = n_samples - 1 if opus_free else n_samples
    total = per_image * billable
    return {
        "total": total,
        "per_image": per_image,
        "billable": billable,
        "opus_free": opus_free,
        "over_cap": over_cap,
    }
