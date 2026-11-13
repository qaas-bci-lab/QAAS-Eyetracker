"""AFF-Net TorchScript inference + application-level 2D personal calibration (not MAML)."""
from pathlib import Path
import json,hashlib,io,re,threading
import numpy as np
import cv2,torch
from app_paths import RESOURCE_ROOT
ROOT=RESOURCE_ROOT
_THREAD_LOCK=threading.Lock()
_threads_configured=False


def configure_torch_threads(threads=4):
    """Configure once before starting a batch, never from its concurrent records."""
    global _threads_configured
    if isinstance(threads,bool) or not isinstance(threads,int) or threads<1:
        raise ValueError('CPU线程数必须为正整数')
    with _THREAD_LOCK:
        if torch.get_num_threads()!=threads:torch.set_num_threads(threads)
        _threads_configured=True


def _ensure_default_threads():
    global _threads_configured
    with _THREAD_LOCK:
        if not _threads_configured:
            torch.set_num_threads(max(1,min(4,torch.get_num_threads())))
            _threads_configured=True


def inference_device(value='cpu'):
    name=str(value).strip().lower()
    if re.fullmatch(r'cpu|cuda(?::[0-9]+)?',name) is None:
        raise ValueError('推理设备应为 cpu、cuda 或 cuda:编号')
    device=torch.device(name)
    if device.type=='cuda':
        if not torch.cuda.is_available():
            detail='当前运行环境未包含CUDA版PyTorch' if torch.version.cuda is None else '未检测到可用CUDA显卡或驱动'
            raise RuntimeError(detail+'，请选择CPU或安装GPU处理环境')
        if device.index is not None and device.index>=torch.cuda.device_count():
            raise ValueError('指定的CUDA显卡编号不存在')
    return device

def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def to_screen(camera_cm,cfg):
    x,y=np.asarray(camera_cm,dtype=float)
    if not np.isfinite([x,y]).all():raise ValueError('AFF-Net输出无效')
    return np.array([((x*10+cfg['camera_x_mm'])/cfg['screen_width_mm']+.5)*cfg['physical_width'],(-y*10-cfg['camera_y_mm'])/cfg['screen_height_mm']*cfg['physical_height']])

def _clip_box(x0,y0,x1,y1,w,h):
    b=tuple(map(int,[max(0,np.floor(x0)),max(0,np.floor(y0)),min(w,np.ceil(x1)),min(h,np.ceil(y1))]))
    if b[2]-b[0]<4 or b[3]-b[1]<4:raise ValueError('人脸或眼睛区域太小')
    return b

def boxes_from_landmarks(landmarks,w,h):
    pts=np.array([[p.x*w,p.y*h] for p in landmarks],dtype=float)
    if len(pts)<468 or not np.isfinite(pts).all():raise ValueError('人脸关键点无效')
    low=pts[:468].min(0);high=pts[:468].max(0);extent=high-low
    face=_clip_box(low[0]-.05*extent[0],low[1]-.1*extent[1],high[0]+.05*extent[0],high[1]+.05*extent[1],w,h);eyes=[]
    # Anatomical left is image-right in an unmirrored camera frame.
    for ids in [(362,263),(33,133)]:
        a,b=pts[list(ids)];c=(a+b)/2;s=max(8.,np.linalg.norm(b-a)*1.8)
        eyes.append(_clip_box(c[0]-s/2,c[1]-.36*s,c[0]+s/2,c[1]+.36*s,w,h))
    return face,eyes[0],eyes[1]

def make_inputs(frame,boxes):
    h,w=frame.shape[:2];images=[];rects=[]
    for i,(b,size) in enumerate(zip(boxes,[224,112,112])):
        x0,y0,x1,y1=b
        if not(0<=x0<x1<=w and 0<=y0<y1<=h):raise ValueError('人脸或眼睛框超出图像')
        rgb=cv2.cvtColor(cv2.resize(frame[y0:y1,x0:x1],(size,size)),cv2.COLOR_BGR2RGB)
        if i==2:rgb=cv2.flip(rgb,1)
        images.append(torch.from_numpy(rgb.transpose(2,0,1).copy()).float().unsqueeze(0)/255)
        rects.extend([(x1-x0)/w,(y1-y0)/h,x0/w,y0/h])
    return images[1],images[2],images[0],torch.tensor([rects],dtype=torch.float32)

class AffnetTracker:
    model_family='AFF-Net'
    calibration_method='personal_screen_affine'
    def __init__(self,model_id,cfg):
        import mediapipe as mp
        self.cfg=cfg;self.model_id=model_id;self.mp=mp;self.head=None;self.affine=None;self.initial_head=None;d=ROOT/'models'/model_id
        if not (d/'model_cpu.ts').is_file():raise ValueError('AFF-Net权重尚未就绪')
        item=next(r for r in json.loads((ROOT/'models/manifest.json').read_text(encoding='utf8')) if r['id']==model_id)
        for f in item['files'].values():
            if _sha(ROOT/f['path'])!=f['sha256']:raise ValueError('AFF-Net模型校验失败')
        self.model_hash=_sha(d/'backbone.pt');self.runtime_hash=_sha(d/'model_cpu.ts')
        _ensure_default_threads()
        self.device=inference_device(cfg.get('inference_device','cpu'))
        # The native TorchScript fopen does not handle all Windows Unicode paths.
        # Frozen TorchScript tensors are constants, so module.to() cannot move them.
        try:
            self.net=torch.jit.load(io.BytesIO((d/'model_cpu.ts').read_bytes()),map_location=self.device).eval()
        except RuntimeError as error:
            raise RuntimeError(f'AFF-Net无法加载到{self.device}：{error}') from error
        opts=mp.tasks.vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_buffer=(ROOT/'models/face_landmarker.task').read_bytes()),running_mode=mp.tasks.vision.RunningMode.IMAGE,num_faces=1,min_face_detection_confidence=.5,min_face_presence_confidence=.5)
        self.landmarker=mp.tasks.vision.FaceLandmarker.create_from_options(opts)
        self.points3=np.array([[0,0,0],[0,63.6,-12.5],[-43.3,-32.7,-26],[43.3,-32.7,-26],[-28.9,28.9,-24.1],[28.9,28.9,-24.1]],np.float64)
    def infer(self,bgr):
        result=self.infer_batch([bgr])[0]
        if isinstance(result,ValueError):raise result
        return result
    def _prepare(self,bgr):
        if bgr is None or not getattr(bgr,'size',0):raise ValueError('对应视频帧缺失或读取失败')
        h,w=bgr.shape[:2];rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);out=self.landmarker.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb))
        if not out.face_landmarks:raise ValueError('未检测到人脸')
        lm=out.face_landmarks[0]
        return make_inputs(bgr,boxes_from_landmarks(lm,w,h)),lm,w,h
    def infer_batch(self,frames):
        """One result per input in order; invalid frames retain their error slot."""
        if len(frames)>64:raise ValueError('单批推理最多64帧')
        results=[None]*len(frames);prepared=[]
        for index,frame in enumerate(frames):
            try:prepared.append((index,self._prepare(frame)))
            except ValueError as error:results[index]=error
        if not prepared:return results
        try:
            inputs=[torch.cat([item[1][0][part] for item in prepared],dim=0).to(
                        device=self.device,dtype=torch.float32) for part in range(4)]
            with torch.inference_mode():predictions=self.net(*inputs).cpu().numpy()
        except RuntimeError as error:
            if self.device.type=='cuda':
                raise RuntimeError('CUDA推理失败，请减小批量或并行数并检查显存：'+str(error)) from error
            raise
        if predictions.shape!=(len(prepared),2):raise ValueError('AFF-Net输出形状无效')
        for prediction,(index,(_,landmarks,width,height)) in zip(predictions,prepared):
            try:results[index]=self._finish(prediction,landmarks,width,height)
            except ValueError as error:results[index]=error
        return results
    def _finish(self,pred,lm,w,h):
        if pred.shape!=(2,):raise ValueError('AFF-Net输出形状无效')
        raw=to_screen(pred,self.cfg);xy=raw.copy()
        if self.affine is not None:xy=np.r_[raw/[self.cfg['physical_width'],self.cfg['physical_height']],1.]@self.affine
        if self.cfg.get('camera_matrix'):
            K=np.array(self.cfg['camera_matrix'],float);size=self.cfg.get('intrinsics_resolution',[w,h]);K[0]*=w/size[0];K[1]*=h/size[1];dist=np.array(self.cfg.get('distortion',[0]*5),float)
        else:
            f=w/(2*np.tan(np.deg2rad(self.cfg.get('fov_degrees',60))/2));K=np.array([[f,0,w/2],[0,f,h/2],[0,0,1.]]);dist=np.zeros(5)
        pts=np.array([[lm[i].x*w,lm[i].y*h] for i in [1,152,33,263,61,291]],float);ok,r,t=cv2.solvePnP(self.points3,pts,K,dist,flags=cv2.SOLVEPNP_EPNP)
        if not ok or t[2,0]<=0:raise ValueError('人脸姿态估计失败')
        r,t=cv2.solvePnPRefineLM(self.points3,pts,K,dist,r,t);R=cv2.Rodrigues(r)[0];origin=R@np.array([0,-32.7,-26.])+t.ravel()
        reproj=float(np.sqrt(np.mean((cv2.projectPoints(self.points3,r,t,K,dist)[0].reshape(-1,2)-pts)**2)))
        if reproj>max(w,h)*.06:raise ValueError('人脸姿态重投影误差过大')
        pnp_depth=float(origin[2])
        if self.cfg.get('distance_mode')=='fixed':origin*=float(self.cfg.get('view_distance_cm',55))*10/origin[2]
        if not np.isfinite(xy).all() or origin[2]<=0:raise ValueError('预测位置或距离无效')
        return dict(x=float(xy[0]),y=float(xy[1]),raw=raw,camera_xy_cm=pred,z=np.zeros(6,np.float32),origin=origin,R=R,distance_mm=float(origin[2]),distance_mm_pnp=pnp_depth,reprojection_px=reproj,model_family='AFF-Net')
    def calibrate(self,samples,use_maml=False,*,on_progress=None):
        if on_progress:on_progress(0,'拟合屏幕校准…')
        groups={}
        for s in samples:groups.setdefault(s['target_id'],[]).append(s)
        if len(groups) not in (5,9) or any(len(v)<5 for v in groups.values()):raise ValueError('每个校准点至少需要5帧有效预测')
        X=np.array([np.r_[np.median([s['raw'] for s in group],axis=0)/[self.cfg['physical_width'],self.cfg['physical_height']],1.] for group in groups.values()]);Y=np.array([g[0]['target'] for g in groups.values()],float)
        if not np.isfinite(X).all() or np.linalg.matrix_rank(X)<3:raise ValueError('校准数据退化，请重新校准')
        self.affine=np.linalg.solve(X.T@X+np.diag([1e-5,1e-5,0]),X.T@Y);fit=np.linalg.norm(X@self.affine-Y,axis=1)
        if on_progress:on_progress(100,'校准完成')
        return dict(target_count=len(groups),valid_frames=len(samples),head_adapted=False,affine=self.affine.tolist(),fit_mean_px=float(fit.mean()),model_sha256=self.model_hash,runtime_sha256=getattr(self,'runtime_hash',None),model_family='AFF-Net',calibration_method='personal_screen_affine',note='Application-level affine calibration, not MAML. Fit error is not independent test accuracy.')
    def close(self):self.landmarker.close()
