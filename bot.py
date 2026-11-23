#!/usr/bin/env python3
"""Звёздное провидение: локальный ADB-бот. Ctrl+C останавливает."""
import argparse, subprocess, time, json, io, math, os, re, struct, ctypes, sys, tempfile
from contextlib import contextmanager
from pathlib import Path
from functools import lru_cache

if sys.platform == "win32":
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl

import cv2
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parent
PACKAGE="com.global.sgdbzseaeu"
# Правила режима: плитки уровня CAP (2048) не сливаются; цель — GOAL_COUNT таких плиток.
CAP=11
GOAL_COUNT=3
OCR_TIMEOUT=3.0
STALE_READ_LIMIT=6
OBSERVATION_ERRORS=(RuntimeError, OSError, ValueError, struct.error,
                    subprocess.TimeoutExpired, subprocess.CalledProcessError)

class CycleMetrics:
    """Exclusive stage times: nested OCR is excluded from recognition time."""
    def __init__(self):
        self.started=time.monotonic()
        self.seconds={}
        self.counts={"captures":0,"ocr_calls":0,"full_frame_fallbacks":0,"observations":0}
        self.stack=[]
        self.first_capture_at=None
        self.frame_size=None

    def call(self,name,fn,*args,**kwargs):
        frame=[time.monotonic(),0.0]
        self.stack.append(frame)
        try:return fn(*args,**kwargs)
        finally:
            elapsed=time.monotonic()-frame[0]
            self.stack.pop()
            self.seconds[name]=self.seconds.get(name,0.0)+max(0.0,elapsed-frame[1])
            if self.stack:self.stack[-1][1]+=elapsed

    def snapshot(self,outcome):
        total=time.monotonic()-self.started
        return {"outcome":outcome,"total_s":round(total,6),
                "seconds":{k:round(v,6) for k,v in self.seconds.items()},
                "other_s":round(max(0.0,total-sum(self.seconds.values())),6),
                "counts":dict(self.counts)}

def _timed(metrics,name,fn,*args,**kwargs):
    return metrics.call(name,fn,*args,**kwargs) if metrics is not None else fn(*args,**kwargs)

def _save_failed_observation(data,metrics=None):
    try:
        _timed(metrics,"diagnostic_write",(ROOT/"failed-observation.bin").write_bytes,data)
    except OSError as exc:
        if metrics is not None:
            metrics.counts["diagnostic_write_errors"]=metrics.counts.get("diagnostic_write_errors",0)+1
        print(json.dumps({"diagnostic_error":str(exc),"artifact":"failed-observation.bin",
                          "action":"continue_observation"},ensure_ascii=False),flush=True)

def _ocr(canvas,psm,metrics=None,whitelist=None):
    png=io.BytesIO()
    canvas.save(png,format="PNG")
    command=["tesseract","stdin","stdout","--psm",str(psm),"-l","eng"]
    if whitelist is None and psm==13:whitelist="0123456789"
    if whitelist:command.extend(["-c",f"tessedit_char_whitelist={whitelist}"])
    if metrics is not None:metrics.counts["ocr_calls"]+=1
    try:
        result=_timed(metrics,"ocr",subprocess.run,command,input=png.getvalue(),
                      capture_output=True,check=True,timeout=OCR_TIMEOUT)
        return result.stdout.decode("utf-8",errors="replace").strip()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OCR превысил лимит {OCR_TIMEOUT:g} с") from exc
    except (subprocess.CalledProcessError,OSError) as exc:
        raise RuntimeError(f"Ошибка OCR: {exc}") from exc

def mask(im,r,c,raw=False):
    # Геометрия при 1440x3040; координаты нормализованы в read_board.
    x,y=361+310*c,1188+310*r
    a=np.array(im.crop((x-34,y-24,x+35,y+31)).convert("RGB"))
    a=((a.min(axis=2)>175)&(a[:,:,0].astype(int)>=a[:,:,2].astype(int)-8)).astype(np.uint8)*255
    a=cv2.morphologyEx(a,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,labels,stats,centers=cv2.connectedComponentsWithStats(a)
    if count<2:return None
    # x<53 отсекает правую дугу светлой рамки кружка значка (на 7/8 она попадает в x≈59..60).
    candidates=[i for i in range(1,count) if 8<centers[i,0]<53 and 3<centers[i,1]<48 and stats[i,cv2.CC_STAT_AREA]>=12]
    if not candidates:return None
    largest=max(candidates,key=lambda i:stats[i,cv2.CC_STAT_AREA])
    areas=stats[:,cv2.CC_STAT_AREA]
    keep=[i for i in candidates if areas[i]>=max(12,areas[largest]*.20)]
    a=np.isin(labels,keep).astype(np.uint8)*255
    ys,xs=np.where(a>0)
    if len(xs)<20 or ys.max()-ys.min()<21:return None
    crop=a[ys.min():ys.max()+1,xs.min():xs.max()+1]
    return crop if raw else cv2.resize(crop,(32,40))

def templates():
    a=Image.open(ROOT/"calibration.png")
    s=Image.open(ROOT/"fixtures/saved-board.png")
    f=Image.open(ROOT/"fixtures/icon-three-vs-four.png")
    return {1:mask(a,0,0),2:mask(a,1,0),3:mask(a,3,0),4:mask(a,2,1),5:mask(a,3,1),
            6:mask(f,3,0),7:mask(s,3,0),8:mask(f,2,0),9:mask(f,1,0)}
TEMPLATES=templates()
BASE_TEMPLATES=frozenset(TEMPLATES)

def glyphs(im,r,c):
    a=mask(im,r,c,raw=True)
    if a is None:return []
    columns=np.where(a.any(axis=0))[0]
    groups=np.split(columns,np.where(np.diff(columns)>1)[0]+1)
    result=[]
    for group in groups:
        part=a[:,group.min():group.max()+1]
        ys,xs=np.where(part>0)
        if ys.max()-ys.min()<21:return []
        result.append(cv2.resize(part[ys.min():ys.max()+1,:],(32,40)))
    return result

# The two glyphs from an actual level-10 screen calibrate the smaller font.
TEN_GLYPHS=glyphs(Image.open(ROOT/"fixtures/level-ten.png"),0,0)
DIGITS={i:TEMPLATES[i] for i in range(1,10)}
DIGITS[1],DIGITS[0]=TEN_GLYPHS

def two_digit_level(im,r,c):
    parts=glyphs(im,r,c)
    if len(parts)!=2:return None
    error=lambda a,b:float(np.mean(np.abs(a.astype(float)-b.astype(float)))/255)
    if error(parts[0],DIGITS[1])>.25:return None
    errors={i:error(parts[1],template) for i,template in DIGITS.items()}
    suffix=min(errors,key=errors.get)
    if errors[suffix]>.28:raise RuntimeError("Не распознана вторая цифра двузначного уровня")
    return 10+suffix

def decode_screen(data):
    if data.startswith(b"STRP"):
        w,h,rows,n=struct.unpack_from("<IIII",data,4)
        im=Image.new("RGB",(w,h));offset=20
        for _ in range(n):
            y=struct.unpack_from("<I",data,offset)[0];offset+=4
            length=w*rows*4
            im.paste(Image.frombytes("RGBA",(w,rows),data[offset:offset+length]).convert("RGB"),(0,y))
            offset+=length
        return im
    if data.startswith(b"CROP"):
        w,h,y,rows=struct.unpack_from("<IIII",data,4)
        im=Image.new("RGB",(w,h))
        im.paste(Image.frombytes("RGBA",(w,rows),data[20:]).convert("RGB"),(0,y))
        return im
    if data.startswith(b"\x89PNG"):
        return Image.open(io.BytesIO(data)).convert("RGB")
    w,h,fmt=struct.unpack_from("<III",data)
    if not (100<=w<=5000 and 100<=h<=5000) or fmt not in (1,2):
        raise RuntimeError("Неподдерживаемый формат снимка ADB")
    size=w*h*4;offset=len(data)-size
    if offset not in (12,16):raise RuntimeError("Повреждённый кадр ADB")
    return Image.frombytes("RGBA",(w,h),data[offset:]).convert("RGB")

def icon_patch(im,r,c,half):
    x,y=255+310*c,1285+310*r
    return np.array(im.crop((x-half,y-half,x+half,y+half)).convert("L").resize((half,half)))

def _icon_refs():
    cal=Image.open(ROOT/"calibration.png")
    ic34=Image.open(ROOT/"fixtures/icon-three-vs-four.png")
    refs={level:icon_patch(cal,r,c,64) for level,r,c in ((1,0,0),(2,1,0),(3,3,0),(4,2,1),(5,3,1))}
    refs.update({level:icon_patch(ic34,r,c,64) for level,r,c in ((6,3,0),(7,0,1),(8,2,0),(9,1,0))})
    return refs

ICON_REFS=_icon_refs()

def icon_level(im,r,c):
    patch=icon_patch(im,r,c,90)
    if float(patch.std())<5:return None # badge-only capture has no icon pixels
    scores={level:float(cv2.matchTemplate(patch,ref,cv2.TM_CCOEFF_NORMED).max())
            for level,ref in ICON_REFS.items()}
    ranked=sorted(scores,key=scores.get,reverse=True)
    best,second=ranked[:2]
    return best if scores[best]>=.94 and scores[best]-scores[second]>=.12 else None

def read_board(data,metrics=None):
    im=decode_screen(data)
    if metrics is not None:metrics.frame_size=im.size
    im=im.resize((1440,3040))
    # Борд должен быть виден; интерфейс проверяется до каждого жеста.
    board=[]
    for r in range(4):
        for c in range(4):
            icon=icon_level(im,r,c)
            if icon is not None:board.append(icon);continue
            double=two_digit_level(im,r,c)
            if double is not None:board.append(double);continue
            m=mask(im,r,c)
            if m is None:board.append(0);continue
            errors={v:np.mean(np.abs(m.astype(float)-t.astype(float)))/255 for v,t in TEMPLATES.items()}
            v=min(errors,key=errors.get)
            ranked=sorted(errors.values())
            if errors[v]>.18 or ranked[1]-ranked[0]<.06:
                pic=Image.fromarray(255-m).resize((128,160))
                canvas=Image.new("L",(168,200),255);canvas.paste(pic,(20,20))
                result=_ocr(canvas,6,metrics)
                if not result.isdigit():
                    result=_ocr(canvas,13,metrics)
                if not result.isdigit() or not 1<=int(result)<=20:raise RuntimeError(f"Не распознан уровень ({r},{c}): {result!r}, ошибка {errors[v]:.2f}")
                v=int(result)
                if v not in TEMPLATES:TEMPLATES[v]=m
            board.append(v)
    if not any(board):raise RuntimeError("Поле не найдено")
    return tuple(board)

def read_energy(data,metrics=None):
    try:
        im=data if isinstance(data,Image.Image) else decode_screen(data)
        if not hasattr(im,"resize") or not hasattr(im,"crop"):return None
        crop=im.resize((1440,3040)).crop((630,1035,815,1105))
        arr=np.array(crop.convert("RGB"))
        if float(arr.std())<5:return None
        bw=(arr.max(axis=2)>140).astype(np.uint8)*255
        inv=Image.fromarray(255-bw).resize((370,140))
        text=_ocr(inv,7,metrics,whitelist="0123456789/")
    except OBSERVATION_ERRORS:
        return None
    m=re.match(r"^(\d+)/(\d+)$",text)
    if not m:return None
    cur,cap=int(m.group(1)),int(m.group(2))
    return (cur,cap) if 0<=cur<=cap<=9999 and cap>0 else None

def read_observation(capture,full_capture,full_mode=False,metrics=None):
    """One acquisition+decode attempt, with at most one full-frame fallback."""
    data=None
    captured=None
    def acquire(full):
        nonlocal captured
        if metrics is not None:metrics.counts["captures"]+=1
        frame=_timed(metrics,"capture",full_capture if full else capture)
        if captured is None:captured=time.monotonic()
        if metrics is not None and metrics.first_capture_at is None:
            metrics.first_capture_at=captured
        return frame
    def recognize(frame):
        if metrics is None:return read_board(frame)
        metrics.frame_size=None
        return metrics.call("recognize",read_board,frame,metrics=metrics)
    try:
        data=acquire(full_mode)
        return recognize(data),data,full_mode,captured
    except OBSERVATION_ERRORS as exc:
        if data is not None:
            _save_failed_observation(data,metrics)
        if full_mode:raise RuntimeError(f"Не удалось прочитать полный кадр: {exc}") from exc
        if metrics is not None:metrics.counts["full_frame_fallbacks"]+=1
        print(json.dumps({"observation_error":str(exc),"action":"retry_full_frame"},ensure_ascii=False),flush=True)
        full=None
        try:
            full=acquire(True)
            return recognize(full),full,True,captured
        except OBSERVATION_ERRORS as full_exc:
            if full is not None:
                _save_failed_observation(full,metrics)
            raise RuntimeError(f"Не удалось прочитать полный кадр после {exc}: {full_exc}") from full_exc

def read_with_fallback(data,full_capture):
    board,frame,used_full,_=read_observation(lambda:data,full_capture)
    return board,frame,used_full

def ensure_game_focus(adb):
    focus=adb("shell","dumpsys","window").decode(errors="replace")
    if not any(PACKAGE in line for line in focus.splitlines() if "mCurrentFocus" in line):
        raise RuntimeError("Игра не в фокусе")

def move(board,d):
    out=list(board)
    for n in range(4):
        ids=([4*n+i for i in range(4)] if d<2 else [4*i+n for i in range(4)])
        if d in (1,3):ids=ids[::-1]
        vals=[board[i] for i in ids if board[i]]; result=[];i=0
        while i<len(vals):
            if i+1<len(vals) and vals[i]==vals[i+1] and vals[i]<CAP:result.append(vals[i]+1);i+=2
            else:result.append(vals[i]);i+=1
        result += [0]*(4-len(result))
        for idx,v in zip(ids,result):out[idx]=v
    return tuple(out)

def valid_successor(expected,actual):
    changed=[i for i in range(16) if expected[i]!=actual[i]]
    return len(changed)==1 and expected[changed[0]]==0 and 1<=actual[changed[0]]<=min(15,max(4,max(expected)))

def observation_ready(current,previous,expected,last):
    if current!=previous:return False
    if expected is None:return True
    return current!=last and valid_successor(expected,current)

def send_move(adb,xy):
    x1,y1,x2,y2=xy
    # Exactly one MOVE event, avoiding multiple touch-move callbacks per swipe.
    command=(f"input motionevent DOWN {x1} {y1}; "
             f"input motionevent MOVE {x2} {y2}; "
             f"input motionevent UP {x2} {y2}")
    adb("shell",command)

def reached_goal(b):
    return b.count(CAP)>=GOAL_COUNT

def legal_moves(b):
    # Плитки CAP не сливаются, поэтому потерять уже собранную CAP нельзя.
    return [(d,move(b,d)) for d in range(4) if move(b,d)!=b]

def heuristic(b):
    if reached_goal(b):return 1e9
    empty=b.count(0); smooth=0;mono=0
    for r in range(4):
        for c in range(4):
            i=r*4+c
            for j in ([i+1] if c<3 else [])+([i+4] if r<3 else []):
                if b[i] and b[j]:smooth-=abs(b[i]-b[j])
    for lines in ([[b[r*4+c] for c in range(4)] for r in range(4)],[[b[r*4+c] for r in range(4)] for c in range(4)]):
        for line in lines:
            mono+=max(sum(min(0,a-z) for a,z in zip(line,line[1:])),sum(min(0,z-a) for a,z in zip(line,line[1:])))
    top=max(b);corner=max(b[i] for i in (0,3,12,15))
    return empty*28+smooth*2+mono*5+corner*5+(12 if corner==top else 0)

@lru_cache(maxsize=50000)
def search(b,depth,chance):
    if depth==0:return heuristic(b)
    if chance:
        cells=[i for i,v in enumerate(b) if not v]
        if not cells:return search(b,depth-1,False)
        total=0
        for i in cells:
            a=list(b);a[i]=1;total+=search(tuple(a),depth-1,False)
        return total/len(cells)
    options=[v for _,v in legal_moves(b)]
    return max((search(v,depth-1,True) for v in options),default=-100000)

def choose_legacy(b):
    search.cache_clear()
    legal=legal_moves(b)
    depth=3 if b.count(0)>=6 else 5
    return max(legal,key=lambda item:search(item[1],depth,True))[0] if legal else None

ENGINE=ROOT/"vendor"/{"darwin":"engine.dylib","win32":"engine.dll"}.get(sys.platform,"engine.so")
ENGINE_BUILD=r"vendor\build-windows.cmd" if sys.platform=="win32" else "sh vendor/build.sh"
ENGINE_API=2
REQUIRED_SYMBOLS=("engine_api_version","get_cap_rank","get_goal_count","solve_budget","set_spawn_table",
                  "get_spawn_prob","score_levels_thresh","init_tables","execute_move","goal_levels")

def check_engine(lib,name=ENGINE.name):
    # До настройки argtypes: у старого движка нет новых функций, и ctypes упал бы с AttributeError.
    missing=[n for n in REQUIRED_SYMBOLS if not hasattr(lib,n)]
    if missing:
        raise RuntimeError(f"Движок {name} собран из старой версии (нет {', '.join(missing)}): пересоберите {ENGINE_BUILD}")
    if lib.engine_api_version()!=ENGINE_API or (lib.get_cap_rank(),lib.get_goal_count())!=(CAP,GOAL_COUNT):
        raise RuntimeError(f"Движок {name} собран из другой версии: пересоберите {ENGINE_BUILD}")

def load_engine(path=ENGINE):
    if not path.exists():raise RuntimeError(f"Движок не собран: выполните {ENGINE_BUILD} ({path.name})")
    try:lib=ctypes.CDLL(str(path))
    except OSError as exc:raise RuntimeError(f"Не удалось загрузить {path.name}: {exc}") from exc
    check_engine(lib,path.name)
    lib.solve_levels.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int]
    lib.solve_levels.restype=ctypes.c_int
    lib.solve_budget.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int,ctypes.POINTER(ctypes.c_float),ctypes.c_int,ctypes.c_double,ctypes.c_double,ctypes.POINTER(ctypes.c_double)]
    lib.solve_budget.restype=ctypes.c_int
    if hasattr(lib,"solve_budget_progressive"):
        lib.solve_budget_progressive.argtypes=lib.solve_budget.argtypes
        lib.solve_budget_progressive.restype=ctypes.c_int
    lib.execute_move.argtypes=[ctypes.c_int,ctypes.c_uint64]
    lib.execute_move.restype=ctypes.c_uint64
    lib.set_spawn_probs.argtypes=[ctypes.c_float]*3
    lib.set_spawn_probs.restype=None
    lib.set_spawn_vector.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_int]
    lib.set_spawn_vector.restype=None
    lib.set_spawn_table.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_int,ctypes.c_int]
    lib.set_spawn_table.restype=ctypes.c_int
    lib.get_spawn_prob.argtypes=[ctypes.c_int,ctypes.c_int]
    lib.get_spawn_prob.restype=ctypes.c_float
    lib.score_levels.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int]
    lib.score_levels.restype=ctypes.c_float
    lib.score_levels_thresh.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int,ctypes.c_float]
    lib.score_levels_thresh.restype=ctypes.c_float
    lib.goal_levels.argtypes=[ctypes.c_uint64]
    lib.goal_levels.restype=ctypes.c_int
    lib.init_tables()
    return lib

try:NATIVE=load_engine()
except RuntimeError as exc:
    if __name__=="__main__":sys.exit(str(exc))
    raise
DIRECTIONS=(2,3,0,1) # Python L,R,U,D -> upstream U,D,L,R

# Поиск: проходы с ослабляемым отсечением, пока укладываются в бюджет времени.
DEFAULT_DEPTH=8
DEFAULT_THINK=10.0
THRESHOLDS=(1e-4,1e-5,1e-6)
# Бюджет жёсткий: каждый проход прерывается дедлайном. Если не завершился ни один,
# используется запасной ход глубины 1 (считается за микросекунды до проходов).
# Следующий проход не начинается, если даже при росте в GROWTH раз он не успеет до дедлайна.
# Замеренный рост 6–14 раз; берём оптимистичные 5: если проход не успеет, дедлайн прервёт его,
# и останется результат предыдущего прохода.
GROWTH=5.0

# Модель появлений по стадиям (стадия = максимальный уровень после хода).
SPAWN_DEFAULT=ROOT/"spawn-model.default.json"
SPAWN_LEARNED=ROOT/"spawn-model.json"
SPAWN_MODEL_VERSION=3
SPAWN_PRIOR_WEIGHT=20.0

def default_spawn_model():
    return json.loads(SPAWN_DEFAULT.read_text(encoding="utf-8"))

def load_spawn_model():
    if SPAWN_LEARNED.exists():
        data=json.loads(SPAWN_LEARNED.read_text(encoding="utf-8"))
        if data.get("version")==SPAWN_MODEL_VERSION:return data
        print(json.dumps({"spawn_model":"устаревший формат spawn-model.json проигнорирован; используется spawn-model.default.json"},ensure_ascii=False),flush=True)
    return default_spawn_model()

def stage_row(model,stage):
    keys=sorted(int(k) for k in model["stages"])
    key=max([k for k in keys if k<=stage],default=keys[0])
    return (list(model["stages"][str(key)])+[0]*15)[:15]

def _bounded_stage_prior(stage,base_model=None):
    base=default_spawn_model() if base_model is None else base_model
    row=stage_row(base,stage)
    total=sum(row)
    if total>SPAWN_PRIOR_WEIGHT:
        return [round(v*SPAWN_PRIOR_WEIGHT/total,4) if v else 0 for v in row]
    return list(row)

def spawn_table(model):
    return [stage_row(model,s) for s in range(16)]

def apply_spawn_model(model):
    flat=[float(v) for row in spawn_table(model) for v in row]
    if NATIVE.set_spawn_table((ctypes.c_float*len(flat))(*flat),16,15)!=0:
        raise RuntimeError("Некорректная модель появлений")

def record_spawn(model,stage,level):
    key=str(stage)
    if key not in model["stages"]:
        model["stages"][key]=_bounded_stage_prior(stage)
    row=model["stages"][key]
    row+=[0]*(15-len(row))
    cur=row[level-1]
    row[level-1]=round(cur+1,4) if isinstance(cur,float) else cur+1
    model["version"]=SPAWN_MODEL_VERSION

def format_spawn_model(model):
    head="".join(f" {json.dumps(k)}: {json.dumps(v,ensure_ascii=False)},\n" for k,v in model.items() if k!="stages")
    rows=",\n".join(f'  "{k}": {json.dumps(model["stages"][k])}' for k in sorted(model["stages"],key=int))
    return "{\n"+head+' "stages": {\n'+rows+"\n }\n}\n"

def save_spawn_model(model):
    model["version"]=SPAWN_MODEL_VERSION
    text=format_spawn_model(model)
    SPAWN_LEARNED.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=SPAWN_LEARNED.parent,
                                         prefix=SPAWN_LEARNED.name+".",suffix=".tmp",delete=False) as f:
            temporary=Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary,SPAWN_LEARNED)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

def pack(b):
    if any(not 0<=v<=15 for v in b):raise RuntimeError("Уровень вне диапазона движка")
    return sum(v<<(4*i) for i,v in enumerate(b))

def choose(b,depth=DEFAULT_DEPTH,think=DEFAULT_THINK,info=None,policy="legacy"):
    if policy not in ("legacy","progressive"):raise ValueError("Неизвестная политика поиска: "+str(policy))
    if policy=="progressive" and depth<1:raise ValueError("Глубина progressive должна быть положительной")
    legal=legal_moves(b)
    if not legal:return None
    allowed=sum(1<<DIRECTIONS[d] for d,_ in legal)
    progressive=policy=="progressive"
    if progressive and not hasattr(NATIVE,"solve_budget_progressive"):
        raise RuntimeError(f"Движок не поддерживает progressive: пересоберите {ENGINE_BUILD}")
    out=(ctypes.c_double*(10 if progressive else 8))()
    thresholds=(ctypes.c_float*len(THRESHOLDS))(*THRESHOLDS)
    solver=NATIVE.solve_budget_progressive if progressive else NATIVE.solve_budget
    result=solver(pack(b),allowed,depth,thresholds,len(THRESHOLDS),think,GROWTH,out)
    if info is not None:
        fallback=bool(out[7])
        has_threshold=out[0]>0 if progressive else bool(out[1])
        info.update(depth=int(out[8]) if progressive else 1 if fallback else depth,passes=int(out[0]),threshold=float(f"{out[1]:.0e}") if has_threshold else None,
                    positions=int(out[2]),search_s=round(out[3],3),cut=bool(out[4]),skipped=bool(out[5]),fallback=fallback)
        if progressive:info.update(requested_depth=int(out[9]),policy=policy)
    return DIRECTIONS.index(result) if result>=0 else None

apply_spawn_model(default_spawn_model())

@contextmanager
def lock_bot(lock):
    if sys.platform != "win32":
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeError("Бот уже запущен") from exc
        yield
        return

    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    for operation in (kernel.LockFile,kernel.UnlockFile):
        operation.argtypes=[wintypes.HANDLE]+[wintypes.DWORD]*4
        operation.restype=wintypes.BOOL
    region=(msvcrt.get_osfhandle(lock.fileno()),0,0,1,0)
    if not kernel.LockFile(*region):
        error=ctypes.get_last_error()
        if error==33:  # ERROR_LOCK_VIOLATION
            raise RuntimeError("Бот уже запущен")
        raise ctypes.WinError(error)
    try:yield
    finally:kernel.UnlockFile(*region)

def _sleep_with_stop(seconds,metrics=None,stage_name="sleep"):
    remaining=max(0.0,float(seconds))
    def wait():
        nonlocal remaining
        while remaining>0:
            if (ROOT/"STOP").exists():return True
            chunk=min(1.0,remaining)
            time.sleep(chunk)
            remaining-=chunk
        return (ROOT/"STOP").exists()
    return _timed(metrics,stage_name,wait) if metrics is not None else wait()

@contextmanager
def _tee_stdout(log_path):
    if not log_path:
        yield
        return
    path=Path(log_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    original=sys.stdout
    with path.open("a",encoding="utf-8") as log_file:
        class _Tee:
            def write(self,s):
                original.write(s)
                return log_file.write(s)
            def flush(self):
                original.flush()
                log_file.flush()
        sys.stdout=_Tee()
        try:yield
        except Exception as exc:
            log_file.write(f"{exc}\n")
            log_file.flush()
            raise
        finally:sys.stdout=original

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--serial",default=None)
    ap.add_argument("--steps",type=int,default=10000)
    ap.add_argument("--delay",type=float,default=.4)
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--clear-stop",action="store_true",help="снять STOP при запуске после получения блокировки")
    ap.add_argument("--depth",type=int,default=DEFAULT_DEPTH,help="предел глубины поиска (ходов вперёд)")
    ap.add_argument("--think",type=float,default=DEFAULT_THINK,help="бюджет расчёта хода, с")
    ap.add_argument("--early-think",type=float,default=None,help="бюджет расчёта хода, с, пока на поле меньше двух плиток 11 (0 — отключить)")
    ap.add_argument("--wait-energy",action="store_true",help="ждать восстановления энергии при 0/200 вместо остановки")
    ap.add_argument("--energy-poll",type=float,default=30.0,help="интервал проверки энергии при --wait-energy, с")
    ap.add_argument("--log",default=None,help="дописывать журнал вывода в файл (UTF-8)")
    ap.add_argument("--search-policy",choices=("legacy","progressive"),default="progressive",help="планировщик поиска; progressive последовательно углубляет завершённые проходы")
    args=ap.parse_args()
    if args.search_policy=="progressive":
        if args.depth<1:ap.error("Глубина progressive должна быть положительной")
        if not hasattr(NATIVE,"solve_budget_progressive"):ap.error(f"Движок не поддерживает progressive: пересоберите {ENGINE_BUILD}")
    if not math.isfinite(args.energy_poll) or args.energy_poll<=0:
        ap.error("--energy-poll должен быть положительным")
    if args.early_think is not None and (not math.isfinite(args.early_think) or args.early_think<0):
        ap.error("--early-think должен быть неотрицательным")
    with open(ROOT/"bot.lock","a+b") as lock, lock_bot(lock):
        if args.clear_stop:(ROOT/"STOP").unlink(missing_ok=True)
        with _tee_stdout(args.log):
            play(args)

def play(args):
    def adb(*cmd):
        return subprocess.run(["adb",*(["-s",args.serial] if args.serial else []),*cmd],capture_output=True,check=True,timeout=20).stdout
    size=adb("shell","wm","size").decode().strip()
    cropped=size=="Physical size: 1440x3040"
    def capture():
        if not cropped:return adb("exec-out","screencap")
        # Four badge strips from one frame, consuming the pipe sequentially.
        command="screencap | { dd bs=5760 skip=1150 count=81; dd bs=5760 skip=229 count=81; dd bs=5760 skip=229 count=81; dd bs=5760 skip=229 count=81; } 2>/dev/null"
        raw=adb("exec-out",command)
        block=5760*81
        if len(raw)!=block*4:raise RuntimeError("Неполный кадр значков")
        payload=b"STRP"+struct.pack("<IIII",1440,3040,80,4)
        for i,y in enumerate((1150,1460,1770,2080)):
            payload+=struct.pack("<I",y)+raw[i*block+16:i*block+16+5760*80]
        return payload
    # Начальная модель — счётчики из play.log по стадиям; дальше уточняется по наблюдениям.
    model=load_spawn_model()
    apply_spawn_model(model)
    last=None;expected=None
    for step in range(args.steps):
        if (ROOT/"STOP").exists():print("Остановлено файлом STOP",flush=True);break
        metrics=CycleMetrics()
        started=metrics.started
        if step:print(json.dumps({"cycle_s":round(started-previous_started,3)}),flush=True)
        previous_started=started
        outcome="error"
        data=None
        waited_energy=False
        try:
            metrics.call("focus",ensure_game_focus,adb)
            previous_observation=None
            full_mode=False
            last_observation_error=None
            unchanged_streak=0
            adb_capture_errors=0
            for attempt in range(15):
                if (ROOT/"STOP").exists():
                    outcome="stopped"
                    print("Остановлено при ожидании кадра",flush=True);return
                metrics.counts["observations"]+=1
                try:
                    b,data,used_full,_=read_observation(capture,lambda:adb("exec-out","screencap"),full_mode,metrics)
                except RuntimeError as exc:
                    unchanged_streak=0
                    if isinstance(exc.__cause__,(subprocess.CalledProcessError,subprocess.TimeoutExpired)):
                        adb_capture_errors+=1
                        if adb_capture_errors>=3:
                            metrics.call("focus",ensure_game_focus,adb)
                            raise RuntimeError(f"Повторяющаяся ошибка захвата экрана ADB: {exc}") from exc.__cause__
                    else:
                        adb_capture_errors=0
                    previous_observation=None
                    full_mode=True
                    last_observation_error=str(exc)
                    metrics.counts["failed_observations"]=metrics.counts.get("failed_observations",0)+1
                    print(json.dumps({"observation_error":str(exc),"attempt":attempt},ensure_ascii=False),flush=True)
                else:
                    adb_capture_errors=0
                    full_mode=full_mode or used_full
                    if observation_ready(b,previous_observation,expected,last):
                        if expected is not None:
                            rank=next(b[i] for i in range(16) if b[i]!=expected[i])
                            if NATIVE.get_spawn_prob(max(expected),rank)==0:print(json.dumps({"confirmed_new_spawn":rank,"stage":max(expected)}),flush=True)
                        break
                    if expected is not None and b==last:
                        unchanged_streak+=1
                        if unchanged_streak>=STALE_READ_LIMIT:
                            full_data=data if used_full else None
                            if full_data is None:
                                try:
                                    metrics.counts["captures"]+=1
                                    full_data=_timed(metrics,"capture",lambda:adb("exec-out","screencap"))
                                except OBSERVATION_ERRORS:
                                    full_data=None
                            energy=read_energy(full_data,metrics) if full_data is not None else None
                            print(json.dumps({"stale_board":True,"unchanged_reads":unchanged_streak,"energy":list(energy) if energy else None},ensure_ascii=False),flush=True)
                            if energy is not None and energy[0]==0:
                                if getattr(args,"wait_energy",False):
                                    print(json.dumps({"waiting_for_energy":True,"energy":list(energy),"poll_s":args.energy_poll},ensure_ascii=False),flush=True)
                                    while True:
                                        if _sleep_with_stop(args.energy_poll,metrics,"energy_wait"):
                                            outcome="stopped"
                                            print("Остановлено при ожидании энергии",flush=True);return
                                        metrics.call("focus",ensure_game_focus,adb)
                                        metrics.counts["captures"]+=1
                                        poll_data=_timed(metrics,"capture",lambda:adb("exec-out","screencap"))
                                        energy=read_energy(poll_data,metrics)
                                        print(json.dumps({"energy_poll":list(energy) if energy else None},ensure_ascii=False),flush=True)
                                        if energy is not None and energy[0]>=1:
                                            expected=None;last=None;waited_energy=True;break
                                    break
                                if full_data is not None:
                                    try:metrics.call("diagnostic_write",lambda:decode_screen(full_data).save(ROOT/"latest.png"))
                                    except OBSERVATION_ERRORS:pass
                                outcome="no_energy"
                                print(f"Энергия закончилась ({energy[0]}/{energy[1]}). Остановка. Для автоожидания используйте --wait-energy.",flush=True)
                                return
                            save_frame=full_data if full_data is not None else data
                            if save_frame is not None:
                                try:metrics.call("diagnostic_write",lambda:decode_screen(save_frame).save(ROOT/"latest.png"))
                                except OBSERVATION_ERRORS:pass
                            raise RuntimeError("Поле не изменилось после свайпа: возможно, закончилась энергия или появился диалог. Повторный жест не отправлялся.")
                    else:
                        unchanged_streak=0
                    if expected is not None and b!=last and not valid_successor(expected,b):
                        if not full_mode:print(json.dumps({"action":"retry_full_frame","reason":"transition_mismatch"}),flush=True)
                        full_mode=True
                    previous_observation=b
                if attempt==14:
                    if data is not None:
                        try:metrics.call("diagnostic_write",lambda:decode_screen(data).save(ROOT/"recognition-error.png"))
                        except OBSERVATION_ERRORS:pass
                    raise RuntimeError("Не дождался стабильного результата хода; повторный жест не отправлялся. "+json.dumps({"expected":expected,"actual":previous_observation,"recognition_error":last_observation_error}))
                metrics.call("confirm_wait",time.sleep,.15)
            if waited_energy:
                outcome="waited_energy"
                continue
            if expected is not None and b!=last:
                index=next(i for i in range(16) if b[i]!=expected[i])
                rank=b[index];stage=max(expected)
                def update_model():
                    record_spawn(model,stage,rank)
                    apply_spawn_model(model)
                metrics.call("model_update",update_model)
                metrics.call("model_write",save_spawn_model,model)
                print(json.dumps({"spawn_level":rank,"cell":index,"stage":stage,"stage_counts":model["stages"][str(stage)][:6]}),flush=True)
            if reached_goal(b):
                metrics.call("diagnostic_write",lambda:decode_screen(adb("exec-out","screencap")).save(ROOT/"goal.png"))
                outcome="goal"
                print(f"ЦЕЛЬ ДОСТИГНУТА: {GOAL_COUNT} плитки уровня {CAP} одновременно. "+str(b),flush=True);break
            search={}
            early=getattr(args,"early_think",None)
            think_budget=min(args.think,early) if (early is not None and early>0 and b.count(CAP)<2) else args.think
            d=metrics.call("choose",choose,b,args.depth,think_budget,search,policy=args.search_policy)
            captured=metrics.first_capture_at if metrics.first_capture_at is not None else started
            step_log={"step":step,"board":b,"direction":None if d is None else ["left","right","up","down"][d],"capture_s":round(captured-started,3),"think_s":round(time.monotonic()-captured,3),"search":search}
            if think_budget!=args.think:step_log["budget_s"]=round(think_budget,3)
            print(json.dumps(step_log,ensure_ascii=False),flush=True)
            if args.dry_run or d is None:
                metrics.call("diagnostic_write",lambda:decode_screen(data).save(ROOT/"latest.png"))
                outcome="dry_run" if args.dry_run else "no_move"
                break
            last=b
            expected=move(b,d)
            w,h=metrics.frame_size if metrics.frame_size is not None else metrics.call("frame_geometry",lambda:decode_screen(data).size)
            coords=[(.77,.58,.28,.58),(.28,.58,.77,.58),(.50,.72,.50,.43),(.50,.43,.50,.72)][d]
            xy=[round(v*(w if i%2==0 else h)) for i,v in enumerate(coords)]
            if (ROOT/"STOP").exists():
                outcome="stopped"
                print("Остановлено перед жестом",flush=True);break
            metrics.call("focus",ensure_game_focus,adb)
            if (ROOT/"STOP").exists():
                outcome="stopped"
                print("Остановлено перед жестом",flush=True);break
            metrics.call("gesture",send_move,adb,xy)
            outcome="moved"
            _sleep_with_stop(max(.15,args.delay),metrics,"post_move_wait")
        finally:
            print(json.dumps({"step":step,"cycle_metrics":metrics.snapshot(outcome)},ensure_ascii=False),flush=True)
    else:
        print("Достигнут лимит ходов",flush=True)

if __name__=="__main__":
    try:main()
    except KeyboardInterrupt:print("Остановлено")
    except subprocess.CalledProcessError as exc:
        sys.exit((exc.stderr or b"").decode(errors="replace").strip() or str(exc))
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:sys.exit(str(exc))
