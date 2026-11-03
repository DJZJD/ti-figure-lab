import pytest
from PIL import Image,ImageDraw
from backend import graph_pixels as gp,graph_refine as gr


@pytest.mark.parametrize('color,scale',[('red',1),('blue',1),('green',2)])
def test_frame_and_hollow_centroids_without_specific_color(tmp_path,color,scale):
    im=Image.new('RGB',(700,500),'white');d=ImageDraw.Draw(im)
    d.rectangle((100,80,600,420),outline='black',width=2)
    for x in [200,300,400,500]:d.line((x,408,x,420),fill='black',width=2)
    for y in [100,200,300,400]:d.line((100,y,112,y),fill='black',width=2)
    for x,y in [(250,150),(450,350)]:d.polygon([(x-10,y),(x+5,y-10),(x+5,y+10)],outline=color,width=2)
    if scale!=1:im=im.resize((700*scale,500*scale))
    path=tmp_path/'plot.png';im.save(path)
    c=gp.detect(path,dict(left=160,top=150,right=900,bottom=850))
    assert c['frame']['left']==pytest.approx(100*scale,abs=3)
    assert len(c['markers'])==2
    assert c['markers'][0]['x']==pytest.approx(250*scale,abs=3)
    assert c['markers'][0]['y']==pytest.approx(150*scale,abs=3)


def test_blank_image_refuses_candidates(tmp_path):
    path=tmp_path/'blank.png';Image.new('RGB',(500,500),'white').save(path)
    with pytest.raises(ValueError):gp.detect(path,dict(left=100,top=100,right=900,bottom=900))


def test_binding_unknown_and_duplicate_ids_rejected():
    c=dict(width=1000,height=1000,frame=dict(left=100,top=100,right=900,bottom=900),
      ticks=dict(x=[dict(id=f'X{i}',x=x,y=900) for i,x in enumerate([100,500,900])],
                 y=[dict(id=f'Y{i}',x=100,y=y) for i,y in enumerate([900,500,100])]),
      markers=[dict(id='M0',x=300,y=700)],warnings=[])
    def axis(prefix):return dict(title='Axis',unit='u',ticks=[dict(candidate_id=f'{prefix}{i}',value=v) for i,v in enumerate([0,50,100])])
    b=gr.Binding(status='bound',reason='Fixture',x_axis=axis('X'),y_axis=axis('Y'),markers=[dict(candidate_id='M0',sample_label='',curve_label='',binding_evidence='')])
    measured=gr.bind(c,b)
    assert measured.plot.markers[0].x==300
    b.markers[0].candidate_id='not_present'
    with pytest.raises(ValueError):gr.bind(c,b)
    b.markers[0].candidate_id='M0';b.x_axis.ticks[1].candidate_id='X0'
    with pytest.raises(ValueError):gr.bind(c,b)
