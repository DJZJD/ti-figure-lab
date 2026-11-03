import io
import math
from PIL import Image,ImageDraw
from backend.peaks import peak_candidates
from backend.demo import demo_image


def test_demo_narrow_peak_and_blank():
    im=Image.open(io.BytesIO(demo_image('graph')))
    result=peak_candidates(im,504,151,40)
    assert result['candidates']
    assert abs(result['candidates'][0]['x']-500)<=2
    assert not peak_candidates(im,300,200,40)['candidates']


def test_black_and_colored_stacked_traces_are_not_topmost_only():
    im=Image.new('RGB',(180,180),'white');draw=ImageDraw.Draw(im)
    for base,color in [(80,'black'),(120,'blue')]:
        draw.line([(x,base-30*math.exp(-((x-90)/10)**2)) for x in range(180)],fill=color,width=3)
    result=peak_candidates(im,90,87,60)['candidates']
    assert abs(result[0]['y']-90)<3
    assert any(abs(p['y']-50)<3 for p in result)


def test_no_peak_on_flat_sloped_or_dark_image():
    for points in [[(0,60),(160,60)],[(0,120),(160,20)]]:
        im=Image.new('RGB',(160,160),'white');ImageDraw.Draw(im).line(points,fill='black',width=3)
        assert not peak_candidates(im,80,70,40)['candidates']
    assert not peak_candidates(Image.new('RGB',(160,160),'black'),80,70,40)['candidates']


def test_tinted_background_and_thin_tall_peak():
    im=Image.new('RGB',(180,180),(155,255,255));draw=ImageDraw.Draw(im)
    draw.line([(20,150),(86,150),(90,40),(94,150),(160,150)],fill='red',width=3)
    result=peak_candidates(im,92,45,40)['candidates']
    assert result and abs(result[0]['x']-90)<=2
    assert not peak_candidates(im,30,60,20)['candidates']


def test_unresolved_single_column_tip_is_not_claimed_as_supported_apex():
    im=Image.new('RGB',(120,120),'white')
    ImageDraw.Draw(im).line([(60,35),(60,110)],fill='black',width=1)
    result=peak_candidates(im,63,39,20)
    assert result['candidates'][0]['kind']=='unresolved_stroke_tip'
    assert result['candidates'][0]['local_rise_px'] is None

def test_gradient_background_scan_and_no_assigned_identity():
    from backend.xrd_pixels import scan_peaks
    im=Image.new('RGB',(180,180));draw=ImageDraw.Draw(im)
    for y in range(180):draw.line((0,y,179,y),fill=(70+y//2,180+y//3,210))
    for base,color in [(75,'red'),(140,'black')]:
        draw.line([(x,base-25*math.exp(-((x-90)/9)**2)) for x in range(180)],fill=color,width=3)
    cs=scan_peaks(im,(0,0,180,180))
    for apex in (50,115):assert any(abs(c['x']-90)<3 and abs(c['y']-apex)<3 for c in cs)
    assert all(c['sample'] is None and c['review']=='pending' for c in cs)
    blank=Image.new('RGB',(180,180),(80,180,210))
    assert not scan_peaks(blank,(0,0,180,180))

def test_framed_stacked_traces_do_not_merge_via_border():
    from backend.xrd_pixels import scan_peaks
    im=Image.new('RGB',(320,300),(120,220,240));d=ImageDraw.Draw(im)
    d.rectangle((10,10,310,290),outline='black',width=4)
    for base,color in [(85,'purple'),(180,'green'),(260,'black')]:
        d.line([(10,base),(140,base),(150,base-60),(160,base),(310,base)],fill=color,width=3)
    d.text((210,100),'1000 C',fill='black')
    cs=scan_peaks(im,(10,10,310,290))
    assert len(cs)==3
    assert len({c['trace_id'] for c in cs})==3
    for apex in (25,120,200):
        assert any(abs(c['x']-150)<=2 and abs(c['y']-apex)<=2 for c in cs)
