"""Only the retained balanced AFF-Net model can be used for new inference."""
SUPPORTED_MODELS = ('affnet_mixed',)

def model_family(model_id):return 'AFF-Net' if str(model_id).startswith('affnet_') else 'FAZE'

def get_tracker(model_id,cfg):
    if model_id not in SUPPORTED_MODELS:
        raise ValueError(f'本版本未安装此记录使用的模型 {model_id}，不能重新推理。已有坐标可继续查看或生成图和回放。')
    from affnet_tracker import AffnetTracker
    return AffnetTracker(model_id,cfg)
