from PIL import Image,ImageDraw
import pytest
from backend.scale_pixels import refine_scale
from backend.models import Point

def test_refine_short_model_bar_preserves_printed_value_as_unverified():
    im=Image.new('RGB',(240,150),'white');ImageDraw.Draw(im).rectangle((80,100,160,102),fill='black')
    ends,info=refine_scale(im,[Point(x=85,y=95),Point(x=145,y=95)])
    assert ends==[Point(x=80,y=101),Point(x=160,y=101)]
    assert info['refined_length_px']==80 and not info['printed_value_verified']

def test_ambiguous_blank_tilted_and_cropped_scale_rejected():
    im=Image.new('RGB',(240,150),'white');points=[Point(x=80,y=95),Point(x=160,y=95)]
    with pytest.raises(ValueError):refine_scale(im,points)
    d=ImageDraw.Draw(im);d.rectangle((80,90,160,92),fill='black');d.rectangle((80,105,160,107),fill='black')
    with pytest.raises(ValueError):refine_scale(im,points)
    with pytest.raises(ValueError):refine_scale(im,[Point(x=80,y=80),Point(x=160,y=100)])
    edge=Image.new('RGB',(140,150),'white');ImageDraw.Draw(edge).rectangle((80,100,139,102),fill='black')
    with pytest.raises(ValueError):refine_scale(edge,points)
