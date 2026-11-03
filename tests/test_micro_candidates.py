from PIL import Image,ImageDraw
from backend.micro_candidates import propose_regions

def test_local_regions_exclude_edges_and_color_contacts():
    im=Image.new('RGB',(200,200),'white');d=ImageDraw.Draw(im)
    d.rectangle((30,30,49,49),fill='black');d.rectangle((0,60,20,80),fill='black')
    d.rectangle((100,100,120,120),fill='black');d.line((110,90,110,130),fill='yellow',width=3)
    r=propose_regions(im,(0,0,200,200));assert len(r['candidates'])==1
    c=r['candidates'][0];assert c['area_px2']==400 and c['phase'] is None and c['status']=='needs_review'
    assert not propose_regions(Image.new('RGB',(100,100),'white'),(0,0,100,100))['candidates']
