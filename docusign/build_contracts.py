#!/usr/bin/env python3
"""Generate the four role contracts that back the DocuSign templates.

One page each, deliberately different from one another: a reviewer who opens
all four should see four different jobs, not the same document with the title
swapped. The scope and compensation clauses are the parts that differ.

Each document carries three anchor strings for DocuSign to hang tabs on:

    /sig1/   signature
    /dt1/    date signed
    /nm1/    full name

They are drawn in white so they do not appear in the rendered page. DocuSign
finds them by text, not by coordinate, which is why the templates survive an
edit to this script: change the wording, regenerate, recreate the templates,
and the tabs still land next to the signature rule.

Run:  python3 docusign/build_contracts.py
Out:  docusign/contracts/{setter,coach,closer,full_cycle}.pdf
"""

import os
import textwrap

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")

INK = HexColor("#111111")
MUTED = HexColor("#555555")
RULE = HexColor("#999999")

COMPANY = "Northline Coaching Group"

ROLES = {
    "setter": {
        "title": "Appointment Setter",
        "summary": (
            "The Contractor performs outbound prospecting and books qualified "
            "discovery calls into the calendars of the Company's closing team."
        ),
        "scope": [
            "Work assigned cold and warm lists; the Company supplies the lists "
            "and the messaging framework.",
            "Book a minimum of twelve qualified discovery calls per week, where "
            "qualified is defined in Schedule A.",
            "Hand every booked call to a closer with written notes covering the "
            "prospect's stated problem, budget signal and objection.",
            "Log all activity in the Company's CRM by end of each working day.",
        ],
        "no_scope": "The Contractor does not conduct closing calls and does not "
                    "collect payment from prospects.",
        "pay": [
            ("Retainer", "USD 1,200 per month, paid on the first business day."),
            ("Per booked call", "USD 40 per call that is held and marked "
                                "qualified by the receiving closer."),
            ("Clawback", "A call that no-shows twice is not billable."),
        ],
    },
    "closer": {
        "title": "Closer",
        "summary": (
            "The Contractor conducts closing calls on opportunities booked by "
            "the Company's setting team and collects payment on the call."
        ),
        "scope": [
            "Take assigned closing calls; the Company fills the calendar.",
            "Run the Company's closing framework, including the payment "
            "conversation, on every call.",
            "Collect payment during the call using the Company's merchant "
            "account. The Contractor never holds client funds.",
            "Complete the disposition form within one hour of each call.",
        ],
        "no_scope": "The Contractor is not required to prospect and is not "
                    "assigned a self-sourcing quota.",
        "pay": [
            ("Commission", "10% of cash collected on closed opportunities, "
                           "paid weekly in arrears."),
            ("Accelerator", "12% on collected revenue above USD 60,000 in a "
                            "calendar month."),
            ("Refunds", "Commission on a refunded sale is deducted from the "
                        "next payment run."),
        ],
    },
    "coach": {
        "title": "Coach",
        "summary": (
            "The Contractor delivers the Company's coaching programme to "
            "enrolled clients after the sale is complete."
        ),
        "scope": [
            "Deliver the twelve-week onboarding programme to assigned clients.",
            "Run weekly accountability calls and respond to client messages "
            "within one business day.",
            "Escalate any client at risk of churn to the programme owner before "
            "the end of week four.",
            "Maintain session notes in the Company's client record system.",
        ],
        "no_scope": "The Contractor does not sell and is not asked to appear on "
                    "sales calls.",
        "pay": [
            ("Delivery fee", "USD 220 per client per month for the duration of "
                             "the client's programme."),
            ("Cohort sessions", "USD 300 per group session delivered."),
            ("Retention bonus", "USD 500 per cohort where week-twelve "
                                "completion exceeds 80%."),
        ],
    },
    "full_cycle": {
        "title": "Full Cycle Representative",
        "summary": (
            "The Contractor owns the opportunity end to end: sourcing, booking, "
            "closing and handover to delivery."
        ),
        "scope": [
            "Source and book the Contractor's own pipeline; the Company "
            "supplies tooling and list data on request.",
            "Run both the discovery and the closing call for owned "
            "opportunities.",
            "Collect payment on the call and complete the handover brief for "
            "the delivery team within one business day.",
            "Maintain a minimum of twenty live opportunities in the CRM.",
        ],
        "no_scope": "Opportunities sourced by the Company's setting team are "
                    "not routed to this role and carry no commission here.",
        "pay": [
            ("Retainer", "USD 800 per month, paid on the first business day."),
            ("Commission", "14% of cash collected on self-sourced closed "
                           "opportunities, paid weekly in arrears."),
            ("Refunds", "Commission on a refunded sale is deducted from the "
                        "next payment run."),
        ],
    },
}

WIDTH, HEIGHT = LETTER
LEFT = 72
RIGHT = WIDTH - 72
LINE_W = RIGHT - LEFT


def wrapped(c, text, x, y, width_chars, leading=13, font="Helvetica", size=9.5):
    c.setFont(font, size)
    for line in textwrap.wrap(text, width_chars):
        c.drawString(x, y, line)
        y -= leading
    return y


def build(slug, spec):
    path = os.path.join(OUT_DIR, f"{slug}.pdf")
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle(f"Independent Contractor Agreement - {spec['title']}")

    y = HEIGHT - 72

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    c.drawString(LEFT, y, COMPANY.upper())
    y -= 22

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(LEFT, y, "Independent Contractor Agreement")
    y -= 21
    c.setFont("Helvetica-Bold", 12)
    c.drawString(LEFT, y, spec["title"])
    y -= 16

    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(LEFT, y, RIGHT, y)
    y -= 22

    c.setFillColor(MUTED)
    y = wrapped(
        c,
        f"This Agreement is made between {COMPANY} (the \"Company\") and the "
        "individual named in the signature block below (the \"Contractor\"). "
        "It takes effect on the date the Contractor signs.",
        LEFT, y, 100,
    )
    y -= 10

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LEFT, y, "1.  Engagement")
    y -= 15
    c.setFillColor(MUTED)
    y = wrapped(c, spec["summary"], LEFT, y, 100)
    y -= 12

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LEFT, y, "2.  Scope of work")
    y -= 15
    c.setFillColor(MUTED)
    for item in spec["scope"]:
        c.setFont("Helvetica", 9.5)
        c.drawString(LEFT, y, "•")
        y = wrapped(c, item, LEFT + 12, y, 94)
        y -= 3
    y -= 4
    c.setFont("Helvetica-Oblique", 9.5)
    y = wrapped(c, spec["no_scope"], LEFT, y, 100, font="Helvetica-Oblique")
    y -= 12

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LEFT, y, "3.  Compensation")
    y -= 15
    for label, body in spec["pay"]:
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(LEFT, y, label)
        c.setFillColor(MUTED)
        y = wrapped(c, body, LEFT + 92, y, 76)
        y -= 3
    y -= 10

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LEFT, y, "4.  Term, status and termination")
    y -= 15
    c.setFillColor(MUTED)
    y = wrapped(
        c,
        "The Contractor is an independent contractor and not an employee. "
        "Either party may terminate this Agreement on fourteen days' written "
        "notice. Work completed before the termination date remains payable. "
        "Client lists, recordings and messaging frameworks remain the property "
        "of the Company.",
        LEFT, y, 100,
    )

    # ---- signature block -------------------------------------------------
    base = 150
    c.setStrokeColor(RULE)
    c.line(LEFT, base + 46, RIGHT, base + 46)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LEFT, base + 26, "Accepted and agreed")

    c.setStrokeColor(HexColor("#333333"))
    c.setLineWidth(0.8)
    c.line(LEFT, base - 20, LEFT + 230, base - 20)
    c.line(LEFT + 270, base - 20, LEFT + 400, base - 20)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(LEFT, base - 32, "Contractor signature")
    c.drawString(LEFT + 270, base - 32, "Date signed")

    c.setFont("Helvetica", 8)
    c.drawString(LEFT, base - 56, "Printed name")
    c.setStrokeColor(HexColor("#333333"))
    c.line(LEFT + 66, base - 58, LEFT + 300, base - 58)

    # Anchors, drawn in white. DocuSign locates tabs by this text.
    c.setFillColor(white)
    c.setFont("Helvetica", 7)
    c.drawString(LEFT + 4, base - 12, "/sig1/")
    c.drawString(LEFT + 274, base - 12, "/dt1/")
    c.drawString(LEFT + 70, base - 52, "/nm1/")

    c.setFillColor(HexColor("#888888"))
    c.setFont("Helvetica", 7.5)
    c.drawString(LEFT, 60, f"{COMPANY} — {spec['title']} — page 1 of 1")

    c.showPage()
    c.save()
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for slug, spec in ROLES.items():
        path = build(slug, spec)
        print(f"{os.path.getsize(path):>7,} bytes  {path}")


if __name__ == "__main__":
    main()
