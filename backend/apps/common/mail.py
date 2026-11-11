"""HTML version of the mails the app sends to merchants.

Every mail keeps its plain-text body (what ``send_mail`` always sent, and what
the tests read); this builds the ``html_message`` that goes with it, so a mail
client that shows HTML gets the brand, a readable layout and a real button
instead of a bare link.

Written for mail clients, not browsers: tables for layout, every style inline,
system fonts, no images (a mail client blocks them by default, and the logo
would have to be fetched from a server that may not be public) and no external
CSS. The palette is the app's (``frontend/assets/css/ui.css``); if the accent
changes there, change it here.
"""

from __future__ import annotations

from django.utils.html import escape

BRAND = "Rótulos"
COMPANY = "Human Log"

ACCENT = "#308028"
INK = "#1c1a17"
MUTED = "#6b665e"
LINE = "#e7e4dd"
PAGE = "#f3f1ec"
SURFACE = "#ffffff"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def _paragraph(text, *, color=INK, size=15, bottom=14):
    return (
        f'<p style="margin:0 0 {bottom}px;font-family:{FONT};font-size:{size}px;'
        f'line-height:1.55;color:{color};">{escape(text)}</p>'
    )


def _button(label, url):
    safe_url = escape(url)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 22px;">'
        f'<tr><td bgcolor="{ACCENT}" style="border-radius:8px;">'
        f'<a href="{safe_url}" style="display:inline-block;padding:12px 22px;font-family:{FONT};'
        f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;">'
        f"{escape(label)}</a></td></tr></table>"
        # A button a client refuses to draw still leaves the link reachable.
        f'<p style="margin:0 0 18px;font-family:{FONT};font-size:12px;line-height:1.5;color:{MUTED};">'
        f'Si el botón no funciona, copiá este enlace en tu navegador:<br>'
        f'<a href="{safe_url}" style="color:{ACCENT};word-break:break-all;">{safe_url}</a></p>'
    )


def _items(items):
    rows = []
    for title, detail in items:
        detail_html = (
            f'<div style="margin-top:3px;font-size:13px;line-height:1.5;color:{MUTED};">{escape(detail)}</div>'
            if detail
            else ""
        )
        rows.append(
            f'<tr><td style="padding:12px 0;border-top:1px solid {LINE};font-family:{FONT};'
            f'font-size:15px;line-height:1.45;color:{INK};"><strong style="font-weight:600;">'
            f"{escape(title)}</strong>{detail_html}</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:4px 0 20px;border-bottom:1px solid {LINE};">{"".join(rows)}</table>'
    )


def branded_html(*, heading, paragraphs=(), action=None, items=(), footnote=""):
    """The HTML body of a mail.

    ``paragraphs`` go under the heading, then ``items`` (``(title, detail)``
    pairs, a list of things to look at), then ``action`` (``(label, url)``, the
    one button) and ``footnote`` (the quiet "if this wasn't you" line). Every
    value is escaped: store names and notice texts come from third parties.
    """
    body = "".join(_paragraph(text) for text in paragraphs)
    if items:
        body += _items(items)
    if action:
        body += _button(*action)
    if footnote:
        body += _paragraph(footnote, color=MUTED, size=13, bottom=0)
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f"<title>{escape(heading)}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE}">'
        '<tr><td align="center" style="padding:32px 16px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">'
        # Brand: the app mark as a coloured cell (no image to block) + the name.
        '<tr><td style="padding:0 4px 18px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="28" height="28" align="center" bgcolor="{ACCENT}" style="border-radius:7px;'
        f'font-family:{FONT};font-size:15px;font-weight:700;color:#ffffff;line-height:28px;">R</td>'
        f'<td style="padding-left:10px;font-family:{FONT};font-size:16px;font-weight:700;color:{INK};">'
        f'{BRAND} <span style="font-weight:400;color:{MUTED};">· {COMPANY}</span></td>'
        "</tr></table></td></tr>"
        f'<tr><td bgcolor="{SURFACE}" style="padding:32px 32px 28px;border:1px solid {LINE};border-radius:12px;">'
        f'<h1 style="margin:0 0 16px;font-family:{FONT};font-size:22px;line-height:1.25;'
        f'font-weight:700;letter-spacing:-0.01em;color:{INK};">{escape(heading)}</h1>'
        f"{body}</td></tr>"
        f'<tr><td style="padding:18px 4px 0;font-family:{FONT};font-size:12px;line-height:1.5;color:{MUTED};">'
        f"{COMPANY} · {BRAND}: los rótulos de envío de tu tienda, en un solo lugar.</td></tr>"
        "</table></td></tr></table></body></html>"
    )
