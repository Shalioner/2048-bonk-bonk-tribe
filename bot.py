#!/usr/bin/env python3
"""Звёздное провидение: локальный ADB-бот. Ctrl+C останавливает."""
import argparse, subprocess, time, json, io, tempfile, struct, fcntl, ctypes
from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parent
PACKAGE="com.global.sgdbzseaeu"

def mask(im,r,c,raw=False):
    # Геометрия при 1440x3040; координаты нормализованы в read_board.
    x,y=361+310*c,1188+310*r
    a=np.array(im.crop((x-34,y-24,x+35,y+31)).convert("RGB"))
    a=((a.min(axis=2)>175)&(a[:,:,0].astype(int)>=a[:,:,2].astype(int)-8)).astype(np.uint8)*255
    a=cv2.morphologyEx(a,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,labels,stats,centers=cv2.connectedComponentsWithStats(a)
    if count<2:return None
    candidates=[i for i in range(1,count) if 8<centers[i,0]<61 and 3<centers[i,1]<48 and stats[i,cv2.CC_STAT_AREA]>=12]
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
    a=Image.open(ROOT/"calibration.png"); b=Image.open(ROOT/"calibration2.png")
    return {1:mask(a,0,0),2:mask(a,1,0),3:mask(a,3,0),4:mask(a,2,1),5:mask(a,3,1),7:mask(Image.open(ROOT/"fixtures/saved-board.png"),3,0)}
TEMPLATES=templates()

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
DIGITS={i:TEMPLATES[i] for i in (1,2,3,4,5)}
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

ICON_REFS={level:icon_patch(Image.open(ROOT/"calibration.png"),r,c,64)
           for level,r,c in ((3,3,0),(4,2,1),(5,3,1))}

def icon_level(im,r,c):
    patch=icon_patch(im,r,c,90)
    if float(patch.std())<5:return None # badge-only capture has no icon pixels
    scores={level:float(cv2.matchTemplate(patch,ref,cv2.TM_CCOEFF_NORMED).max())
            for level,ref in ICON_REFS.items()}
    ranked=sorted(scores,key=scores.get,reverse=True)
    best,second=ranked[:2]
    return best if scores[best]>=.94 and scores[best]-scores[second]>=.12 else None

def read_board(data):
    im=decode_screen(data).resize((1440,3040))
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
                with tempfile.NamedTemporaryFile(suffix=".png",dir=ROOT) as f:
                    canvas.save(f.name)
                    result=subprocess.run(["tesseract",f.name,"stdout","--psm","6","-l","eng"],capture_output=True,text=True,check=True).stdout.strip()
                if not result.isdigit():
                    with tempfile.NamedTemporaryFile(suffix=".png",dir=ROOT) as f:
                        canvas.save(f.name)
                        result=subprocess.run(["tesseract",f.name,"stdout","--psm","13","-l","eng","-c","tessedit_char_whitelist=0123456789"],capture_output=True,text=True,check=True).stdout.strip()
                if not result.isdigit() or not 1<=int(result)<=20:raise RuntimeError(f"Не распознан уровень ({r},{c}): {result!r}, ошибка {errors[v]:.2f}")
                v=int(result);TEMPLATES[v]=m
            board.append(v)
    if not any(board):raise RuntimeError("Поле не найдено")
    return tuple(board)

def read_with_fallback(data,full_capture):
    try:
        return read_board(data),data,False
    except RuntimeError as exc:
        (ROOT/"failed-observation.bin").write_bytes(data)
        print(json.dumps({"observation_error":str(exc),"action":"retry_full_frame"},ensure_ascii=False),flush=True)
        full=full_capture()
        return read_board(full),full,True

def move(board,d):
    out=list(board)
    for n in range(4):
        ids=([4*n+i for i in range(4)] if d<2 else [4*i+n for i in range(4)])
        if d in (1,3):ids=ids[::-1]
        vals=[board[i] for i in ids if board[i]]; result=[];i=0
        while i<len(vals):
            if i+1<len(vals) and vals[i]==vals[i+1]:result.append(vals[i]+1);i+=2
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
    return b.count(13)>=3

def legal_moves(b):
    options=[(d,move(b,d)) for d in range(4) if move(b,d)!=b]
    # Сохраняем собранные 13 для трёх плиток одновременно.
    preserved=[(d,a) for d,a in options if a.count(13)>=b.count(13)]
    return preserved or options

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

NATIVE=ctypes.CDLL(str(ROOT/"vendor/engine.dylib"))
NATIVE.init_tables()
NATIVE.solve_levels.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int]
NATIVE.solve_levels.restype=ctypes.c_int
NATIVE.execute_move.argtypes=[ctypes.c_int,ctypes.c_uint64]
NATIVE.execute_move.restype=ctypes.c_uint64
NATIVE.set_spawn_probs.argtypes=[ctypes.c_float]*3
NATIVE.set_spawn_probs.restype=None
NATIVE.set_spawn_vector.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_int]
NATIVE.set_spawn_vector.restype=None
NATIVE.score_levels.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_int]
NATIVE.score_levels.restype=ctypes.c_float
DIRECTIONS=(2,3,0,1) # Python L,R,U,D -> upstream U,D,L,R

def set_spawn_weights(counts):
    weights=(ctypes.c_float*len(counts))(*counts)
    NATIVE.set_spawn_vector(weights,len(counts))

def pack(b):
    if any(not 0<=v<=15 for v in b):raise RuntimeError("Уровень вне диапазона движка")
    return sum(v<<(4*i) for i,v in enumerate(b))

def choose(b):
    legal=legal_moves(b)
    if not legal:return None
    allowed=sum(1<<DIRECTIONS[d] for d,_ in legal)
    depth=7
    result=NATIVE.solve_levels(pack(b),allowed,depth)
    return DIRECTIONS.index(result) if result>=0 else None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--serial",default=None)
    ap.add_argument("--steps",type=int,default=10000)
    ap.add_argument("--delay",type=float,default=.4)
    ap.add_argument("--dry-run",action="store_true")
    args=ap.parse_args()
    lock=open(ROOT/"bot.lock","w")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError("Бот уже запущен")
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
    # Initial weights are a provisional prior, not measured game probabilities.
    model_path=ROOT/"spawn-model.json"
    counts=json.loads(model_path.read_text())["weights"] if model_path.exists() else [85,10,5]
    counts=(counts+[0]*15)[:15]
    if counts[3]==0:counts[3]=1 # rank 4 independently confirmed in fixtures/spawn-four.png
    set_spawn_weights(counts)
    last=None;expected=None;stuck=0
    for step in range(args.steps):
        if (ROOT/"STOP").exists():print("Остановлено файлом STOP",flush=True);break
        started=time.monotonic()
        if step:print(json.dumps({"cycle_s":round(started-previous_started,3)}),flush=True)
        previous_started=started
        focus=adb("shell","dumpsys","window").decode(errors="replace")
        if not any(PACKAGE in line for line in focus.splitlines() if "mCurrentFocus" in line):
            raise RuntimeError("Игра не в фокусе")
        data=capture()
        captured=time.monotonic()
        previous_observation=None
        full_mode=False
        last_observation_error=None
        for attempt in range(15):
            if (ROOT/"STOP").exists():print("Остановлено при ожидании кадра",flush=True);return
            try:
                b,data,used_full=read_with_fallback(data,lambda:adb("exec-out","screencap"))
                full_mode=full_mode or used_full
                if observation_ready(b,previous_observation,expected,last):
                    if expected is not None:
                        rank=next(b[i] for i in range(16) if b[i]!=expected[i])
                        if counts[rank-1]==0:print(json.dumps({"confirmed_new_spawn":rank}),flush=True)
                    break
                if expected is not None and b!=last and not valid_successor(expected,b):
                    if not full_mode:print(json.dumps({"action":"retry_full_frame","reason":"transition_mismatch"}),flush=True)
                    full_mode=True
                previous_observation=b
            except RuntimeError as exc:
                previous_observation=None
                full_mode=True
                last_observation_error=str(exc)
                print(json.dumps({"observation_error":str(exc),"attempt":attempt},ensure_ascii=False),flush=True)
            if attempt==14:
                decode_screen(adb("exec-out","screencap")).save(ROOT/"recognition-error.png")
                raise RuntimeError("Не дождался стабильного результата хода; повторный жест не отправлялся. "+json.dumps({"expected":expected,"actual":previous_observation,"recognition_error":last_observation_error}))
            time.sleep(.15)
            data=adb("exec-out","screencap") if full_mode else capture()
        if expected is not None and b!=last:
            index=next(i for i in range(16) if b[i]!=expected[i])
            rank=b[index];counts[rank-1]+=1
            set_spawn_weights(counts)
            model_path.write_text(json.dumps({"weights":counts,"prior":[85,10,5],"description":"prior plus observed spawns of levels 1..15; new ranks require stable frame confirmation"}))
            print(json.dumps({"spawn_level":rank,"cell":index,"spawn_weights":counts}),flush=True)
        if reached_goal(b):
            decode_screen(adb("exec-out","screencap")).save(ROOT/"goal.png")
            print("ЦЕЛЬ ДОСТИГНУТА: три плитки 13 одновременно. "+str(b),flush=True);break
        d=choose(b)
        print(json.dumps({"step":step,"board":b,"direction":None if d is None else ["left","right","up","down"][d],"capture_s":round(captured-started,3),"think_s":round(time.monotonic()-captured,3)},ensure_ascii=False),flush=True)
        if args.dry_run or d is None:
            decode_screen(data).save(ROOT/"latest.png");break
        stuck=stuck+1 if b==last else 0
        if stuck>=2:
            decode_screen(data).save(ROOT/"latest.png")
            raise RuntimeError("Поле не меняется: энергия закончилась или появился диалог. Остановка.")
        last=b
        expected=move(b,d)
        w,h=decode_screen(data).size
        coords=[(.77,.58,.28,.58),(.28,.58,.77,.58),(.50,.72,.50,.43),(.50,.43,.50,.72)][d]
        xy=[round(v*(w if i%2==0 else h)) for i,v in enumerate(coords)]
        if (ROOT/"STOP").exists():print("Остановлено перед жестом",flush=True);break
        send_move(adb,xy)
        time.sleep(max(.15,args.delay))
    else:
        print("Достигнут лимит ходов",flush=True)

if __name__=="__main__":
    try:main()
    except KeyboardInterrupt:print("Остановлено")
