"""Prevent public template releases from regressing readable text and focus."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'

def luminance(color):
    channels = [int(color[n:n+2],16)/255 for n in (1,3,5)]
    linear = [c/12.92 if c<=0.04045 else ((c+.055)/1.055)**2.4 for c in channels]
    return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))

def test_faint_text_is_readable_on_all_gateway_solid_surfaces():
    css=(TEMPLATES/'base.html').read_text(encoding='utf-8')
    colors={name:re.search(r'--'+name+r':(#[0-9a-f]{6})',css)[1] for name in ('bg','surface','surface-2','text-faint')}
    for name in ('bg','surface','surface-2'):
        high,low=sorted((luminance(colors['text-faint']),luminance(colors[name])),reverse=True)
        assert (high+.05)/(low+.05)>=4.5,name

@pytest.mark.parametrize('template',['login.html','register.html','marketing_calculator_page.html'])
def test_rendered_public_form_retains_shared_keyboard_indicator(template):
    env=Environment(loader=FileSystemLoader(TEMPLATES),autoescape=select_autoescape(['html']))
    env.globals.update(site_base_url=lambda:'https://synthetic.invalid',analytics_available=lambda:False,analytics_consent=lambda request:'unset',public_metrika_counter=lambda request:'')
    html=env.get_template(template).render(request=SimpleNamespace(url=SimpleNamespace(path='/synthetic')),csrf_token='synthetic-only',trial_days=14,price_rub=2990,period_days=30,demo_url='/demo',article_schema=False,draft_payload=None,draft_id=None)
    for selector in ('a[href]','button','input','select','textarea','summary','[tabindex]'):
        assert selector+':focus-visible' in html
    assert 'outline: 3px solid var(--focus)' in html
    assert not re.search(r'input:focus\s*\{\s*outline:\s*none',html)
    assert not re.search(r'\.calc-field input:focus[^}]*outline:\s*none',html)

def test_marketing_links_use_the_shared_focus_role():
    css=(TEMPLATES/'marketing_base.html').read_text(encoding='utf-8')
    assert 'outline:3px solid var(--focus)' in css
