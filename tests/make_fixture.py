"""Builds tests/fixtures/sample.enex: one note of each kind."""
import base64, hashlib, io
from pathlib import Path
from PIL import Image

img = Image.new("RGB", (40, 30), (200, 120, 60))
buf = io.BytesIO(); img.save(buf, format="PNG"); png = buf.getvalue()
h = hashlib.md5(png).hexdigest()

def note(title, body, tags=(), url=None, resource=False):
    tags_xml = "".join(f"<tag>{t}</tag>" for t in tags)
    attrs = f"<note-attributes><source-url>{url}</source-url></note-attributes>" if url else "<note-attributes/>"
    res = (f"<resource><data encoding=\"base64\">{base64.b64encode(png).decode()}</data><mime>image/png</mime>"
           f"<resource-attributes><file-name>card.png</file-name></resource-attributes></resource>") if resource else ""
    content = ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
               f"<en-note>{body}</en-note>")
    return (f"<note><title>{title}</title><content><![CDATA[{content}]]></content>"
            f"<created>20190314T180000Z</created>{tags_xml}{attrs}{res}</note>")

typed = """<div>Grandma's banana bread</div><div>Serves: 8</div><div>Prep time: 15 min</div>
<div><b>Ingredients</b></div><ul><li>3 ripe bananas</li><li>1/3 cup melted butter</li></ul>
<div>For the topping:</div><ul><li>2 tbsp brown sugar</li></ul>
<div>Directions</div><ol><li>1. Heat oven to 350F.</li><li>2. Mash bananas, stir in butter.</li></ol>
<div>Notes</div><div>Used walnuts last time, better.</div>"""
image = f'<div><en-media type="image/png" hash="{h}"/></div>'
clip = "<div>Best Chili Ever</div><div>Ingredients</div><div>1 lb beef</div><div>Instructions</div><div>Brown the beef.</div>"
shopping = "<div>milk</div><div>eggs</div>"

enex = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">\n'
        '<en-export export-date="20261101T000000Z" application="Evernote" version="10">'
        + note("Banana Bread", typed, tags=("Baking", "desserts"))
        + note("Aunt May's card", image, tags=("cards",), resource=True)
        + note("Best Chili Ever | Example Site", clip, url="https://example.invalid/chili")
        + note("Shopping", shopping)
        + note("Blank", "")
        + "</en-export>")
Path(__file__).parent.joinpath("fixtures", "sample.enex").write_text(enex)
