#!/usr/bin/env python3
"""Звёздное провидение: локальный ADB-бот. Ctrl+C останавливает."""
import argparse, subprocess, time, json, io, tempfile
from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parent
PACKAGE="com.global.sgdbzseaeu"

def mask(im,r,c):
    # Геометрия при 1440x3040; координаты нормализованы в read_board.
    x,y=361+310*c,1188+310*r
    a=np.array(im.crop((x-24,y-24,x+25,y+31)).convert("RGB"))
    a=(a.min(axis=2)>185).astype(np.uint8)*255
    count,labels,stats,_=cv2.connectedComponentsWithStats(a)
    if count<2:return None
    largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    a=(labels==largest).astype(np.uint8)*255
    ys,xs=np.where(a>0)
    if len(xs)<20:return None
    return cv2.resize(a[ys.min():ys.max()+1,xs.min():xs.max()+1],(32,40))

def templates():
    a=Image.open(ROOT/"calibration.png"); b=Image.open(ROOT/"calibration2.png")
    return {1:mask(a,0,0),2:mask(a,1,0),3:mask(a,3,0),4:mask(a,2,1),5:mask(a,3,1)}
TEMPLATES=templates()

def read_board(data):
    im=Image.open(io.BytesIO(data)).convert("RGB").resize((1440,3040))
    # Борд должен быть виден; интерфейс проверяется до каждого жеста.
    board=[]
    for r in range(4):
        for c in range(4):
            m=mask(im,r,c)
            if m is None:board.append(0);continue
            errors={v:np.mean(np.abs(m.astype(float)-t.astype(float)))/255 for v,t in TEMPLATES.items()}
            v=min(errors,key=errors.get)
            if errors[v]>.12:
                pic=Image.fromarray(255-m).resize((128,160))
                canvas=Image.new("L",(168,200),255);canvas.paste(pic,(20,20))
                with tempfile.NamedTemporaryFile(suffix=".png",dir=ROOT) as f:
                    canvas.save(f.name)
                    result=subprocess.run(["tesseract",f.name,"stdout","--psm","6","-l","eng"],capture_output=True,text=True,check=True).stdout.strip()
                if not result.isdigit() or not 1<=int(result)<=20:raise RuntimeError(f"Не распознан уровень ({r},{c}): {result!r}, ошибка {errors[v]:.2f}")
                v=int(result);TEMPLATES[v]=m
            board.append(v)
    if not any(board):raise RuntimeError("Поле не найдено")
    return tuple(board)

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

def heuristic(b):
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
    options=[move(b,d) for d in range(4)];options=[v for v in options if v!=b]
    return max((search(v,depth-1,True) for v in options),default=-100000)

def choose(b):
    search.cache_clear()
    legal=[d for d in range(4) if move(b,d)!=b]
    return max(legal,key=lambda d:search(move(b,d),3,True)) if legal else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--serial",default=None);ap.add_argument("--steps",type=int,default=100);ap.add_argument("--dry-run",action="store_true");args=ap.parse_args()
    def adb(*cmd):return subprocess.run(["adb",*(["-s",args.serial] if args.serial else []),*cmd],capture_output=True,check=True,timeout=20).stdout
    last=None;stuck=0
    for step in range(args.steps):
        if (ROOT/"STOP").exists():print("Остановлено файлом STOP");break
        focus=adb("shell","dumpsys","window").decode(errors="replace")
        if not any(PACKAGE in line for line in focus.splitlines() if "mCurrentFocus" in line):raise RuntimeError("Игра не в фокусе")
        data=adb("exec-out","screencap","-p");(ROOT/"latest.png").write_bytes(data)
        b=read_board(data);d=choose(b)
        print(json.dumps({"step":step,"board":b,"direction":None if d is None else ["left","right","up","down"][d]},ensure_ascii=False),flush=True)
        if args.dry_run or d is None:break
        stuck=stuck+1 if b==last else 0
        if stuck>=2:raise RuntimeError("Поле не меняется: энергия закончилась или появился диалог. Остановка.")
        last=b
        w,h=Image.open(io.BytesIO(data)).size
        coords=[(.77,.58,.28,.58),(.28,.58,.77,.58),(.50,.72,.50,.43),(.50,.43,.50,.72)][d]
        xy=[round(v*(w if i%2==0 else h)) for i,v in enumerate(coords)]
        adb("shell","input","swipe",*map(str,xy),"220");time.sleep(1.2)

if __name__=="__main__":
    try:main()
    except KeyboardInterrupt:print("Остановлено")
