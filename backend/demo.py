"""Deterministic teaching images, not experimental or published measurements."""
import io
import math
from PIL import Image, ImageDraw, ImageFont


def demo_image(mode: str) -> bytes:
    im=Image.new('RGB',(1000,700),'#fafaf7')
    d=ImageDraw.Draw(im)
    def font(size):
        try: return ImageFont.truetype('arial.ttf',size)
        except OSError: return ImageFont.load_default(size=size)
    d.text((45,25),'SYNTHETIC PRACTICE IMAGE  /  NOT RESEARCH DATA',fill='#52616c',font=font(19))
    if mode=='graph':
        d.line([(100,100),(100,600),(900,600)],fill='#354451',width=3)
        for value in range(10,91,10):
            x=100+(value-10)*10
            d.line([(x,600),(x,609)],fill='#354451',width=2)
            d.text((x-10,617),str(value),fill='#354451',font=font(18))
        for value in range(0,101,20):
            y=600-value*5
            d.line([(90,y),(100,y)],fill='#354451',width=2)
            d.text((48,y-10),str(value),fill='#354451',font=font(17))
        pts=[]
        for x in range(100,901):
            t=10+(x-100)/10
            val=6+55*math.exp(-((t-35)/.65)**2)+85*math.exp(-((t-50)/.9)**2)+30*math.exp(-((t-70)/1.2)**2)
            pts.append((x,600-val*5))
        d.line(pts,fill='#cf7241',width=3)
        d.text((400,660),'X position (degrees)',fill='#354451',font=font(18))
        d.text((580,130),'Known peaks: 35, 50, 70 degrees',fill='#52616c',font=font(18))
    else:
        d.rectangle((200,200,400,400),fill='#84939e',outline='#374b59',width=2)
        d.ellipse((550,230,750,430),fill='#a7b4a0',outline='#526c46',width=2)
        d.text((190,430),'Square: 200 x 200 px',fill='#354451',font=font(19))
        d.text((180,462),'At 10 units / 100 px: area = 400 units squared',fill='#52616c',font=font(18))
        d.line([(100,600),(200,600)],fill='#202f3b',width=4)
        d.line([(100,592),(100,608)],fill='#202f3b',width=2)
        d.line([(200,592),(200,608)],fill='#202f3b',width=2)
        d.text((100,620),'10 um',fill='#354451',font=font(20))
        d.text((550,490),'Geometry only; no phase identity.',fill='#52616c',font=font(18))
    out=io.BytesIO(); im.save(out,format='PNG'); return out.getvalue()
